import io
import json
import urllib.error
import urllib.request
import urllib.response
from email.message import Message
from unittest import mock

from django.test import SimpleTestCase

from apps.whatsapp.client.base import WhatsAppAPIError
from apps.whatsapp.client.live import CloudAPIClient, MAX_RESPONSE_BYTES

TOKEN = 'synthetic-not-a-real-token'


def client(opener=None):
    return CloudAPIClient(token=TOKEN, phone_number_id='123', graph_version='v27.0', opener=opener)


class WhatsAppHTTPSecurityTests(SimpleTestCase):
    def test_redirect_chain_never_contacts_a_second_destination(self):
        real_build = urllib.request.build_opener
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                requests = []

                class Transport(urllib.request.HTTPSHandler):
                    def https_open(self, request):
                        requests.append(request)
                        if len(requests) > 1:
                            raise AssertionError('No debe contactar el destino secundario')
                        headers = Message()
                        headers['Location'] = 'https://untrusted.invalid/'
                        response = urllib.response.addinfourl(io.BytesIO(b'private provider data'), headers,
                                                             request.full_url, status)
                        response.msg = 'Redirect'
                        return response

                def build(*handlers):
                    return real_build(urllib.request.ProxyHandler({}), Transport(), *handlers)

                with mock.patch('apps.whatsapp.client.live.urllib.request.build_opener', side_effect=build):
                    api = client()
                with self.assertRaises(WhatsAppAPIError) as ctx:
                    api._request('POST', '123/messages', body={})
                self.assertEqual(ctx.exception.code, f'http_{status}')
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].get_header('Authorization'), 'Bearer ' + TOKEN)
                self.assertNotIn(TOKEN, str(ctx.exception))
                self.assertNotIn('private provider', str(ctx.exception))

    def test_oversized_success_is_bounded_and_closed(self):
        class Stream(io.BytesIO):
            read_size = None

            def read(self, size=-1):
                self.read_size = size
                return super().read(size)

        response = Stream(b'x' * (MAX_RESPONSE_BYTES * 2))
        with self.assertRaises(WhatsAppAPIError) as ctx:
            client(lambda *a, **k: response)._request('GET', '123')
        self.assertEqual(ctx.exception.code, 'response_too_large')
        self.assertEqual(response.read_size, MAX_RESPONSE_BYTES + 1)
        self.assertTrue(response.closed)

    def test_oversized_error_is_bounded_closed_and_sanitized(self):
        response = io.BytesIO(b'x' * (MAX_RESPONSE_BYTES * 2))
        error = urllib.error.HTTPError('https://provider.invalid/', 503, 'private', Message(), response)
        api = client(mock.Mock(side_effect=error))
        with self.assertRaises(WhatsAppAPIError) as ctx:
            api._request('GET', '123')
        self.assertEqual(ctx.exception.code, 'response_too_large')
        self.assertEqual(ctx.exception.http_status, 503)
        self.assertTrue(response.closed)

    def test_response_at_limit_is_accepted(self):
        raw = b'{"ok": true}'
        response = io.BytesIO(raw + b' ' * (MAX_RESPONSE_BYTES - len(raw)))
        self.assertEqual(client(lambda *a, **k: response)._request('GET', '123'), {'ok': True})

    def test_nested_json_and_non_object_have_controlled_errors(self):
        for body in (b'[]', b'null', b'[' * 2000 + b'0' + b']' * 2000):
            with self.subTest(size=len(body)):
                response = io.BytesIO(body)
                with self.assertRaises(WhatsAppAPIError) as ctx:
                    client(lambda *a, **k: response)._request('GET', '123')
                self.assertEqual(ctx.exception.code, 'unreadable_response')

    def test_malformed_error_metadata_does_not_escape(self):
        for body in (b'[' * 2000 + b'0' + b']' * 2000, b'{"error":{"code":1e309}}'):
            error = urllib.error.HTTPError('https://provider.invalid/', 400, 'private', Message(), io.BytesIO(body))
            with self.assertRaises(WhatsAppAPIError) as ctx:
                client(mock.Mock(side_effect=error))._request('GET', '123')
            self.assertEqual(ctx.exception.code, 'http_400')
