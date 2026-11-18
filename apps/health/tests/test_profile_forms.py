"""Validación de la ficha de salud (formulario): listas cerradas, rangos, coherencias, textos.

Incluye la ficha básica (docs/contracts/ficha-basica.md, secciones 4 y 8): las dos preguntas que
despliegan, el descarte de lo médico con «No», «Sí» sin nada marcado y `defaulted_fields`.
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.http import QueryDict
from django.test import SimpleTestCase

from apps.health import choices as ch
from apps.health import dates, screening
from apps.health.forms import (
    FOOD_FORM_FIELDS, HEALTH_FORM_FIELDS, SECTION_FIELDS, HealthProfileForm, empty_form_values,
    error_steps, first_error_step, form_values_from_post, form_values_from_profile,
)

from .helpers import adult_birth_date, basic_post, valid_post


def run(known_height=None, **overrides):
    form = HealthProfileForm(valid_post(**overrides), known_height=known_height)
    form.is_valid()
    return form


class ValidProfileTests(SimpleTestCase):
    def test_valid_post_is_accepted_and_normalized(self):
        form = run()
        self.assertTrue(form.is_valid(), form.errors)
        data = form.cleaned_data
        self.assertEqual(data['target_weight_kg'], Decimal('58.5'))          # coma decimal
        self.assertEqual(data['sleep_hours'], Decimal('7.5'))
        self.assertEqual(data['meal_slots'], ['breakfast', 'lunch', 'dinner'])
        self.assertEqual(data['meal_times'], {'breakfast': '07:00', 'lunch': '12:30', 'dinner': '19:00'})
        self.assertEqual(data['parq'], {f'q{i}': False for i in range(1, 8)})
        self.assertNotIn('parq_q1', data)                                    # no quedan campos intermedios
        self.assertNotIn('meal_time_breakfast', data)
        self.assertEqual(data['eats_out_per_week'], 2)

    def test_optional_fields_can_be_empty(self):
        form = run(target_weight_kg='', sleep_hours='', eats_out_per_week='', meal_time_breakfast='',
                   meal_time_lunch='', meal_time_dinner='')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['target_weight_kg'])
        self.assertIsNone(form.cleaned_data['sleep_hours'])
        self.assertEqual(form.cleaned_data['eats_out_per_week'], 0)
        self.assertEqual(form.cleaned_data['meal_times'], {})

    def test_accepts_querydict_with_repeated_values(self):
        post = QueryDict(mutable=True)
        for key, value in valid_post(conditions=['asthma', 'hypertension'],
                                     condition_controlled='on').items():
            post.setlist(key, value if isinstance(value, list) else [value])
        form = HealthProfileForm(post)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['conditions'], ['hypertension', 'asthma'])   # orden canónico
        self.assertTrue(form.cleaned_data['condition_controlled'])

    def test_birth_date_accepts_day_month_year(self):
        born = adult_birth_date(40)
        form = run(birth_date=born.strftime('%d/%m/%Y'))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['birth_date'], born)


class RequiredAndClosedListTests(SimpleTestCase):
    # Siempre obligatorios (pasos 1 a 4)
    REQUIRED = ['birth_date', 'sex_for_calculation', 'training_goal', 'health_screen', 'food_screen',
                'training_days_per_week', 'session_minutes', 'training_place', 'experience_level']
    # Obligatorios solo con «Sí» en salud / en alimentación
    REQUIRED_WITH_HEALTH_YES = (['recent_surgery', 'pregnancy_status', 'eating_disorder_history']
                                + [f'parq_q{i}' for i in range(1, 8)])
    REQUIRED_WITH_FOOD_YES = ['diet_type']
    # Paso 5 «Más detalles»: opcionales (toman un valor por defecto)
    OPTIONAL_DETAILS = ['cooking_access', 'budget_level', 'activity_level', 'eats_out_per_week',
                        'sleep_hours', 'target_weight_kg']

    def test_each_required_field_is_enforced(self):
        for name in self.REQUIRED:
            form = run(**{name: None})
            self.assertIn(name, form.errors, name)
        for name in self.REQUIRED_WITH_HEALTH_YES:
            form = run(health_screen='yes', injuries=['knee'], **{name: None})
            self.assertIn(name, form.errors, name)
        for name in self.REQUIRED_WITH_FOOD_YES:
            form = run(food_screen='yes', allergies=['peanut'], allergy_severity='mild', **{name: None})
            self.assertIn(name, form.errors, name)

    def test_details_are_optional(self):
        for name in self.OPTIONAL_DETAILS:
            form = run(**{name: None})
            self.assertTrue(form.is_valid(), (name, form.errors))

    def test_unknown_codes_are_rejected(self):
        for name, value in [('sex_for_calculation', 'X'), ('training_goal', 'volar'),
                            ('recent_surgery', 'ayer'), ('pregnancy_status', 'quizas'),
                            ('eating_disorder_history', 'tal_vez'), ('diet_type', 'carnivora'),
                            ('cooking_access', 'mucho'), ('budget_level', 'infinito'),
                            ('training_place', 'luna'), ('experience_level', 'dios'),
                            ('activity_level', 'extrema'), ('parq_q3', 'tal vez'),
                            ('health_screen', 'quizas'), ('health_screen', 'legacy'),
                            ('food_screen', 'tal_vez'), ('food_screen', 'legacy')]:
            self.assertIn(name, run(**{name: value}).errors, name)

    def test_unknown_codes_in_lists_are_rejected(self):
        for name in ('conditions', 'medications', 'injuries', 'allergies', 'intolerances', 'meal_slots'):
            slots = ['breakfast', 'lunch', 'dinner']
            value = slots + ['inventado'] if name == 'meal_slots' else ['inventado']
            self.assertIn(name, run(**{name: value}).errors, name)
        form = run(training_place='home', equipment=['cohete'])
        self.assertIn('equipment', form.errors)

    def test_numeric_ranges(self):
        for name, bad in [('training_days_per_week', ['0', '7', 'x', '2,5']),
                          ('session_minutes', ['19', '121', 'x']),
                          ('eats_out_per_week', ['-1', '22', 'x']),
                          ('sleep_hours', ['2,9', '12,1', 'x', 'NaN', 'Infinity']),
                          ('target_weight_kg', ['29', '251', 'x', '1e500'])]:
            for value in bad:
                self.assertIn(name, run(**{name: value}).errors, f'{name}={value}')
        for name, good in [('training_days_per_week', ['1', '6']), ('session_minutes', ['20', '120']),
                           ('eats_out_per_week', ['0', '21']), ('sleep_hours', ['3', '12']),
                           ('target_weight_kg', ['30', '250'])]:
            for value in good:
                self.assertNotIn(name, run(**{name: value}).errors, f'{name}={value}')

    def test_every_field_belongs_to_a_wizard_step(self):
        form = HealthProfileForm()
        belonging = {name for names in SECTION_FIELDS.values() for name in names}
        self.assertEqual(set(form.fields), belonging)


class BirthDateTests(SimpleTestCase):
    def test_age_borders_with_a_fixed_today(self):
        fixed = date(2026, 10, 8)
        with mock.patch.object(dates, 'today', return_value=fixed):
            self.assertIn('birth_date', run(birth_date='2008-10-09').errors)        # 17 años y 364 días
            self.assertNotIn('birth_date', run(birth_date='2008-10-08').errors)     # cumple 18 hoy
            self.assertNotIn('birth_date', run(birth_date='1926-10-09').errors)     # 99 años
            self.assertNotIn('birth_date', run(birth_date='1926-10-08').errors)     # 100 años cumplidos
            self.assertIn('birth_date', run(birth_date='1925-10-07').errors)        # 101 años
            self.assertIn('birth_date', run(birth_date='2026-10-09').errors)        # futura

    def test_minor_message_points_to_the_trainer(self):
        form = run(birth_date=(dates.today() - timedelta(days=365 * 15)).isoformat())
        self.assertIn('entrenador', form.errors['birth_date'][0])

    def test_garbage_dates(self):
        for value in ('', 'ayer', '31/02/1990', '0000-00-00', '1990-13-01'):
            self.assertIn('birth_date', run(birth_date=value).errors, value)


class CoherenceTests(SimpleTestCase):
    def test_pregnancy_with_male_sex_is_rejected(self):
        for status in ('pregnant', 'lactating', 'postpartum_under_6m'):
            self.assertIn('pregnancy_status', run(sex_for_calculation='M', pregnancy_status=status).errors)
        self.assertNotIn('pregnancy_status', run(sex_for_calculation='NA', pregnancy_status='pregnant').errors)
        self.assertNotIn('pregnancy_status', run(sex_for_calculation='M', pregnancy_status='none').errors)

    def test_clearance_requires_a_date_and_it_cannot_be_future(self):
        self.assertIn('clearance_date', run(medical_clearance='on', clearance_date='').errors)
        future = (dates.today() + timedelta(days=1)).isoformat()
        self.assertIn('clearance_date', run(medical_clearance='on', clearance_date=future).errors)
        self.assertIn('clearance_date', run(medical_clearance='on', clearance_date='1980-01-01').errors)
        ok = run(medical_clearance='on', clearance_date=(dates.today() - timedelta(days=30)).isoformat())
        self.assertTrue(ok.is_valid(), ok.errors)
        self.assertEqual(ok.cleaned_data['clearance_date'], dates.today() - timedelta(days=30))

    def test_clearance_date_is_dropped_without_clearance(self):
        form = run(medical_clearance='', clearance_date='2025-01-01', injuries=['knee'])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['clearance_date'])
        self.assertFalse(form.cleaned_data['medical_clearance'])

    def test_condition_controlled_is_cleared_without_conditions(self):
        form = run(conditions=[], condition_controlled='on', injuries=['knee'])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data['condition_controlled'])
        kept = run(conditions=['asthma'], condition_controlled='on')
        self.assertTrue(kept.cleaned_data['condition_controlled'])

    def test_allergy_severity_only_with_allergies(self):
        self.assertIn('allergy_severity', run(allergies=['peanut'], allergy_severity='').errors)
        self.assertNotIn('allergy_severity', run(allergies=['peanut'], allergy_severity='mild').errors)
        form = run(allergies=[], allergy_severity='anaphylaxis', diet_type='vegan')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['allergy_severity'], '')

    def test_equipment_rules(self):
        # Ficha básica (6A): en casa y sin responder = «Ninguno», marcado como no declarado
        home = run(training_place='home', equipment=[])
        self.assertTrue(home.is_valid(), home.errors)
        self.assertEqual(home.cleaned_data['equipment'], ['none'])
        self.assertIn('equipment', home.cleaned_data['defaulted_fields'])
        self.assertIn('equipment', run(training_place='home', equipment=['none', 'bands']).errors)
        self.assertTrue(run(training_place='home', equipment=['none']).is_valid())
        form = run(training_place='home', equipment=['mat', 'dumbbells', 'mat'])
        self.assertEqual(form.cleaned_data['equipment'], ['dumbbells', 'mat'])
        # En el gimnasio el equipo no aplica
        gym = run(training_place='gym', equipment=['bands'])
        self.assertTrue(gym.is_valid(), gym.errors)
        self.assertEqual(gym.cleaned_data['equipment'], [])

    def test_target_weight_must_fit_known_height(self):
        form = run(known_height=Decimal('1.65'), target_weight_kg='250')       # IMC 91: aún creíble
        self.assertNotIn('target_weight_kg', form.errors)
        form = run(known_height=Decimal('2.5'), target_weight_kg='30')         # IMC 4,8: incoherente
        self.assertIn('target_weight_kg', form.errors)
        self.assertNotIn('target_weight_kg', run(known_height=None, target_weight_kg='30').errors)


class MealSlotTests(SimpleTestCase):
    def test_breakfast_lunch_and_dinner_are_always_required(self):
        for slots in (['breakfast', 'lunch'], ['lunch', 'dinner'], ['breakfast', 'dinner'],
                      ['breakfast', 'snack', 'dinner'], ['mid_morning', 'snack', 'lunch']):
            self.assertIn('meal_slots', run(meal_slots=slots).errors, slots)
        # Sin ninguna comida (no llegaron los campos ocultos): las tres obligatorias, sin marcarlo
        empty = run(meal_slots=[])
        self.assertTrue(empty.is_valid(), empty.errors)
        self.assertEqual(empty.cleaned_data['meal_slots'], ['breakfast', 'lunch', 'dinner'])
        self.assertNotIn('meal_slots', empty.cleaned_data['defaulted_fields'])

    def test_optional_slots_and_canonical_order(self):
        for extra in (['snack'], ['mid_morning'], ['mid_morning', 'snack']):
            form = run(meal_slots=['dinner', 'lunch', 'breakfast', *extra])
            self.assertTrue(form.is_valid(), form.errors)
            slots = form.cleaned_data['meal_slots']
            self.assertEqual(slots, [c for c in ['breakfast', 'mid_morning', 'lunch', 'snack', 'dinner']
                                     if c in slots])
            self.assertTrue(3 <= len(slots) <= 5)

    def test_duplicates_are_collapsed(self):
        form = run(meal_slots=['breakfast', 'breakfast', 'lunch', 'dinner', 'dinner'])
        self.assertEqual(form.cleaned_data['meal_slots'], ['breakfast', 'lunch', 'dinner'])

    def test_meal_times(self):
        for bad in ('7:5', '25:00', '12:60', 'mediodia', '07:00:00', '12h30'):
            self.assertIn('meal_time_breakfast', run(meal_time_breakfast=bad).errors, bad)
        form = run(meal_time_breakfast='7:05')
        self.assertEqual(form.cleaned_data['meal_times']['breakfast'], '07:05')

    def test_times_of_unselected_meals_are_discarded(self):
        form = run(meal_time_snack='16:00', meal_time_mid_morning='10:00')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(set(form.cleaned_data['meal_times']), {'breakfast', 'lunch', 'dinner'})
        form = run(meal_slots=['breakfast', 'lunch', 'dinner', 'snack'], meal_time_snack='16:00')
        self.assertEqual(form.cleaned_data['meal_times']['snack'], '16:00')


class FreeTextTests(SimpleTestCase):
    def test_text_is_cleaned(self):
        form = run(other_condition_text='  Rinitis \n\n alérgica\t\u200b crónica  ',
                   liked_foods_text='Café')       # e + acento combinante -> é (NFC)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['other_condition_text'], 'Rinitis alérgica crónica')
        self.assertEqual(form.cleaned_data['liked_foods_text'], 'Café')

    def test_control_and_format_characters_are_removed(self):
        form = run(injuries_text='ro\x07dil\x1bla\u202eX')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['injuries_text'], 'rodillaX')

    def test_null_characters_are_dropped(self):
        form = run(injuries_text='a\x00b')
        self.assertEqual(form.cleaned_data['injuries_text'], 'ab')

    def test_html_is_rejected(self):
        for value in ('<script>alert(1)</script>', 'a < b', 'x>y', '<b>negrita</b>'):
            self.assertIn('medications_text', run(medications_text=value).errors, value)

    def test_length_limit_is_measured_after_cleaning(self):
        self.assertTrue(run(liked_foods_text='a' * 300).is_valid())
        self.assertIn('liked_foods_text', run(liked_foods_text='a' * 301).errors)
        # Los espacios sobrantes se colapsan antes de contar: 'a' + 400 espacios + 'b' queda en 'a b'
        self.assertTrue(run(liked_foods_text='a' + ' ' * 400 + 'b').is_valid())
        self.assertIn('liked_foods_text', run(liked_foods_text='a' * 5000).errors)

    def test_empty_text_is_empty_string(self):
        self.assertEqual(run(disliked_foods_text='   ').cleaned_data['disliked_foods_text'], '')


class StepsAndValuesTests(SimpleTestCase):
    def test_error_steps(self):
        self.assertEqual(error_steps({}), [])
        self.assertIsNone(first_error_step({}))
        errors = {'training_days_per_week': 'x', 'birth_date': 'x', 'parq_q4': 'x', 'meal_slots': 'x'}
        self.assertEqual(error_steps(errors), [1, 2, 4, 5])
        self.assertEqual(first_error_step(errors), 1)
        self.assertEqual(error_steps({'meal_time_lunch': 'x'}), [5])
        self.assertEqual(error_steps({'health_screen': 'x'}), [2])
        self.assertEqual(error_steps({'food_screen': 'x', 'diet_type': 'x'}), [3])
        self.assertEqual(error_steps({'target_weight_kg': 'x', 'activity_level': 'x'}), [5])
        self.assertEqual(error_steps({'general': 'x'}), [1])

    def test_form_errors_map_to_steps(self):
        form = run(health_screen='yes', injuries=['knee'], parq_q2=None, session_minutes='5',
                   meal_slots=['lunch'])
        self.assertEqual(error_steps({name: 'x' for name in form.errors}), [2, 4, 5])

    def test_values_from_post_have_stable_shape(self):
        post = QueryDict(mutable=True)
        post.setlist('injuries', ['knee', 'hip'])
        post.update({'condition_controlled': 'on', 'birth_date': '1990-01-01', 'parq_q1': 'yes'})
        values = form_values_from_post(post)
        self.assertEqual(values['injuries'], ['knee', 'hip'])                # lista completa, no la última
        self.assertEqual(values['conditions'], [])                          # siempre lista
        self.assertIs(values['condition_controlled'], True)
        self.assertIs(values['medical_clearance'], False)
        self.assertEqual(values['parq_q1'], 'yes')
        self.assertEqual(values['parq_q2'], '')
        self.assertEqual(values['meal_time_lunch'], '')
        self.assertEqual(values['sleep_hours'], '')

    def test_empty_values_default_to_required_meals(self):
        values = empty_form_values()
        self.assertEqual(values['meal_slots'], ['breakfast', 'mid_morning', 'lunch', 'snack', 'dinner'])
        self.assertEqual(values['conditions'], [])
        self.assertEqual(set(values), set(form_values_from_post({})))

    def test_values_from_profile_roundtrip(self):
        post = valid_post(conditions=['asthma'], condition_controlled='on', injuries=['knee'],
                          parq_q5='yes', meal_slots=['breakfast', 'snack', 'lunch', 'dinner'],
                          meal_time_snack='16:00', medical_clearance='on', clearance_date='2026-01-15')
        form = HealthProfileForm(post)
        self.assertTrue(form.is_valid(), form.errors)

        class Fake:    # misma interfaz que HealthProfile, sin base de datos
            pass
        fake = Fake()
        for name, value in form.cleaned_data.items():
            setattr(fake, name, value)
        values = form_values_from_profile(fake, 'toning')
        self.assertEqual(values['conditions'], ['asthma'])
        self.assertEqual(values['parq_q5'], 'yes')
        self.assertEqual(values['parq_q1'], 'no')
        self.assertEqual(values['meal_time_snack'], '16:00')
        self.assertEqual(values['clearance_date'], '2026-01-15')
        self.assertEqual(values['training_goal'], 'toning')
        self.assertIs(values['medical_clearance'], True)
        # El formulario acepta de vuelta lo que publicó
        again = HealthProfileForm({k: v for k, v in values.items() if v not in ('', False)})
        self.assertTrue(again.is_valid(), again.errors)


# ─── Ficha básica (contrato ficha-basica.md, sección 8: formulario) ───────────

def basic(**overrides):
    form = HealthProfileForm(basic_post(**overrides))
    form.is_valid()
    return form


def as_post(values):
    """Lo que el navegador volvería a enviar con estos valores (sin vacíos ni casillas apagadas)."""
    return {key: value for key, value in values.items()
            if key != 'defaulted_fields' and value not in ('', False, [])}


# Datos médicos "basura" (códigos inventados, respuestas raras, HTML) que llegan en campos ocultos
MEDICAL_GARBAGE = dict(
    conditions=['inventado', 'asthma'], medications=['pocion'], injuries=['knee', 'ala'],
    condition_controlled='on', medical_clearance='on', clearance_date='no-es-fecha',
    recent_surgery='ayer', pregnancy_status='pregnant', eating_disorder_history='tal_vez',
    other_condition_text='<script>alert(1)</script>', medications_text='<b>x</b>',
    injuries_text='a' * 5000, parq_q1='yes', parq_q2='quizas', parq_q3='yes',
)
FOOD_GARBAGE = dict(diet_type='carnivora', allergies=['peanut', 'kriptonita'], allergy_severity='mucha',
                    intolerances=['todo'])


class BasicProfileTests(SimpleTestCase):
    def test_minimal_profile_is_valid_with_exact_neutral_and_default_values(self):
        form = basic()
        self.assertTrue(form.is_valid(), form.errors)
        data = form.cleaned_data
        self.assertEqual((data['health_screen'], data['food_screen']), ('no', 'no'))
        # 4.1: valores neutros explícitos
        self.assertEqual(data['parq'], {f'q{i}': False for i in range(1, 8)})
        for name in ('conditions', 'medications', 'injuries'):
            self.assertEqual(data[name], [], name)
        self.assertIs(data['condition_controlled'], False)
        self.assertIs(data['medical_clearance'], False)
        self.assertIsNone(data['clearance_date'])
        self.assertEqual((data['recent_surgery'], data['pregnancy_status'], data['eating_disorder_history']),
                         ('none', 'none', 'no'))
        for name in ('other_condition_text', 'medications_text', 'injuries_text'):
            self.assertEqual(data[name], '', name)
        # 4.2
        self.assertEqual((data['diet_type'], data['allergies'], data['allergy_severity'], data['intolerances']),
                         ('omnivore', [], '', []))
        # 4.3: valores por defecto y «no declarados»
        self.assertIsNone(data['target_weight_kg'])
        self.assertEqual(data['meal_slots'], ['breakfast', 'lunch', 'dinner'])
        self.assertEqual(data['meal_times'], {})
        self.assertEqual((data['cooking_access'], data['budget_level'], data['eats_out_per_week']), ('basic', 'medium', 0))
        self.assertEqual((data['liked_foods_text'], data['disliked_foods_text']), ('', ''))
        self.assertEqual(data['equipment'], [])                                  # gimnasio
        self.assertEqual(data['activity_level'], 'sedentary')
        self.assertIsNone(data['sleep_hours'])
        self.assertEqual(data['defaulted_fields'],
                         ['cooking_access', 'budget_level', 'eats_out_per_week', 'activity_level'])
        self.assertFalse(any(name.startswith('parq_') for name in data))

    def test_minimal_profile_at_home_trains_without_equipment(self):
        form = basic(training_place='home')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['equipment'], ['none'])
        self.assertIn('equipment', form.cleaned_data['defaulted_fields'])

    def test_minimal_profile_does_not_need_the_meal_fields(self):
        form = basic(meal_slots=None)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['meal_slots'], ['breakfast', 'lunch', 'dinner'])


class HealthScreenTests(SimpleTestCase):
    def test_no_discards_medical_garbage_without_errors(self):
        form = basic(**MEDICAL_GARBAGE)
        self.assertTrue(form.is_valid(), form.errors)
        data = form.cleaned_data
        for name, value in screening.health_neutral_values().items():
            self.assertEqual(data[name], value, name)
        dump = repr(data)
        for secret in ('script', 'inventado', 'pocion', 'quizas', 'pregnant', 'aaaa'):
            self.assertNotIn(secret, dump, secret)
        for name in HEALTH_FORM_FIELDS:
            self.assertNotIn(name, form.errors, name)

    def test_counterproof_yes_validates_the_same_garbage(self):
        form = basic(health_screen='yes', **MEDICAL_GARBAGE)
        self.assertFalse(form.is_valid())
        for name in ('conditions', 'medications', 'injuries', 'clearance_date', 'recent_surgery',
                     'eating_disorder_history', 'other_condition_text', 'medications_text', 'injuries_text',
                     'parq_q2', 'parq_q4'):
            self.assertIn(name, form.errors, name)
        self.assertEqual(error_steps(form.errors), [2])

    def test_yes_requires_the_seven_questions_on_step_2(self):
        form = basic(health_screen='yes', injuries=['knee'], recent_surgery='none', pregnancy_status='none',
                     eating_disorder_history='no')
        self.assertEqual(set(form.errors), {f'parq_q{i}' for i in range(1, 8)})
        self.assertEqual(error_steps(form.errors), [2])

    def test_yes_with_nothing_marked_is_an_error_on_the_question(self):
        neutral_yes = dict(health_screen='yes', recent_surgery='none', pregnancy_status='none',
                           eating_disorder_history='no', **{f'parq_q{i}': 'no' for i in range(1, 8)})
        form = basic(**neutral_yes)
        self.assertEqual(list(form.errors), ['health_screen'])
        self.assertEqual(form.errors['health_screen'], [screening.HEALTH_YES_EMPTY])
        self.assertEqual(error_steps(form.errors), [2])
        # Una casilla de «condiciones controladas» sin condiciones no cuenta como dato
        self.assertIn('health_screen', basic(condition_controlled='on', **neutral_yes).errors)

    def test_yes_with_a_single_item_is_valid(self):
        neutral_yes = dict(health_screen='yes', recent_surgery='none', pregnancy_status='none',
                           eating_disorder_history='no', **{f'parq_q{i}': 'no' for i in range(1, 8)})
        today = dates.today()
        for single in (dict(parq_q3='yes'), dict(conditions=['asthma']), dict(medications=['insulin']),
                       dict(injuries=['knee']), dict(recent_surgery='over_12m'),
                       dict(pregnancy_status='lactating'), dict(eating_disorder_history='prefer_not_say'),
                       dict(other_condition_text='Rinitis'), dict(medications_text='Vitamina D'),
                       dict(injuries_text='Esguince viejo'),
                       dict(medical_clearance='on', clearance_date=(today - timedelta(days=10)).isoformat())):
            form = basic(**{**neutral_yes, **single})
            self.assertTrue(form.is_valid(), (single, form.errors))
            self.assertEqual(form.cleaned_data['health_screen'], 'yes')

    def test_yes_keeps_the_current_coherence_rules(self):
        base = dict(health_screen='yes', recent_surgery='none', eating_disorder_history='no',
                    **{f'parq_q{i}': 'no' for i in range(1, 8)})
        # Sexo masculino: el embarazo no se pregunta y se guarda 'none'; otro valor sigue siendo error
        male = basic(sex_for_calculation='M', injuries=['knee'], **base)
        self.assertTrue(male.is_valid(), male.errors)
        self.assertEqual(male.cleaned_data['pregnancy_status'], 'none')
        self.assertIn('pregnancy_status', basic(sex_for_calculation='M', pregnancy_status='pregnant', **base).errors)
        # Con sexo femenino, el embarazo es obligatorio
        self.assertIn('pregnancy_status', basic(injuries=['knee'], **base).errors)
        # Autorización médica sin fecha
        self.assertIn('clearance_date', basic(pregnancy_status='none', medical_clearance='on', **base).errors)

    def test_missing_or_unknown_answer_is_an_error_without_flooding_the_screen(self):
        for value in (None, '', 'legacy', 'quizas', 'YES', 'si'):
            form = basic(health_screen=value)
            self.assertEqual(list(form.errors), ['health_screen'], value)
            self.assertEqual(form.errors['health_screen'], [screening.HEALTH_SCREEN_REQUIRED])

    def test_answer_does_not_depend_on_the_order_of_the_post(self):
        post = QueryDict(mutable=True)
        for key, value in basic_post(injuries=['knee']).items():
            post.setlist(key, value if isinstance(value, list) else [value])
        post.setlist('health_screen', ['yes', 'no'])          # el último manda, como en Django
        form = HealthProfileForm(post)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['injuries'], [])


class FoodScreenTests(SimpleTestCase):
    def test_no_discards_food_garbage_without_errors(self):
        form = basic(**FOOD_GARBAGE)
        self.assertTrue(form.is_valid(), form.errors)
        for name, value in screening.food_neutral_values().items():
            self.assertEqual(form.cleaned_data[name], value, name)

    def test_counterproof_yes_validates_the_same_garbage(self):
        form = basic(food_screen='yes', **FOOD_GARBAGE)
        self.assertEqual(set(form.errors), {'diet_type', 'allergies', 'allergy_severity', 'intolerances'})
        self.assertEqual(error_steps(form.errors), [3])

    def test_yes_requires_a_diet_type(self):
        form = basic(food_screen='yes', allergies=['peanut'], allergy_severity='mild')
        self.assertEqual(list(form.errors), ['diet_type'])
        self.assertEqual(error_steps(form.errors), [3])

    def test_yes_with_nothing_is_an_error(self):
        form = basic(food_screen='yes', diet_type='omnivore')
        self.assertEqual(list(form.errors), ['food_screen'])
        self.assertEqual(form.errors['food_screen'], [screening.FOOD_YES_EMPTY])

    def test_yes_with_a_single_item_is_valid(self):
        for single in (dict(diet_type='vegan'), dict(diet_type='omnivore', intolerances=['lactose']),
                       dict(diet_type='omnivore', allergies=['peanut'], allergy_severity='anaphylaxis')):
            form = basic(food_screen='yes', **single)
            self.assertTrue(form.is_valid(), (single, form.errors))

    def test_yes_allergy_needs_severity(self):
        self.assertIn('allergy_severity', basic(food_screen='yes', diet_type='omnivore', allergies=['milk']).errors)

    def test_missing_or_unknown_answer_is_an_error(self):
        for value in (None, '', 'legacy', 'tal_vez'):
            form = basic(food_screen=value)
            self.assertEqual(list(form.errors), ['food_screen'], value)
            self.assertEqual(form.errors['food_screen'], [screening.FOOD_SCREEN_REQUIRED])


class DefaultedFieldsTests(SimpleTestCase):
    def test_an_empty_detail_is_listed_and_a_sent_one_is_not(self):
        for name, value in (('activity_level', 'light'), ('cooking_access', 'none'), ('budget_level', 'low'),
                            ('eats_out_per_week', '0')):
            form = basic(**{name: value})
            self.assertTrue(form.is_valid(), form.errors)
            self.assertNotIn(name, form.cleaned_data['defaulted_fields'], name)
            self.assertIn(name, basic().cleaned_data['defaulted_fields'], name)
        self.assertEqual(basic(activity_level='light').cleaned_data['activity_level'], 'light')

    def test_full_profile_has_nothing_defaulted(self):
        self.assertEqual(run().cleaned_data['defaulted_fields'], [])

    def test_none_at_home_counts_as_declared(self):
        form = basic(training_place='home', equipment=['none'])
        self.assertEqual(form.cleaned_data['equipment'], ['none'])
        self.assertNotIn('equipment', form.cleaned_data['defaulted_fields'])

    def test_at_the_gym_equipment_is_never_listed(self):
        for equipment in ([], ['bands'], ['none']):
            form = basic(training_place='gym', equipment=equipment)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertEqual(form.cleaned_data['equipment'], [])
            self.assertNotIn('equipment', form.cleaned_data['defaulted_fields'])

    def test_only_known_codes_and_canonical_order(self):
        data = basic(training_place='home').cleaned_data
        self.assertEqual(data['defaulted_fields'], list(screening.ordered_defaulted(data['defaulted_fields'])))
        self.assertTrue(set(data['defaulted_fields']) <= set(screening.DEFAULTABLE_FIELDS))


class BasicStepsTests(SimpleTestCase):
    def test_five_steps_with_the_contract_keys(self):
        self.assertEqual([s['key'] for s in ch.WIZARD_STEPS], ['basics', 'health', 'food', 'training', 'details'])
        self.assertEqual([s['number'] for s in ch.WIZARD_STEPS], sorted(SECTION_FIELDS))
        self.assertEqual([s['optional'] for s in ch.WIZARD_STEPS], [False, False, False, False, True])

    def test_each_field_belongs_to_exactly_one_step(self):
        names = [name for fields in SECTION_FIELDS.values() for name in fields]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(set(HEALTH_FORM_FIELDS) | {'health_screen'} <= set(SECTION_FIELDS[2]))
        self.assertTrue(set(FOOD_FORM_FIELDS) | {'food_screen'} <= set(SECTION_FIELDS[3]))
        self.assertIn('activity_level', SECTION_FIELDS[5])
        self.assertIn('target_weight_kg', SECTION_FIELDS[5])

    def test_bound_form_with_no_still_maps_every_field(self):
        form = basic()
        belonging = {name for names in SECTION_FIELDS.values() for name in names}
        self.assertTrue(set(form.fields) <= belonging)


def profile_like(**attrs):
    """Objeto con la interfaz de lectura de `HealthProfile` (sin base de datos)."""
    form = HealthProfileForm(valid_post())
    assert form.is_valid(), form.errors
    values = dict(form.cleaned_data)
    values.update(attrs)
    return SimpleNamespace(**values)


class BasicFormValuesTests(SimpleTestCase):
    def test_values_from_a_basic_profile_leave_unanswered_fields_empty(self):
        form = basic(training_place='home')
        profile = SimpleNamespace(**form.cleaned_data)
        values = form_values_from_profile(profile, 'toning')
        self.assertEqual((values['health_screen'], values['food_screen']), ('no', 'no'))
        for name in ('cooking_access', 'budget_level', 'eats_out_per_week', 'activity_level'):
            self.assertEqual(values[name], '', name)
        self.assertEqual(values['equipment'], [])
        self.assertEqual(values['defaulted_fields'], ['cooking_access', 'budget_level', 'eats_out_per_week',
                                                      'equipment', 'activity_level'])
        # Lo que nunca vio (respondió No) vuelve sin responder
        for name in ('recent_surgery', 'pregnancy_status', 'eating_disorder_history', 'diet_type',
                     *[f'parq_q{i}' for i in range(1, 8)]):
            self.assertEqual(values[name], '', name)
        # Guardar de nuevo sin tocar nada no convierte nada en «declarado»
        again = HealthProfileForm(as_post(values))
        self.assertTrue(again.is_valid(), again.errors)
        self.assertEqual(again.cleaned_data['defaulted_fields'], values['defaulted_fields'])
        self.assertEqual(again.cleaned_data['activity_level'], 'sedentary')

    def test_declared_values_come_back(self):
        profile = SimpleNamespace(**basic(activity_level='active', budget_level='low').cleaned_data)
        values = form_values_from_profile(profile)
        self.assertEqual((values['activity_level'], values['budget_level']), ('active', 'low'))
        self.assertEqual(values['cooking_access'], '')

    def test_legacy_preselection(self):
        neutral = profile_like(health_screen='legacy', food_screen='legacy', defaulted_fields=[])
        values = form_values_from_profile(neutral)
        self.assertEqual((values['health_screen'], values['food_screen']), ('no', 'no'))
        self.assertEqual(values['parq_q1'], 'no')                 # en legacy sí respondió las 7
        self.assertEqual(values['recent_surgery'], 'none')
        self.assertEqual(values['diet_type'], 'omnivore')
        self.assertEqual(values['activity_level'], 'light')       # legacy: todo declarado
        for attrs in (dict(injuries=['knee']), dict(parq={**neutral.parq, 'q5': True}),
                      dict(recent_surgery='over_12m'), dict(eating_disorder_history='prefer_not_say'),
                      dict(medications_text='algo'), dict(pregnancy_status='lactating')):
            legacy = profile_like(health_screen='legacy', food_screen='legacy', **attrs)
            self.assertEqual(form_values_from_profile(legacy)['health_screen'], 'yes', attrs)
        for attrs in (dict(diet_type='vegan'), dict(allergies=['egg'], allergy_severity='mild'),
                      dict(intolerances=['lactose'])):
            legacy = profile_like(health_screen='legacy', food_screen='legacy', **attrs)
            self.assertEqual(form_values_from_profile(legacy)['food_screen'], 'yes', attrs)

    def test_saved_yes_is_kept(self):
        values = form_values_from_profile(profile_like(health_screen='yes', food_screen='yes', injuries=['hip'],
                                                       diet_type='vegan'))
        self.assertEqual((values['health_screen'], values['food_screen']), ('yes', 'yes'))
        self.assertEqual(values['injuries'], ['hip'])

    def test_values_from_post_and_empty_values_have_the_new_keys(self):
        values = form_values_from_post(QueryDict('health_screen=no&food_screen=yes&training_place=home'))
        self.assertEqual((values['health_screen'], values['food_screen']), ('no', 'yes'))
        self.assertEqual(values['defaulted_fields'], ['cooking_access', 'budget_level', 'eats_out_per_week',
                                                      'equipment', 'activity_level'])
        empty = empty_form_values()
        self.assertEqual((empty['health_screen'], empty['food_screen'], empty['defaulted_fields']), ('', '', []))
        self.assertEqual(set(empty), set(values))
