"""Cliente `live` sin red (un `opener` falso hace de servidor) y selección del cliente por modo."""
import io
import json
import socket
import urllib.error
from email.message import Message

from django.test import SimpleTestCase, override_settings

from apps.whatsapp.client import WhatsAppAPIError, get_client
from apps.whatsapp.client import base
from apps.whatsapp.client.fake import FakeWhatsAppClient
from apps.whatsapp.client.live import GRAPH_ROOT, CloudAPIClient

TOKEN = 'EAAtokenInventadoParaPruebas0123456789'


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class Opener:
    def __init__(self, body=None, error=None):
        self.body, self.error, self.requests = body, error, []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        if self.error is not None:
            raise self.error
        return FakeResponse(json.dumps(self.body).encode())


def http_error(status, body, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers['Retry-After'] = str(retry_after)
    return urllib.error.HTTPError('https://x', status, 'error', headers, io.BytesIO(json.dumps(body).encode()))


OK = {'messaging_product': 'whatsapp', 'contacts': [{'input': '+573001234567', 'wa_id': '573001234567'}],
      'messages': [{'id': 'wamid.ABC'}]}


class CloudClientTests(SimpleTestCase):
    def client_for(self, opener):
        return CloudAPIClient(token=TOKEN, phone_number_id='2000', graph_version='v27.0', timeout=10, opener=opener)

    def test_template_request_shape_and_token_only_in_header(self):
        opener = Opener(OK)
        result = self.client_for(opener).send_template(
            to='+573001234567', template_name='profit_plan_aprobado_v1', language='es_CO',
            body_params=[('nombre', 'Ana')], button_payloads=((0, 'optout'),), ref='ref-1')
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, f'{GRAPH_ROOT}/v27.0/2000/messages')
        self.assertNotIn(TOKEN, request.full_url)
        self.assertEqual(request.get_header('Authorization'), f'Bearer {TOKEN}')
        self.assertEqual(timeout, 10)
        body = json.loads(request.data)
        self.assertEqual(body['type'], 'template')
        self.assertEqual(body['to'], '+573001234567')
        self.assertEqual(body['biz_opaque_callback_data'], 'ref-1')
        self.assertEqual(body['template']['language'], {'code': 'es_CO'})
        self.assertEqual(body['template']['components'][0]['parameters'],
                         [{'type': 'text', 'parameter_name': 'nombre', 'text': 'Ana'}])
        self.assertEqual(body['template']['components'][1],
                         {'type': 'button', 'sub_type': 'quick_reply', 'index': '0',
                          'parameters': [{'type': 'payload', 'payload': 'optout'}]})
        self.assertEqual((result.wamid, result.wa_id), ('wamid.ABC', '573001234567'))
        self.assertNotIn(TOKEN, repr(self.client_for(opener)))

    def test_text_request(self):
        opener = Opener(OK)
        self.client_for(opener).send_text(to='+573001234567', text='Hola', ref='r')
        body = json.loads(opener.requests[0][0].data)
        self.assertEqual(body['text'], {'body': 'Hola', 'preview_url': False})

    def test_errors_keep_only_codes(self):
        cases = [
            (http_error(400, {'error': {'code': 132001, 'message': 'plantilla +573001234567'}}), 'meta_132001', base.TEMPLATE),
            (http_error(401, {'error': {'code': 190}}), 'meta_190', base.ACCOUNT),
            (http_error(503, {}), 'http_503', base.TRANSIENT),
            (socket.timeout(), 'timeout', base.TRANSIENT),
            (urllib.error.URLError('dns'), 'network', base.TRANSIENT),
        ]
        for error, code, kind in cases:
            with self.subTest(code=code), self.assertRaises(WhatsAppAPIError) as ctx:
                self.client_for(Opener(error=error)).send_text(to='+57300', text='x', ref='r')
            self.assertEqual((ctx.exception.code, ctx.exception.kind), (code, kind))
            self.assertNotIn('573001234567', str(ctx.exception))

    def test_retry_after_is_read(self):
        with self.assertRaises(WhatsAppAPIError) as ctx:
            self.client_for(Opener(error=http_error(429, {'error': {'code': 130429}}, retry_after=40))) \
                .send_text(to='x', text='x', ref='r')
        self.assertEqual(ctx.exception.retry_after, 40)

    def test_unreadable_response(self):
        with self.assertRaises(WhatsAppAPIError):
            self.client_for(Opener({'nada': 1})).send_text(to='x', text='x', ref='r')


class GetClientTests(SimpleTestCase):
    @override_settings(WHATSAPP_CLOUD_MODE='simulated')
    def test_simulated(self):
        self.assertIsInstance(get_client(), FakeWhatsAppClient)

    @override_settings(WHATSAPP_CLOUD_MODE='off')
    def test_off(self):
        with self.assertRaises(WhatsAppAPIError):
            get_client()

    @override_settings(WHATSAPP_CLOUD_MODE='live', WHATSAPP_CLOUD_ACCESS_TOKEN='')
    def test_live_incomplete(self):
        with self.assertRaises(WhatsAppAPIError):
            get_client()

    @override_settings(WHATSAPP_CLOUD_MODE='live', WHATSAPP_CLOUD_ACCESS_TOKEN=TOKEN, WHATSAPP_CLOUD_PHONE_NUMBER_ID='1',
                       WHATSAPP_CLOUD_WABA_ID='2', WHATSAPP_CLOUD_APP_SECRET='s' * 20,
                       WHATSAPP_CLOUD_VERIFY_TOKEN='v' * 40, WHATSAPP_CLOUD_GRAPH_VERSION='v27.0')
    def test_live_complete(self):
        self.assertIsInstance(get_client(), CloudAPIClient)
