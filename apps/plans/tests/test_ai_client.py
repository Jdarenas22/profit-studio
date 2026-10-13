"""Cliente de Gemini (sin red: un `opener` falso hace de servidor) y selección del modo."""
import io
import json
import socket
import urllib.error
from email.message import Message

from django.test import SimpleTestCase, override_settings

from apps.plans import ai
from apps.plans.ai.base import AIBlockedError, AIInvalidOutput, AIPermanentError, AITransientError
from apps.plans.ai.fake import FakeAIClient
from apps.plans.ai.gemini import API_ROOT, GeminiClient

KEY = 'AIzaClaveInventada-0123456789abcdef'
MODEL = 'gemini-modelo-de-prueba'


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Opener:
    """Hace de servidor: guarda la petición y devuelve `body` o lanza `error`."""

    def __init__(self, body=None, error=None):
        self.body, self.error, self.requests = body, error, []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        return FakeResponse(json.dumps(self.body).encode('utf-8'))


def ok_body(data, finish='STOP', thought=False):
    parts = [{'text': 'pensando...', 'thought': True}] if thought else []
    parts.append({'text': json.dumps(data)})
    return {'candidates': [{'content': {'parts': parts}, 'finishReason': finish}],
            'usageMetadata': {'promptTokenCount': 120, 'candidatesTokenCount': 80, 'thoughtsTokenCount': 20},
            'modelVersion': MODEL}


def http_error(status, retry_after=None, body=b'{"error": {"message": "detalle con datos"}}'):
    headers = Message()
    if retry_after is not None:
        headers['Retry-After'] = str(retry_after)
    return urllib.error.HTTPError(f'{API_ROOT}/x', status, 'error', headers, io.BytesIO(body))


def call(client, payload=None):
    return client.generate_json(task='exercise_plan', system='Instrucción', payload=payload or {'a': 1},
                                schema={'type': 'object'}, job_ref='ref')


