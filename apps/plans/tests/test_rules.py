"""Motor de reglas de la fase B (Python puro): elegibilidad, filtro del catálogo y validación de
la salida de la IA."""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from apps.plans.rules import catalog, eligibility, flags, validate

TODAY = date(2026, 10, 12)


def facts(**overrides):
    base = dict(age=30, sex='F', goal='toning', weight_kg=Decimal('60'), height_m=Decimal('1.65'),
                measurement_days_old=0, sleep_hours=Decimal('7'), today=TODAY)
    base.update(overrides)
    return flags.Facts(**base)


def evaluate(kind='exercise', **overrides):
    return eligibility.evaluate(flags.evaluate(facts(**overrides)), kind)


class EligibilityTests(SimpleTestCase):
    def test_complete_profile_can_request(self):
        result = evaluate()
        self.assertTrue(result.can_request)
        self.assertFalse(result.blocked)
        self.assertEqual(result.missing, ())

    def test_missing_requirements_in_screen_order(self):
        result = eligibility.evaluate(
            flags.evaluate(facts(has_profile=False, measurement_days_old=None, ai_consent=False)),
            'exercise', available=False, health_consent=False, quota_ok=False)
        self.assertEqual(result.missing, (
            eligibility.D00_NOT_AVAILABLE, eligibility.D01_CONSENT_HEALTH, eligibility.D01_CONSENT_AI,
            eligibility.D02_PROFILE, eligibility.D03_MEASURE, eligibility.D05_QUOTA))
        self.assertFalse(result.can_request)
        self.assertFalse(result.blocked)   # con datos faltantes no se habla de bloqueo

    def test_unconfirmed_profile_and_old_measure(self):
        result = evaluate(profile_confirmed=False, measurement_days_old=31)
        self.assertIn(eligibility.D02_PROFILE_CONFIRM, result.missing)
        self.assertIn(eligibility.D03_MEASURE, result.missing)

    def test_incoherent_data(self):
        self.assertIn(eligibility.D04_INCOHERENT, evaluate(weight_kg=Decimal('300')).missing)

    def test_red_flag_blocks_its_scope(self):
        result = evaluate(pregnancy_status='pregnant')
        self.assertTrue(result.blocked)
        self.assertIn('R02_PREGNANCY', result.red_codes)
        self.assertFalse(result.can_request)

    def test_nutrition_only_red_leaves_exercise_allowed_with_warning(self):
        result = evaluate(eating_disorder_history='yes')
        self.assertTrue(result.can_request)
        self.assertIn('Y10_ED_EXERCISE', result.yellow_codes)
        self.assertTrue(result.conservative)

    def test_companion_red_becomes_exercise_warning(self):
        # IMC >= 40: rojo en alimentación, amarillo en ejercicio
        result = evaluate(weight_kg=Decimal('115'))
        self.assertTrue(result.can_request)
        self.assertIn('R08_BMI_VERY_HIGH', result.yellow_codes)

    def test_messages_never_include_diagnoses(self):
        self.assertNotIn('embarazo', eligibility.BLOCKED_MESSAGE.lower())
        for text in eligibility.REQUIREMENT_TEXTS.values():
            self.assertNotRegex(text.lower(), r'diabetes|embarazo|cardi|renal|cáncer')


def item(pk, **kwargs):
    base = dict(id=pk, name=f'E{pk}', level='beginner', intensity='moderate', equipment=(), risk_tags=())
    base.update(kwargs)
    return catalog.ExerciseItem(**base)


class CatalogTests(SimpleTestCase):
    def ids(self, items, **profile):
        return sorted(catalog.filter_exercises(items, catalog.TrainingProfile(**profile)).allowed_ids)

    def test_level_is_capped_by_experience(self):
        items = [item(1), item(2, level='intermediate'), item(3, level='advanced')]
        self.assertEqual(self.ids(items, experience_level='none'), [1])
        self.assertEqual(self.ids(items, experience_level='intermediate'), [1, 2])
        self.assertEqual(self.ids(items, experience_level='advanced'), [1, 2, 3])

    def test_home_requires_available_equipment_and_tags(self):
        items = [item(1), item(2, equipment=('dumbbells',)), item(3, equipment=('barbell',)),
                 item(4, intensity='')]
        self.assertEqual(self.ids(items, training_place='home', equipment=('dumbbells',)), [1, 2])
        self.assertEqual(self.ids(items, training_place='home', equipment=('none',)), [1])
        self.assertEqual(self.ids(items, training_place='gym'), [1, 2, 3, 4])

    def test_untagged_excluded_with_injuries_or_warnings(self):
        items = [item(1), item(2, intensity='')]
        self.assertEqual(self.ids(items, injuries=('knee',)), [1])
        self.assertEqual(self.ids(items, yellow_codes=('Y12_OTHER_TEXT',)), [1])
        self.assertEqual(self.ids(items, conditions=('asthma',)), [1])

    def test_injury_zone_and_high_impact(self):
        items = [item(1, risk_tags=('knee',)), item(2, risk_tags=('high_impact',)), item(3)]
        self.assertEqual(self.ids(items, injuries=('knee',)), [2, 3])
        self.assertEqual(self.ids(items, yellow_codes=('Y09_BMI_HIGH',)), [1, 3])
        self.assertEqual(self.ids(items, yellow_codes=('R08_BMI_VERY_HIGH',)), [1, 3])

    def test_hypertension_limits(self):
        items = [item(1, risk_tags=('breath_hold',)), item(2)]
        result = catalog.filter_exercises(items, catalog.TrainingProfile(yellow_codes=('Y01_HYPERTENSION',)))
        self.assertEqual(sorted(result.allowed_ids), [2])
        self.assertEqual(result.limits.max_sets, 3)

    def test_intensity_caps(self):
        items = [item(1, intensity='low'), item(2, intensity='moderate'), item(3, intensity='high')]
        self.assertEqual(self.ids(items, yellow_codes=('Y03_THYROID_ASTHMA',)), [1, 2])
        self.assertEqual(self.ids(items, yellow_codes=('Y11_REHAB_GOAL',)), [1])

    def test_spine_conditions(self):
        items = [item(1, risk_tags=('lower_back',)), item(2, risk_tags=('heavy_axial',)), item(3)]
        self.assertEqual(self.ids(items, conditions=('herniated_disc',)), [3])
        self.assertEqual(self.ids(items, conditions=('osteoporosis',)), [1, 3])

    def test_enough_threshold(self):
        few = catalog.filter_exercises([item(n) for n in range(5)], catalog.TrainingProfile())
        enough = catalog.filter_exercises([item(n) for n in range(6)], catalog.TrainingProfile())
        self.assertFalse(few.enough)
        self.assertTrue(enough.enough)


