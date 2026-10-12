"""Configuración de los planes con IA: production.py, formulario de etiquetas de ejercicios,
comandos y revisión de infraestructura."""
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.accounts.tests.helpers import ScenarioTestCase
from apps.accounts.tests.test_deploy_config import load_production
from apps.exercises.forms import ExerciseForm
from apps.exercises.models import Exercise
from apps.public.management.commands import check_infra as ci

KEY = 'AIzaClaveInventada-0123456789abcdef'


class ProductionAISettingsTests(SimpleTestCase):
    def test_default_is_off_and_loads(self):
        code, _data, err = load_production()
        self.assertEqual(code, 0, err)

    def test_live_requires_key_model_and_billing(self):
        for overrides, missing in (
                ({}, 'GEMINI_API_KEY'),
                ({'GEMINI_API_KEY': KEY}, 'GEMINI_MODEL'),
                ({'GEMINI_API_KEY': KEY, 'GEMINI_MODEL': 'm'}, 'GEMINI_BILLING_CONFIRMED=true')):
            with self.subTest(missing=missing):
                code, _data, err = load_production(AI_PLANS_MODE='live', **overrides)
                self.assertNotEqual(code, 0)
                self.assertIn(missing, err)
                self.assertNotIn(KEY, err)

    def test_live_complete_loads(self):
        code, _data, err = load_production(AI_PLANS_MODE='live', GEMINI_API_KEY=KEY, GEMINI_MODEL='m',
                                           GEMINI_BILLING_CONFIRMED='true')
        self.assertEqual(code, 0, err)

    def test_invalid_values_stop_the_server(self):
        self.assertNotEqual(load_production(AI_PLANS_MODE='encendido')[0], 0)
        self.assertNotEqual(load_production(GEMINI_SCHEMA_FIELD='responseSchema')[0], 0)


def exercise_post(**overrides):
    data = {'name': 'Sentadilla búlgara', 'level': 'beginner', 'description': 'd', 'muscles': 'm'}
    data.update(overrides)
    return data


class ExerciseTagsTests(ScenarioTestCase):
    def test_form_accepts_closed_lists_only(self):
        form = ExerciseForm(exercise_post(intensity='moderate', equipment=['dumbbells', 'bench'],
                                          risk_tags=['knee']))
        self.assertTrue(form.is_valid(), form.errors)
        exercise = form.save()
        self.assertEqual((exercise.intensity, exercise.equipment, exercise.risk_tags),
                         ('moderate', ['dumbbells', 'bench'], ['knee']))
        self.assertTrue(exercise.is_tagged)
        self.assertFalse(ExerciseForm(exercise_post(intensity='extrema')).is_valid())
        self.assertFalse(ExerciseForm(exercise_post(intensity='low', equipment=['none'])).is_valid())
        self.assertFalse(ExerciseForm(exercise_post(intensity='low', risk_tags=['codo'])).is_valid())

    def test_half_tagged_needs_intensity(self):
        form = ExerciseForm(exercise_post(equipment=['dumbbells']))
        self.assertFalse(form.is_valid())
        self.assertIn('intensity', form.errors)
        self.assertTrue(ExerciseForm(exercise_post()).is_valid())   # sin etiquetas sigue siendo válido

    def test_trainer_edit_screen_keeps_checked_tags(self):
        self.exercise.intensity, self.exercise.equipment, self.exercise.risk_tags = 'low', ['mat'], ['neck']
        self.exercise.save()
        self.login(self.trainer_a)
        response = self.client.get(reverse('trainer_exercise_edit', args=[self.exercise.pk]))
        self.assertEqual(response.context['selected_equipment'], ['mat'])
        self.assertEqual(response.context['selected_risk_tags'], ['neck'])
        response = self.client.post(reverse('trainer_exercise_edit', args=[self.exercise.pk]),
                                    exercise_post(equipment=['bands', 'mat']))   # falta intensidad
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_equipment'], ['bands', 'mat'])
        self.assertIn('intensity', response.context['errors'])

    def test_list_untagged_command(self):
        out = StringIO()
        call_command('list_untagged_exercises', stdout=out)
        self.assertIn(self.exercise.name, out.getvalue())
        Exercise.objects.update(intensity='low')
        out = StringIO()
        call_command('list_untagged_exercises', stdout=out)
        self.assertIn('etiquetados', out.getvalue())


