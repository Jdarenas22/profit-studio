"""Constructor de rutinas: el número de consultas no crece con los días ni con los ejercicios (N+1)."""
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.tests.helpers import ScenarioTestCase
from apps.exercises.models import Exercise
from apps.routines.models import Routine, RoutineDay, RoutineExercise


class RoutineBuilderQueryTests(ScenarioTestCase):
    def make_routine(self, days, per_day, prefix='R'):
        routine = Routine.objects.create(name=f'{prefix} rutina', user=self.client_a, trainer=self.trainer_a)
        # los días y ejercicios se crean desordenados a propósito: la vista debe respetar `order`
        for d in reversed(range(days)):
            day = RoutineDay.objects.create(routine=routine, name=f'{prefix} dia {d}', order=d)
            for e in reversed(range(per_day)):
                exercise = Exercise.objects.create(
                    name=f'{prefix} ejercicio {d}-{e}', level=Exercise.LEVEL_BEGINNER, description='x', muscles='y')
                RoutineExercise.objects.create(day=day, exercise=exercise, sets=3, reps='10', rest_seconds=60, order=e)
        return routine

    def get_builder(self, routine):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('trainer_routine_builder', args=[routine.pk]))
        self.assertEqual(response.status_code, 200)
        return response, len(queries)

    def test_query_count_does_not_grow_with_days_and_exercises(self):
        self.login(self.trainer_a)
        _, small = self.get_builder(self.make_routine(1, 1, prefix='S'))
        _, large = self.get_builder(self.make_routine(6, 5, prefix='L'))   # 6 días × 5 = 30 ejercicios
        self.assertEqual(small, large, f'1 día/1 ejercicio: {small} consultas; 6 días/30 ejercicios: {large}')
        self.assertLessEqual(large, 10)   # holgura sobre las 7 actuales: el límite es que sea constante

    def test_every_day_and_exercise_is_still_rendered_in_order(self):
        self.login(self.trainer_a)
        response, _ = self.get_builder(self.make_routine(3, 2, prefix='O'))
        html = response.content.decode()
        positions = [html.index(f'O dia {d}') for d in range(3)]
        self.assertEqual(positions, sorted(positions), 'los días deben salir por `order`')
        for d in range(3):
            exercise_positions = [html.index(f'O ejercicio {d}-{e}</p>') for e in range(2)]
            self.assertEqual(exercise_positions, sorted(exercise_positions), 'los ejercicios salen por `order`')
        self.assertNotIn('Sin ejercicios aún', html)
        self.assertNotIn('Aún no hay días', html)

    def test_empty_states_still_show(self):
        self.login(self.trainer_a)
        response, _ = self.get_builder(self.make_routine(0, 0, prefix='V'))
        self.assertContains(response, 'Aún no hay días')
        response, _ = self.get_builder(self.make_routine(1, 0, prefix='W'))
        self.assertContains(response, 'Sin ejercicios aún')

    def test_permissions_are_unchanged(self):
        routine = self.make_routine(2, 2, prefix='P')
        # el entrenador B no tiene asignada a la clienta A: 404
        self.login(self.trainer_b)
        self.assertEqual(self.client.get(reverse('trainer_routine_builder', args=[routine.pk])).status_code, 404)
        # una clienta no es entrenadora: 403
        self.login(self.client_a)
        self.assertEqual(self.client.get(reverse('trainer_routine_builder', args=[routine.pk])).status_code, 403)
        # la superusuaria ve todo
        self.login(self.boss)
        self.assertEqual(self.client.get(reverse('trainer_routine_builder', args=[routine.pk])).status_code, 200)
