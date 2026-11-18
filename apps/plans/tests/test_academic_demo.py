"""Aislamiento, cuota persistente y flujo académico: todo sin red."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from apps.plans import ai, jobs
from apps.plans.academic import DemoBoundaryMiddleware, demo_environment
from apps.plans.ai.base import AIPermanentError
from apps.plans.ai.demo import DemoGeminiClient, reserve_call
from apps.plans.tests.helpers import NutritionScenarioTestCase
from apps.plans.training_context import assigned_training_summary


class DemoIsolationTests(SimpleTestCase):
    def test_regular_development_is_not_a_demo_environment(self):
        self.assertFalse(demo_environment())

    def test_requires_dedicated_database_debug_and_explicit_module(self):
        database = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': settings.BASE_DIR / '.demo/demo.sqlite3'}}
        with override_settings(ACADEMIC_DEMO=True, DEBUG=True, SETTINGS_MODULE='config.settings.demo', DATABASES=database):
            self.assertTrue(demo_environment())
            with override_settings(DEBUG=False):
                self.assertFalse(demo_environment())
            with override_settings(SETTINGS_MODULE='config.settings.production'):
                self.assertFalse(demo_environment())
            with override_settings(DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': settings.BASE_DIR / 'db.sqlite3'}}):
                self.assertFalse(demo_environment())

    @override_settings(AI_PLANS_MODE='demo', GEMINI_API_KEY='fictional', GEMINI_MODEL='gemini-3.5-flash-lite', DEMO_FREE_TIER_CONFIRMED=True)
    def test_demo_cannot_bypass_normal_environment(self):
        with self.assertRaises(AIPermanentError):
            ai.get_ai_client()

    @override_settings(AI_PLANS_MODE='demo', GEMINI_API_KEY='fictional', GEMINI_MODEL='gemini-3.5-flash-lite', GEMINI_BILLING_CONFIRMED=False)
    def test_free_tier_confirmation_is_separate_from_billing(self):
        with mock.patch('apps.plans.ai.demo.demo_environment', return_value=True):
            with override_settings(DEMO_FREE_TIER_CONFIRMED=False), self.assertRaises(AIPermanentError):
                ai.get_ai_client()
            with override_settings(DEMO_FREE_TIER_CONFIRMED=True):
                self.assertIsInstance(ai.get_ai_client(), DemoGeminiClient)
            with override_settings(DEMO_FREE_TIER_CONFIRMED=True, GEMINI_MODEL='some-paid-model'), self.assertRaises(AIPermanentError):
                ai.get_ai_client()

    def test_seed_refuses_regular_database_before_queries(self):
        with self.assertRaisesMessage(CommandError, 'config.settings.demo'):
            call_command('seed_academic_demo')

    def test_loopback_only_and_no_cache(self):
        middleware = DemoBoundaryMiddleware(lambda request: HttpResponse('ok'))
        with mock.patch('apps.plans.academic.demo_environment', return_value=True):
            for address, status in [('127.0.0.1', 200), ('::1', 200), ('192.168.0.15', 403), ('', 403)]:
                response = middleware(RequestFactory().get('/demo/', REMOTE_ADDR=address))
                self.assertEqual(response.status_code, status)
                if status == 200:
                    self.assertEqual(response['Cache-Control'], 'no-store')

    def test_persistent_budget_fails_closed_at_limit(self):
        with TemporaryDirectory() as directory, override_settings(BASE_DIR=Path(directory)):
            (Path(directory) / '.demo').mkdir()
            with mock.patch('apps.plans.ai.demo.configured', return_value=True), mock.patch('apps.plans.ai.demo.MAX_DAILY_CALLS', 2):
                reserve_call()
                reserve_call()
                with self.assertRaises(AIPermanentError) as error:
                    reserve_call()
                self.assertEqual(error.exception.code, 'demo_daily_limit')


class AssignedRoutineTests(NutritionScenarioTestCase):
    def test_only_own_active_routine_without_names_or_notes(self):
        self.routine_a.name = 'private routine name'
        self.routine_a.notes = 'private free text'
        self.routine_a.save()
        result = assigned_training_summary(self.client_a)
        self.assertEqual(result['days_per_cycle'], 1)
        self.assertEqual(result['days'][0]['total_sets'], 3)
        self.assertNotIn('private', str(result))
        self.assertNotIn(self.client_a.username, str(result))
        self.routine_a.is_active = False
        self.routine_a.save()
        self.assertFalse(assigned_training_summary(self.client_a)['assigned'])

    def test_five_meal_calls_contain_assigned_routine_summary(self):
        self.ready(self.client_a, meal_slots=['breakfast', 'mid_morning', 'lunch', 'snack', 'dinner'])
        job, fake = self.request_nutrition(self.client_a)
        self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
        self.assertEqual(len(fake.calls), 5)
        for call in fake.calls:
            summary = call['payload']['training']['assigned_routine']
            self.assertTrue(summary['assigned'])
            self.assertEqual(summary['days_per_cycle'], 1)

    def test_real_demo_client_rejects_non_synthetic_account_before_network(self):
        self.ready(self.client_a)
        job, _ = self.request_nutrition(self.client_a)
        client = DemoGeminiClient(api_key='fictional', model='gemini-3.5-flash-lite')
        with mock.patch('apps.plans.ai.demo.configured', return_value=True), mock.patch('apps.plans.ai.demo.reserve_call') as reserve:
            with self.assertRaises(AIPermanentError) as error:
                client.generate_json(task='meal_slot', system='', payload={}, schema={}, job_ref=job.job_ref)
            self.assertEqual(error.exception.code, 'demo_synthetic_user_required')
            reserve.assert_not_called()