def plan(days=2, per_day=3, **top):
    data = {
        'schema_version': 'exercise-plan/1', 'routine_name': 'Plan de tonificación',
        'general_notes': 'Calienta 5 minutos.',
        'days': [{'name': f'Día {d + 1}', 'exercises': [
            {'exercise_id': d * 10 + e + 1, 'sets': 3, 'reps': '10-12', 'rest_seconds': 60,
             'observations': 'Controla la bajada.'} for e in range(per_day)]} for d in range(days)],
    }
    data.update(top)
    return data


RULES = validate.ExerciseRules(allowed_ids=frozenset(range(1, 40)), days=2, session_minutes=60, max_sets=4)


class ValidateTests(SimpleTestCase):
    def check(self, data, rules=RULES):
        return validate.validate_exercise_plan(data, rules)

    def test_valid_plan(self):
        result = self.check(plan())
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(len(result.data['days']), 2)

    def test_schema(self):
        self.assertEqual(self.check(plan(extra='x')).codes, ('V01_SCHEMA',))
        self.assertIn('V01_SCHEMA', self.check(plan(schema_version='exercise-plan/2')).codes)
        self.assertEqual(self.check(['no', 'objeto']).codes, ('V01_SCHEMA',))
        data = plan()
        data['days'][0]['exercises'][0]['sets'] = True   # bool no es entero
        self.assertIn('V01_SCHEMA', self.check(data).codes)

    def test_unknown_id(self):
        data = plan()
        data['days'][0]['exercises'][0]['exercise_id'] = 999
        self.assertEqual(self.check(data).codes, ('V02_IDS',))

    def test_exercise_rules(self):
        self.assertIn('V07_EXERCISE', self.check(plan(days=3)).codes)       # días distintos a los pedidos
        self.assertIn('V07_EXERCISE', self.check(plan(per_day=2)).codes)    # menos de 3 por día
        short = validate.ExerciseRules(allowed_ids=RULES.allowed_ids, days=2, session_minutes=20, max_sets=4)
        self.assertIn('V07_EXERCISE', self.check(plan(per_day=4), short).codes)   # 20 min = máx. 3
        for field, value in (('sets', 5), ('reps', 'muchas'), ('rest_seconds', 10), ('rest_seconds', 200)):
            data = plan()
            data['days'][0]['exercises'][0][field] = value
            self.assertIn('V07_EXERCISE', self.check(data).codes, field)
        data = plan()
        data['days'][0]['exercises'][1]['exercise_id'] = data['days'][0]['exercises'][0]['exercise_id']
        self.assertIn('V07_EXERCISE', self.check(data).codes)

    def test_reps_formats(self):
        for reps in ('12', '10-12', '30 seg', '45seg'):
            data = plan()
            data['days'][0]['exercises'][0]['reps'] = reps
            self.assertTrue(self.check(data).ok, reps)

    def test_text_rules(self):
        bad = ['<b>Hola</b>', 'Mira https://ejemplo.com', 'escribe a coach@correo.com',
               'Llama al 300 555 1234', 'Toma SUPLEMENTACIÓN después', 'Haz ayuno intermitente',
               'Este plan cura la diabetes', 'Visita profit.co']
        for text in bad:
            self.assertIn('V08_TEXT', self.check(plan(general_notes=text)).codes, text)
        self.assertIn('V08_TEXT', self.check(plan(general_notes='x' * 601)).codes)
        self.assertIn('V08_TEXT', self.check(plan(routine_name='   ')).codes)

    def test_text_is_cleaned_and_safe_numbers_pass(self):
        result = self.check(plan(general_notes='  3 series   de 10, 12 y 15​ repeticiones '))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.data['general_notes'], '3 series de 10, 12 y 15 repeticiones')

    def test_error_code(self):
        data = plan()
        data['days'][0]['exercises'][0]['exercise_id'] = 999
        self.assertEqual(self.check(data).error_code, 'validation_failed:V02_IDS')