class GeminiClientTests(SimpleTestCase):
    def gemini(self, opener, **kwargs):
        return GeminiClient(api_key=KEY, model=MODEL, timeout=60, opener=opener, **kwargs)

    def test_request_shape_and_key_only_in_header(self):
        opener = Opener(ok_body({'ok': True}))
        result = call(self.gemini(opener), payload={'job_ref': 'r', 'texto': 'ñandú'})
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, f'{API_ROOT}/models/{MODEL}:generateContent')
        self.assertNotIn(KEY, request.full_url)
        self.assertEqual(request.get_header('X-goog-api-key'), KEY)
        self.assertEqual(timeout, 60)
        body = json.loads(request.data)
        self.assertEqual(body['generationConfig']['responseMimeType'], 'application/json')
        self.assertEqual(body['generationConfig']['responseJsonSchema'], {'type': 'object'})
        self.assertEqual(body['systemInstruction']['parts'][0]['text'], 'Instrucción')
        self.assertIn('ñandú', body['contents'][0]['parts'][0]['text'])
        self.assertEqual(result.data, {'ok': True})

    def test_schema_field_none_sends_only_the_mime_type(self):
        opener = Opener(ok_body({'ok': True}))
        call(self.gemini(opener, schema_field='none'))
        config = json.loads(opener.requests[0][0].data)['generationConfig']
        self.assertNotIn('responseJsonSchema', config)
        self.assertEqual(config['responseMimeType'], 'application/json')

    def test_usage_counts_thoughts_and_ignores_thought_parts(self):
        result = call(self.gemini(Opener(ok_body({'ok': 1}, thought=True))))
        self.assertEqual(result.data, {'ok': 1})
        self.assertEqual((result.usage.tokens_in, result.usage.tokens_out), (120, 100))
        self.assertEqual(result.model, MODEL)

    def test_blocked_and_cut_responses(self):
        with self.assertRaises(AIBlockedError):
            call(self.gemini(Opener(ok_body({}, finish='SAFETY'))))
        with self.assertRaises(AIBlockedError):
            call(self.gemini(Opener({'promptFeedback': {'blockReason': 'SAFETY'}})))
        result = call(self.gemini(Opener(ok_body({}, finish='MAX_TOKENS'))))
        self.assertIsNone(result.data)
        self.assertEqual(result.finish_reason, 'MAX_TOKENS')

    def test_invalid_json_is_an_invalid_output(self):
        body = {'candidates': [{'content': {'parts': [{'text': 'no es json'}]}, 'finishReason': 'STOP'}]}
        with self.assertRaises(AIInvalidOutput):
            call(self.gemini(Opener(body)))
        with self.assertRaises(AIPermanentError):
            call(self.gemini(Opener({'candidates': []})))

    def test_http_errors_map_to_codes_without_leaking(self):
        cases = [(http_error(429, retry_after=7), AITransientError, 'ai_http_429'),
                 (http_error(503), AITransientError, 'ai_http_503'),
                 (http_error(400), AIPermanentError, 'ai_http_400'),
                 (http_error(403), AIPermanentError, 'ai_http_403'),
                 (socket.timeout('timed out'), AITransientError, 'ai_timeout'),
                 (urllib.error.URLError(socket.timeout()), AITransientError, 'ai_timeout'),
                 (urllib.error.URLError('dns'), AITransientError, 'ai_network')]
        for error, kind, code in cases:
            with self.subTest(code=code), self.assertRaises(kind) as ctx:
                call(self.gemini(Opener(error=error)))
            self.assertEqual(ctx.exception.code, code)
            self.assertEqual(str(ctx.exception), code)       # nada de la clave ni del cuerpo de Google
            self.assertIsNone(ctx.exception.__cause__)
        with self.assertRaises(AITransientError) as ctx:
            call(self.gemini(Opener(error=http_error(429, retry_after=7))))
        self.assertEqual(ctx.exception.retry_after, 7)

    def test_repr_hides_the_key_and_config_is_required(self):
        self.assertNotIn(KEY, repr(self.gemini(Opener())))
        with self.assertRaises(AIPermanentError):
            GeminiClient(api_key='', model=MODEL)
        with self.assertRaises(AIPermanentError):
            GeminiClient(api_key=KEY, model=MODEL, schema_field='responseSchema')


class ModeTests(SimpleTestCase):
    @override_settings(AI_PLANS_MODE='', DEBUG=False)
    def test_default_is_off_in_production(self):
        self.assertEqual(ai.ai_mode(), ai.MODE_OFF)
        self.assertFalse(ai.ai_enabled())
        with self.assertRaises(AIPermanentError):
            ai.get_ai_client()

    @override_settings(AI_PLANS_MODE='', DEBUG=True)
    def test_default_is_simulated_with_debug(self):
        self.assertIsInstance(ai.get_ai_client(), FakeAIClient)

    @override_settings(AI_PLANS_MODE='cualquiera')
    def test_unknown_mode_is_off(self):
        self.assertEqual(ai.ai_mode(), ai.MODE_OFF)

    @override_settings(AI_PLANS_MODE='live', GEMINI_API_KEY=KEY, GEMINI_MODEL=MODEL, GEMINI_BILLING_CONFIRMED=False)
    def test_live_without_billing_never_builds_a_client(self):
        with self.assertRaises(AIPermanentError):
            ai.get_ai_client()

    @override_settings(AI_PLANS_MODE='live', GEMINI_API_KEY=KEY, GEMINI_MODEL=MODEL, GEMINI_BILLING_CONFIRMED=True,
                       GEMINI_TIMEOUT_SECONDS=30, GEMINI_SCHEMA_FIELD='none')
    def test_live_configured(self):
        client = ai.get_ai_client()
        self.assertIsInstance(client, GeminiClient)
        self.assertEqual((client.model_name, client.timeout, client.schema_field), (MODEL, 30, 'none'))
        self.assertFalse(client.is_simulated)
