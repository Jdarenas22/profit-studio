"""Regresiones de consumo: invalidación de caché, consultas y límite de trabajos web."""
from dataclasses import replace
from decimal import Decimal
from unittest import mock
import threading

from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.health.tests.helpers import HealthScenarioTestCase
from apps.plans import jobs
from apps.plans.models import KIND_EXERCISE, Plan
from apps.plans.rules import recipes
from apps.plans.tests.test_wellness_recipes import rules_for
from apps.routines.models import Routine
from apps.routines.views import member_routine


class RecipeCacheTests(SimpleTestCase):
    def setUp(self):
        recipes._fit_portions.cache_clear()
        self.addCleanup(recipes._fit_portions.cache_clear)

    def test_cached_portions_match_uncached_and_outputs_are_independent(self):
        rules = rules_for('lunch')
        expected = recipes.candidates(rules)
        self.assertTrue(expected)
        with mock.patch.object(recipes.meal, 'evaluate_option', side_effect=AssertionError('recalculated')):
            cached = recipes.candidates(rules)
        self.assertEqual(cached, expected)
        cached[0]['items'][0]['grams'] = -1
        cached[0]['name'] = 'alterado'
        self.assertEqual(recipes.candidates(rules), expected)
        recipes._fit_portions.cache_clear()
        self.assertEqual(recipes.candidates(rules), expected)

    def test_food_restrictions_and_changed_composition_never_reuse_stale_portions(self):
        rules = rules_for('lunch')
        recipes.candidates(rules)
        food_id = next(iter(rules.foods))
        food = rules.foods[food_id]
        changed = dict(rules.foods)
        changed[food_id] = replace(food, protein100=food.protein100 + Decimal('1'))
        with mock.patch.object(recipes.meal, 'evaluate_option', wraps=recipes.meal.evaluate_option) as evaluate:
            recipes.candidates(replace(rules, foods=changed))
        self.assertGreater(evaluate.call_count, 0)
        restricted = {key: value for key, value in rules.foods.items() if key != food_id}
        result = recipes.candidates(replace(rules, foods=restricted))
        self.assertFalse(any(item['food_id'] == food_id for option in result for item in option['items']))
        self.assertEqual(recipes._fit_portions.cache_info().maxsize, 256)

    def test_energy_and_protein_changes_invalidate_cached_portions(self):
        rules = rules_for('lunch')
        recipes.candidates(rules)
        for new_rules in (replace(rules, kcal=Decimal('550')), replace(rules, protein_g=Decimal('23'))):
            with mock.patch.object(recipes.meal, 'evaluate_option', wraps=recipes.meal.evaluate_option) as evaluate:
                recipes.candidates(new_rules)
            self.assertGreater(evaluate.call_count, 0)


@override_settings(AI_PLANS_RUN_INLINE=False, AI_PLANS_WORKER=False)
class WebJobLimitTests(SimpleTestCase):
    def setUp(self):
        patcher = mock.patch.object(jobs, '_web_slots', threading.BoundedSemaphore(2))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_two_generations_run_and_excess_threads_return_without_waiting(self):
        release = threading.Event()
        entered = [threading.Event(), threading.Event()]
        def slow_job(job_id):
            entered[job_id - 1].set()
            if not release.wait(5):
                raise AssertionError('La prueba no liberó el trabajo')
        with mock.patch.object(jobs, 'close_old_connections'), mock.patch.object(jobs, 'connection'), \
                mock.patch.object(jobs, 'run_job', side_effect=slow_job) as run, \
                mock.patch.object(jobs, 'run_next_queued', return_value=None):
            threads = [threading.Thread(target=jobs._thread_main, args=(number,)) for number in (1, 2)]
            try:
                for thread in threads:
                    thread.start()
                self.assertTrue(all(event.wait(2) for event in entered))
                excess = threading.Thread(target=jobs._thread_main, args=(3,))
                excess.start()
                excess.join(2)
                self.assertFalse(excess.is_alive())
                self.assertEqual(run.call_count, 2)
            finally:
                release.set()
                for thread in threads:
                    thread.join(2)
            self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_existing_thread_drains_database_queue_then_releases_slot(self):
        with mock.patch.object(jobs, 'close_old_connections'), mock.patch.object(jobs, 'connection'), \
                mock.patch.object(jobs, 'run_job') as run, \
                mock.patch.object(jobs, 'run_next_queued', side_effect=[2, 3, None]) as take:
            jobs._thread_main(1)
        run.assert_called_once_with(1)
        self.assertEqual(take.call_count, 3)
        self.assertTrue(jobs._web_slots.acquire(blocking=False))
        self.assertTrue(jobs._web_slots.acquire(blocking=False))
        self.assertFalse(jobs._web_slots.acquire(blocking=False))

    def test_execution_errors_release_capacity(self):
        with mock.patch.object(jobs, 'close_old_connections'), mock.patch.object(jobs, 'connection'), \
                mock.patch.object(jobs, 'run_job', side_effect=RuntimeError('execution failed')):
            with self.assertLogs(jobs.logger, level='ERROR'):
                jobs._thread_main(2)
        self.assertTrue(jobs._web_slots.acquire(blocking=False))
        self.assertTrue(jobs._web_slots.acquire(blocking=False))


class RoutineQueryTests(HealthScenarioTestCase):
    def test_plan_and_reviewer_queries_remain_constant_as_routines_grow(self):
        def inspect():
            request = RequestFactory().get('/routines/')
            request.user = self.client_a
            with mock.patch('apps.routines.views.render', side_effect=lambda _r, _t, context: context):
                context = member_routine(request)
            with self.assertNumQueries(4):
                for routine in context['routines']:
                    if hasattr(routine, 'plan'):
                        self.assertEqual(routine.plan.approved_by.pk, self.boss.pk)
                    for day in routine.days.all():
                        for item in day.exercises.all():
                            self.assertTrue(item.exercise.name)

        Plan.objects.create(client=self.client_a, kind=KIND_EXERCISE, version=1,
                            routine=self.routine_a, approved_by=self.boss)
        inspect()
        for version in range(2, 7):
            routine = Routine.objects.create(user=self.client_a, trainer=self.boss, name=f'Rutina {version}')
            Plan.objects.create(client=self.client_a, kind=KIND_EXERCISE, version=version,
                                routine=routine, approved_by=self.boss)
        inspect()


class TestRunnerIsolationTests(SimpleTestCase):
    def test_fast_hashing_is_explicit_and_restored_even_when_suite_fails(self):
        from django.conf import settings
        from django.test.runner import DiscoverRunner
        from config.test_runner import ResourceTestRunner
        original = list(settings.PASSWORD_HASHERS)
        def failing_suite(_labels, **_kwargs):
            self.assertEqual(settings.PASSWORD_HASHERS[0], 'django.contrib.auth.hashers.MD5PasswordHasher')
            self.assertTrue(set(original).issubset(settings.PASSWORD_HASHERS))
            raise RuntimeError('suite interrupted')
        with mock.patch.object(DiscoverRunner, 'run_tests', side_effect=failing_suite):
            with self.assertRaises(RuntimeError):
                ResourceTestRunner(verbosity=0).run_tests([])
        self.assertEqual(settings.PASSWORD_HASHERS, original)
