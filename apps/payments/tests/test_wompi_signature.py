"""Checksum de eventos de Wompi y firma de integridad del checkout (sin red).

Algoritmo oficial (https://docs.wompi.co/docs/colombia/eventos/):
SHA256( valores de signature.properties + timestamp + secreto de eventos ).
"""
import copy
import hashlib

from django.test import SimpleTestCase

from apps.payments.services import (
    WompiConfigError, compute_integrity_signature, verify_webhook_event,
)

SECRET = 'test_events_secretXYZ'
TIMESTAMP = 1530291411


def sign(payload, secret=SECRET, include_timestamp=True):
    """Calcula el checksum como lo haría Wompi y lo escribe en el payload."""
    data = payload['data']
    parts = []
    for prop in payload['signature']['properties']:
        value = data
        for key in prop.split('.'):
            value = value[key]
        parts.append(str(value))
    raw = ''.join(parts) + (str(payload['timestamp']) if include_timestamp else '') + secret
    payload['signature']['checksum'] = hashlib.sha256(raw.encode()).hexdigest().upper()
    return payload


def make_event(**tx_overrides):
    tx = {
        'id': '1234-1610641025-49201',
        'amount_in_cents': 4490000,
        'reference': 'PROFIT-ABC123',
        'currency': 'COP',
        'payment_method_type': 'NEQUI',
        'status': 'APPROVED',
    }
    tx.update(tx_overrides)
    return sign({
        'event': 'transaction.updated',
        'data': {'transaction': tx},
        'environment': 'test',
        'signature': {
            'properties': ['transaction.id', 'transaction.status', 'transaction.amount_in_cents'],
            'checksum': '',
        },
        'timestamp': TIMESTAMP,
        'sent_at': '2026-10-08T16:45:05.000Z',
    })


class VerifyWebhookEventTests(SimpleTestCase):
    def test_valid_checksum(self):
        self.assertTrue(verify_webhook_event(make_event(), SECRET))

    def test_checksum_includes_timestamp(self):
        """La hipótesis confirmada en la doc: sin el timestamp el checksum NO es válido."""
        event = sign(make_event(), include_timestamp=False)
        self.assertFalse(verify_webhook_event(event, SECRET))

    def test_altered_timestamp_rejected(self):
        event = make_event()
        event['timestamp'] = TIMESTAMP + 1
        self.assertFalse(verify_webhook_event(event, SECRET))

    def test_missing_timestamp_rejected(self):
        event = make_event()
        del event['timestamp']
        self.assertFalse(verify_webhook_event(event, SECRET))

    def test_invalid_checksum_rejected(self):
        event = make_event()
        event['signature']['checksum'] = 'A' * 64
        self.assertFalse(verify_webhook_event(event, SECRET))

    def test_wrong_secret_rejected(self):
        self.assertFalse(verify_webhook_event(make_event(), 'otro_secreto'))

    def test_empty_secret_always_rejected(self):
        """Aunque el atacante calcule el checksum con secreto vacío, se rechaza."""
        event = sign(make_event(), secret='')
        self.assertFalse(verify_webhook_event(event, ''))
        self.assertFalse(verify_webhook_event(make_event(), ''))
        self.assertFalse(verify_webhook_event(make_event(), None))

    def test_altered_signed_field_rejected(self):
        for field, value in (('status', 'DECLINED'), ('amount_in_cents', 1), ('id', 'otro-id')):
            event = make_event()
            event['data']['transaction'][field] = value
            self.assertFalse(verify_webhook_event(event, SECRET), field)

    def test_missing_signature_parts_rejected(self):
        for mutate in (
            lambda e: e.pop('signature'),
            lambda e: e['signature'].pop('checksum'),
            lambda e: e['signature'].update(properties=[]),
            lambda e: e['signature'].pop('properties'),
            lambda e: e['signature'].update(properties=['transaction.no_existe']),
            lambda e: e.pop('data'),
        ):
            event = make_event()
            mutate(event)
            self.assertFalse(verify_webhook_event(event, SECRET))

    def test_non_dict_payload_rejected(self):
        for bad in ([], 'x', None, 5):
            self.assertFalse(verify_webhook_event(bad, SECRET))

    def test_checksum_case_insensitive(self):
        event = make_event()
        event['signature']['checksum'] = event['signature']['checksum'].lower()
        self.assertTrue(verify_webhook_event(event, SECRET))

    def test_header_checksum_must_match_when_present(self):
        event = make_event()
        good = event['signature']['checksum']
        self.assertTrue(verify_webhook_event(event, SECRET, good))
        self.assertFalse(verify_webhook_event(event, SECRET, 'B' * 64))

    def test_properties_follow_event_order(self):
        """Las propiedades no son un arreglo fijo: se usan en el orden que trae el evento."""
        event = make_event()
        event['signature']['properties'] = ['transaction.status', 'transaction.id']
        sign(event)
        self.assertTrue(verify_webhook_event(event, SECRET))

    def test_verification_does_not_mutate_payload(self):
        event = make_event()
        before = copy.deepcopy(event)
        verify_webhook_event(event, SECRET)
        self.assertEqual(event, before)


class IntegritySignatureTests(SimpleTestCase):
    def test_matches_wompi_documentation_example(self):
        # Ejemplo de https://docs.wompi.co/docs/colombia/widget-checkout-web/ (consultado 2026-10-08)
        sig = compute_integrity_signature(
            'sk8-438k4-xmxm392-sn2m', 2490000, 'COP', 'prod_integrity_Z5mMke9x0k8gpErbDqwrJXMqsI6SFli6',
        )
        self.assertEqual(sig, '37c8407747e595535433ef8f6a811d853cd943046624a0ec04662b17bbf33bf5')

    def test_empty_secret_fails_clearly(self):
        with self.assertRaises(WompiConfigError):
            compute_integrity_signature('REF', 100, 'COP', '')
