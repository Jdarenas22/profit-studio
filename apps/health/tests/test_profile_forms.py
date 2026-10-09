"""Validación de la ficha de salud (formulario): listas cerradas, rangos, coherencias, textos."""
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.http import QueryDict
from django.test import SimpleTestCase

from apps.health import dates
from apps.health.forms import (
    SECTION_FIELDS, HealthProfileForm, empty_form_values, error_steps, first_error_step,
    form_values_from_post, form_values_from_profile,
)

from .helpers import adult_birth_date, valid_post


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
    REQUIRED = ['birth_date', 'sex_for_calculation', 'training_goal', 'recent_surgery',
                'pregnancy_status', 'eating_disorder_history', 'diet_type', 'cooking_access',
                'budget_level', 'training_days_per_week', 'session_minutes', 'training_place',
                'experience_level', 'activity_level'] + [f'parq_q{i}' for i in range(1, 8)]

    def test_each_required_field_is_enforced(self):
        for name in self.REQUIRED:
            form = run(**{name: None})
            self.assertIn(name, form.errors, name)

    def test_unknown_codes_are_rejected(self):
        for name, value in [('sex_for_calculation', 'X'), ('training_goal', 'volar'),
                            ('recent_surgery', 'ayer'), ('pregnancy_status', 'quizas'),
                            ('eating_disorder_history', 'tal_vez'), ('diet_type', 'carnivora'),
                            ('cooking_access', 'mucho'), ('budget_level', 'infinito'),
                            ('training_place', 'luna'), ('experience_level', 'dios'),
                            ('activity_level', 'extrema'), ('parq_q3', 'tal vez')]:
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
        form = run(medical_clearance='', clearance_date='2025-01-01')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['clearance_date'])
        self.assertFalse(form.cleaned_data['medical_clearance'])

    def test_condition_controlled_is_cleared_without_conditions(self):
        form = run(conditions=[], condition_controlled='on')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.cleaned_data['condition_controlled'])
        kept = run(conditions=['asthma'], condition_controlled='on')
        self.assertTrue(kept.cleaned_data['condition_controlled'])

    def test_allergy_severity_only_with_allergies(self):
        self.assertIn('allergy_severity', run(allergies=['peanut'], allergy_severity='').errors)
        self.assertNotIn('allergy_severity', run(allergies=['peanut'], allergy_severity='mild').errors)
        form = run(allergies=[], allergy_severity='anaphylaxis')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['allergy_severity'], '')

    def test_equipment_rules(self):
        self.assertIn('equipment', run(training_place='home', equipment=[]).errors)
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
                      ['breakfast', 'snack', 'dinner'], ['mid_morning', 'snack', 'lunch'], []):
            self.assertIn('meal_slots', run(meal_slots=slots).errors, slots)

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
        self.assertEqual(error_steps(errors), [1, 3, 4, 5])
        self.assertEqual(first_error_step(errors), 1)
        self.assertEqual(error_steps({'meal_time_lunch': 'x'}), [4])
        self.assertEqual(error_steps({'general': 'x'}), [1])

    def test_form_errors_map_to_steps(self):
        form = run(parq_q2=None, session_minutes='5', meal_slots=['lunch'])
        self.assertEqual(error_steps({name: 'x' for name in form.errors}), [3, 4, 5])

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
        self.assertEqual(values['meal_slots'], ['breakfast', 'lunch', 'dinner'])
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
