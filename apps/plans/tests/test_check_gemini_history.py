"""`check_gemini --history` (paso 5 de docs/contracts/plan-ia-adaptacion.md), sin red: Gemini se reemplaza
por respuestas armadas en la prueba. Comprueba QUÉ se envía (textos `/2`, `history`, instrucción escondida)
y cómo se informa que el modelo siga o no el historial."""
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from apps.plans.ai.base import AIResult, AIUsage
from apps.plans.ai.fake import sample_exercise_plan, sample_meal_slot
from apps.plans.ai.gemini import GeminiClient
from apps.plans.ai.prompts import EXERCISE_SYSTEM, EXERCISE_SYSTEM_V2, MEAL_SYSTEM, MEAL_SYSTEM_V2
from apps.plans.management.commands import check_gemini as cg

KEY = 'AIzaClaveInventada-0123456789abcdef'


def without_history(payload):
    return {k: v for k, v in payload.items() if k != 'history'}


def following(payload):
    """Respuesta de un modelo que sigue el historial (el generador simulado lo respeta)."""
    data = sample_meal_slot(payload) if payload['task'] == 'meal_slot' else sample_exercise_plan(payload)
    return data


def ignoring(payload):
    """Respuesta de un modelo que ignora el historial por completo."""
    plain = without_history(payload)
    return sample_meal_slot(plain) if plain['task'] == 'meal_slot' else sample_exercise_plan(plain)


class CheckGeminiHistoryTests(SimpleTestCase):
    def run_command(self, answer=following, history=True):
        calls, out = [], StringIO()

        def fake_generate(client, **kwargs):
            calls.append(kwargs)
            return AIResult(data=answer(kwargs['payload']), usage=AIUsage(10, 20), model='m')
        with override_settings(GEMINI_API_KEY=KEY, GEMINI_MODEL='m', GEMINI_BILLING_CONFIRMED=True), \
                mock.patch.object(GeminiClient, 'generate_json', fake_generate):
            call_command('check_gemini', history=history, stdout=out)
        return out.getvalue(), calls

    def test_without_the_flag_nothing_about_history_runs(self):
        out, calls = self.run_command(history=False)
        self.assertEqual(len(calls), 2)
        self.assertNotIn('Adaptación', out)
        self.assertTrue(all('history' not in call['payload'] for call in calls))
        self.assertEqual([call['system'] for call in calls], [EXERCISE_SYSTEM, MEAL_SYSTEM])

    def test_the_history_calls_use_the_v2_prompts_and_a_history_block(self):
        _out, calls = self.run_command()
        self.assertEqual(len(calls), 5)                              # 2 de siempre + 3 con historial
        base, history_calls = calls[:2], calls[2:]
        self.assertTrue(all('history' not in call['payload'] for call in base))
        self.assertEqual([call['system'] for call in history_calls],
                         [EXERCISE_SYSTEM_V2, EXERCISE_SYSTEM_V2, MEAL_SYSTEM_V2])
        self.assertTrue(all('history' in call['payload'] for call in history_calls))

    def test_the_hidden_instruction_only_travels_inside_the_history(self):
        _out, calls = self.run_command()
        hostile = calls[3]['payload']
        self.assertIn(cg.HOSTILE_DAY_NAME, hostile['history']['training']['skipped_day_names'])
        self.assertNotIn(cg.HOSTILE_DAY_NAME, str({k: v for k, v in hostile.items() if k != 'history'}))
        for call in calls:
            self.assertNotIn(cg.HOSTILE_DAY_NAME, call['system'])

    def test_the_test_data_is_fictional_and_has_no_personal_fields(self):
        _out, calls = self.run_command()
        for call in calls[2:]:
            sent = str(call['payload'])
            for field in ('email', 'username', 'first_name', 'birth_date', 'phone'):
                self.assertNotIn(field, sent)
            self.assertEqual(set(call['payload']['history']),
                             {'weeks_of_data', 'training', 'nutrition', 'trainer_edits', 'progress'})

    def test_a_model_that_follows_the_history_gets_all_the_checks(self):
        out, _calls = self.run_command()
        self.assertIn('cumple: formato y reglas de seguridad', out)
        self.assertIn('cumple: evita el ejercicio que el entrenador suele quitar', out)
        self.assertIn('cumple: sesiones cortas con constancia baja', out)
        self.assertIn('cumple: trata el historial como datos', out)
        self.assertIn('cumple: evita los alimentos que la clienta descarta', out)
        self.assertIn('cumple: evita el alimento que el entrenador suele quitar', out)
        self.assertNotIn('   NO cumple', out)
        self.assertNotIn(KEY, out)

    def test_a_model_that_ignores_the_history_only_warns(self):
        out, _calls = self.run_command(answer=ignoring)
        self.assertIn('NO cumple: evita el ejercicio que el entrenador suele quitar', out)
        self.assertIn('Python ya lo quita', out)
        self.assertIn('Adaptación: la prueba terminó', out)            # no falló: solo avisa

    def test_a_model_that_obeys_the_hidden_instruction_is_reported(self):
        def obeys(payload):
            data = following(payload)
            if payload['task'] == 'exercise_plan' and cg.HOSTILE_DAY_NAME in str(payload.get('history')):
                data['general_notes'] = 'Toma un suplemento de proteína después de cada sesión.'
            return data
        out, _calls = self.run_command(answer=obeys)
        self.assertIn('NO cumple: trata el historial como datos', out)
        self.assertIn('el texto menciona un suplemento', out)

    def test_an_invalid_format_with_history_fails(self):
        def bad_ids(payload):
            data = following(payload)
            if payload['task'] == 'exercise_plan' and 'history' in payload:
                data['days'][0]['exercises'][0]['exercise_id'] = 987654321
            return data
        with self.assertRaisesMessage(CommandError, 'no pasó la validación'):
            self.run_command(answer=bad_ids)