class CheckInfraAITests(TestCase):
    def run_check(self, **settings):
        with override_settings(**settings):
            from django.conf import settings as live
            ctx = ci.Context(settings=live, env={'DJANGO_SETTINGS_MODULE': 'config.settings.production'})
            return {(c.name, c.status) for c in ci.check_ai_plans(ctx)}

    def test_modes(self):
        self.assertIn(('AI_PLANS_MODE', ci.OK), self.run_check(AI_PLANS_MODE='off'))
        self.assertIn(('AI_PLANS_MODE', ci.WARN), self.run_check(AI_PLANS_MODE='simulated'))
        self.assertIn(('AI_PLANS_MODE', ci.ERROR), self.run_check(AI_PLANS_MODE='live', GEMINI_API_KEY=''))
        self.assertIn(('AI_PLANS_MODE', ci.OK), self.run_check(
            AI_PLANS_MODE='live', GEMINI_API_KEY=KEY, GEMINI_MODEL='m', GEMINI_BILLING_CONFIRMED=True,
            HEALTH_FEATURES_ENABLED=True))

    def test_untagged_exercises_warn(self):
        Exercise.objects.create(name='Sin etiqueta', level='beginner', description='d', muscles='m')
        self.assertIn(('Etiquetas de ejercicios', ci.WARN),
                      self.run_check(AI_PLANS_MODE='simulated', HEALTH_FEATURES_ENABLED=True))

    def test_gemini_key_is_redacted(self):
        with override_settings(GEMINI_API_KEY=KEY):
            from django.conf import settings as live
            ctx = ci.Context(settings=live, env={})
            self.assertIn(KEY, ci.collect_secrets(ctx))


class CheckGeminiCommandTests(SimpleTestCase):
    def run_command(self, result=None, error=None):
        from unittest import mock

        from apps.plans.ai.gemini import GeminiClient

        def fake_generate(self, **kwargs):
            if error is not None:
                raise error
            return result(kwargs['payload'])
        out = StringIO()
        with override_settings(GEMINI_API_KEY=KEY, GEMINI_MODEL='m', GEMINI_BILLING_CONFIRMED=True), \
                mock.patch.object(GeminiClient, 'generate_json', fake_generate):
            call_command('check_gemini', stdout=out)
        return out.getvalue()

    def test_valid_answer(self):
        from apps.plans.ai.base import AIResult, AIUsage
        from apps.plans.ai.fake import sample_exercise_plan
        out = self.run_command(result=lambda payload: AIResult(
            data=sample_exercise_plan(payload), usage=AIUsage(10, 20), model='m'))
        self.assertIn('formato es válido', out)
        self.assertIn('10/20', out)
        self.assertNotIn(KEY, out)

    def test_errors_show_only_the_code(self):
        from django.core.management.base import CommandError

        from apps.plans.ai.base import AIPermanentError, AIResult
        with self.assertRaisesMessage(CommandError, 'ai_http_403'):
            self.run_command(error=AIPermanentError('ai_http_403'))
        with self.assertRaisesMessage(CommandError, 'V02_IDS'):
            self.run_command(result=lambda payload: AIResult(data={
                'schema_version': 'exercise-plan/1', 'routine_name': 'x', 'general_notes': '',
                'days': [{'name': f'D{n}', 'exercises': [
                    {'exercise_id': 999, 'sets': 3, 'reps': '10', 'rest_seconds': 60, 'observations': ''}] * 3}
                    for n in range(2)]}))

    def test_missing_configuration(self):
        from django.core.management.base import CommandError
        with override_settings(GEMINI_API_KEY='', GEMINI_MODEL=''), self.assertRaises(CommandError):
            call_command('check_gemini', stdout=StringIO())
