"""Regresiones de transporte Gemini: sin red y sin credenciales reales."""
import io
import json
import urllib.request
import urllib.response
from email.message import Message
from unittest import mock

from django.test import SimpleTestCase

from apps.plans.ai.base import AIInvalidOutput, AIPermanentError
from apps.plans.ai.gemini import GeminiClient, MAX_RESPONSE_BYTES


KEY = 'synthetic-secret-not-a-real-key'


def generate(client):
    return client.generate_json(task='meal_slot', system='Fictional data', payload={'test': True},
                                schema={'type': 'object'}, job_ref='synthetic')


def envelope(data):
    return json.dumps({'candidates': [{'finishReason': 'STOP', 'content': {
        'parts': [{'text': json.dumps(data)}]}}]}).encode('utf-8')


class GeminiHTTPSecurityTests(SimpleTestCase):
    def test_real_redirect_chain_never_contacts_the_second_destination(self):
        # Se ejercita la cadena real de urllib; solo el transporte está simulado.
        real_build = urllib.request.build_opener
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                requests = []

                class Transport(urllib.request.HTTPSHandler):
                    def https_open(self, request):
                        requests.append(request)
                        if len(requests) > 1:
                            raise AssertionError('Se intentó seguir la redirección')
                        headers = Message()
                        headers['Location'] = 'https://untrusted.invalid/collect'
                        response = urllib.response.addinfourl(
                            io.BytesIO(b'private upstream error'), headers, request.full_url, status)
                        response.msg = 'Redirect'
                        return response

                def build(*handlers):
                    return real_build(urllib.request.ProxyHandler({}), Transport(), *handlers)

                with mock.patch('apps.plans.ai.gemini.urllib.request.build_opener', side_effect=build):
                    client = GeminiClient(api_key=KEY, model='synthetic-model')
                with self.assertRaises(AIPermanentError) as ctx:
                    generate(client)
                self.assertEqual(ctx.exception.code, f'ai_http_{status}')
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].get_header('X-goog-api-key'), KEY)
                self.assertNotIn(KEY, str(ctx.exception))
                self.assertNotIn('private upstream', str(ctx.exception))

    def test_response_at_limit_is_accepted_and_stream_is_closed(self):
        raw = envelope({'ok': True})
        response = io.BytesIO(raw + b' ' * (MAX_RESPONSE_BYTES - len(raw)))
        client = GeminiClient(api_key=KEY, model='synthetic-model', opener=lambda *a, **k: response)
        self.assertEqual(generate(client).data, {'ok': True})
        self.assertTrue(response.closed)

    def test_oversized_response_is_bounded_and_not_parsed(self):
        class ObservedStream(io.BytesIO):
            requested_size = None
            bytes_read = None

            def read(self, size=-1):
                self.requested_size = size
                result = super().read(size)
                self.bytes_read = len(result)
                return result

        response = ObservedStream(b'x' * (MAX_RESPONSE_BYTES * 2))
        client = GeminiClient(api_key=KEY, model='synthetic-model', opener=lambda *a, **k: response)
        with mock.patch.object(client, 'parse_response') as parse:
            with self.assertRaises(AIPermanentError) as ctx:
                generate(client)
            parse.assert_not_called()
        self.assertEqual(ctx.exception.code, 'ai_response_too_large')
        self.assertEqual(response.requested_size, MAX_RESPONSE_BYTES + 1)
        self.assertEqual(response.bytes_read, MAX_RESPONSE_BYTES + 1)
        self.assertTrue(response.closed)

    def test_deep_json_has_a_sanitized_error_instead_of_a_worker_crash(self):
        nested = '[' * 2000 + '0' + ']' * 2000
        for raw, kind, code in (
            (nested.encode(), AIPermanentError, 'ai_unreadable_response'),
            (json.dumps({'candidates': [{'content': {'parts': [{'text': nested}]},
                                       'finishReason': 'STOP'}]}).encode(),
             AIInvalidOutput, 'ai_invalid_json'),
        ):
            with self.subTest(code=code):
                response = io.BytesIO(raw)
                client = GeminiClient(api_key=KEY, model='synthetic-model', opener=lambda *a, **k: response)
                with self.assertRaises(kind) as ctx:
                    generate(client)
                self.assertEqual(ctx.exception.code, code)
                self.assertEqual(str(ctx.exception), code)
                self.assertIsNone(ctx.exception.__cause__)
