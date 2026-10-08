"""Rutinas, días y ejercicios en día: validación de entradas (vistas normales y HTMX)."""
from unittest import mock

from django.urls import reverse

from apps.accounts.tests.helpers import ScenarioTestCase
from apps.exercises.models import Exercise
from apps.routines import views
from apps.routines.models import Routine, RoutineDay, RoutineExercise


class RoutineCreateValidationTests(ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)
        self.url = reverse('trainer_routine_create')
        self.count = Routine.objects.count()

    def test_blank_or_too_long_name_is_rejected_inline_without_duplicate_flash(self):
        for data in ({'name': '', 'client': self.client_a.pk}, {'name': '   ', 'client': self.client_a.pk},
                     {'name': 'x' * 201, 'client': self.client_a.pk},
                     {'name': 'ok', 'client': self.client_a.pk, 'notes': 'x' * 2001}):
            with self.subTest(name=data['name'][:10]):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['errors'])
                # routine_create.html pinta name y notes junto al campo: sin duplicado flash
                self.assertFalse([str(m) for m in response.context['messages']])
        self.assertEqual(Routine.objects.count(), self.count)

    def test_error_not_painted_by_the_template_is_flashed(self):
        rendered = tuple(f for f in views.ROUTINE_RENDERED if f != 'name')
        with mock.patch.object(views, 'ROUTINE_RENDERED', rendered):
            response = self.client.post(self.url, {'name': '', 'client': self.client_a.pk})
        self.assertTrue([str(m) for m in response.context['messages']])

    def test_missing_name_does_not_raise_key_error(self):
        self.assertEqual(self.client.post(self.url, {'client': self.client_a.pk}).status_code, 200)

    def test_authorization_still_wins_over_validation(self):
        """Cliente ajeno + nombre vacío sigue siendo 404 (no se revela nada del cliente)."""
        self.assertEqual(self.client.post(self.url, {'name': '', 'client': self.client_b.pk}).status_code, 404)


class AddDayValidationTests(ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)
        self.url = reverse('trainer_add_day', args=[self.routine_a.pk])

    def test_errors_are_returned_as_the_same_red_html_fragment(self):
        before = self.routine_a.days.count()
        for data in ({}, {'day_name': ''}, {'day_name': '   '}, {'day_name': 'x' * 101}):
            with self.subTest(data=str(data)[:30]):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)    # htmx 1.x no intercambia (swap) los 4xx
                self.assertContains(response, 'class="text-red-400 text-xs p-2"')
                self.assertEqual(response['X-Form-Error'], '1')
        self.assertEqual(self.routine_a.days.count(), before)

    def test_error_text_is_escaped(self):
        # el mensaje es fijo, pero el fragmento nunca debe reflejar la entrada sin escapar
        response = self.client.post(self.url, {'day_name': '<script>alert(1)</script>' * 10})
        self.assertNotContains(response, '<script>alert')

    def test_valid_day_is_created_and_has_no_error_header(self):
        response = self.client.post(self.url, {'day_name': '  Día de piernas  '})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('X-Form-Error', response)
        self.assertTrue(self.routine_a.days.filter(name='Día de piernas').exists())


class AddExerciseValidationTests(ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)
        self.url = reverse('trainer_add_exercise_to_day', args=[self.routine_a.pk, self.day_a.pk])
        self.count = RoutineExercise.objects.count()

    def post(self, **over):
        data = {'exercise': self.exercise.pk, 'sets': '3', 'reps': '10', 'rest_seconds': '60'}
        data.update(over)
        return self.client.post(self.url, data)

    def assert_error(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'text-red-400')
        self.assertEqual(response['X-Form-Error'], '1')
        self.assertEqual(RoutineExercise.objects.count(), self.count)

    def test_invalid_numbers_do_not_500(self):
        for field, values in {
            'sets': ['abc', '0', '-1', '21', '3.5', '', '9' * 5000, '99999999999999999999'],
            'rest_seconds': ['abc', '-5', '601', '1.5', '9' * 5000],
        }.items():
            for value in values:
                if value == '':
                    continue     # vacío = valor por defecto (ver test siguiente)
                with self.subTest(field=field, value=value[:15]):
                    self.assert_error(self.post(**{field: value}))

    def test_exercise_must_exist_and_be_active(self):
        inactive = Exercise.objects.create(name='Vieja', level='beginner', description='x', muscles='y',
                                           is_active=False)
        for value in ('', 'abc', '99999', '-1', str(inactive.pk)):
            with self.subTest(exercise=value):
                self.assert_error(self.post(exercise=value))
        response = self.client.post(self.url, {})
        self.assert_error(response)

    def test_reps_are_limited_and_restricted_to_simple_text(self):
        for bad in ('x' * 51, '<script>alert(1)</script>', '10"><img src=x>', 'a;b'):
            with self.subTest(reps=bad[:15]):
                self.assert_error(self.post(reps=bad))
        for good in ('10', '10-12', '30 seg', '8/8', 'Al fallo', 'AMRAP', '3 x 12'):
            with self.subTest(reps=good):
                self.assertNotIn('X-Form-Error', self.post(reps=good))

    def test_observations_are_limited(self):
        self.assert_error(self.post(observations='x' * 501))

    def test_missing_optional_values_use_the_interface_defaults(self):
        response = self.client.post(self.url, {'exercise': self.exercise.pk})
        self.assertEqual(response.status_code, 200)
        item = RoutineExercise.objects.latest('pk')
        self.assertEqual((item.sets, item.reps, item.rest_seconds), (3, '10', 60))

    def test_boundary_values_are_accepted(self):
        self.post(sets='1', rest_seconds='0')
        self.post(sets='20', rest_seconds='600')
        self.assertEqual(RoutineExercise.objects.count(), self.count + 2)

    def test_cannot_add_to_a_day_of_another_routine(self):
        """La validación no reemplaza la autorización: día de otra rutina => 404."""
        url = reverse('trainer_add_exercise_to_day', args=[self.routine_a.pk, self.day_b.pk])
        self.assertEqual(self.client.post(url, {'exercise': self.exercise.pk}).status_code, 404)
