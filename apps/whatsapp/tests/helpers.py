"""Datos comunes de las pruebas de WhatsApp. Nunca se llama a Meta: `FakeWhatsAppClient` o un
`opener` falso hacen de servidor."""
import hashlib
import hmac
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import override_settings

from apps.accounts.tests.helpers import ScenarioTestCase
from apps.health import services as health_services
from apps.health.models import PURPOSE_WHATSAPP, ConsentTextVersion
from apps.memberships.models import Membership
from apps.whatsapp.client.fake import FakeWhatsAppClient
from apps.whatsapp.models import WhatsAppContact

BOGOTA = ZoneInfo('America/Bogota')
# Martes 17 de noviembre de 2026, 10:00 en Bogotá (dentro de la ventana; el lunes 16 es festivo)
NOW = datetime(2026, 11, 17, 10, 0, tzinfo=BOGOTA)
TODAY = NOW.date()
SECRET = 'secreto-de-app-inventado-para-pruebas'
VERIFY = 'v' * 40
WABA = '1000000000001'
PHONE_ID = '2000000000002'
PHONE = '+573001234567'

WA_SETTINGS = dict(
    WHATSAPP_CLOUD_MODE='simulated', WHATSAPP_CLOUD_APP_SECRET=SECRET, WHATSAPP_CLOUD_VERIFY_TOKEN=VERIFY,
    WHATSAPP_CLOUD_WABA_ID=WABA, WHATSAPP_CLOUD_PHONE_NUMBER_ID=PHONE_ID, WHATSAPP_CLOUD_SEND_INLINE=False,
    WHATSAPP_CLOUD_SITE_URL='https://profit.example.com', PRIVACY_POLICY_URL='https://profit.example.com/datos',
    WHATSAPP_CLOUD_LEGAL_REVIEWED=False, WHATSAPP_CLOUD_PAYMENT_NOTICE=False, WHATSAPP_CLOUD_DAILY_LIMIT=200,
    WHATSAPP_CLOUD_EXPIRING_DAYS=3,
)


def sign(raw):
    return 'sha256=' + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()


def envelope(value, waba=WABA, phone_id=PHONE_ID):
    value = {'messaging_product': 'whatsapp', 'metadata': {'phone_number_id': phone_id}, **value}
    return {'object': 'whatsapp_business_account',
            'entry': [{'id': waba, 'changes': [{'field': 'messages', 'value': value}]}]}


def status_event(wamid, status, ts=1700000000, ref=None, code=None, **extra):
    item = {'id': wamid, 'status': status, 'timestamp': str(ts), 'recipient_id': PHONE.lstrip('+'), **extra}
    if ref:
        item['biz_opaque_callback_data'] = str(ref)
    if code is not None:
        item['errors'] = [{'code': code, 'title': 'x', 'message': 'detalle con +573001234567'}]
    return envelope({'statuses': [item]})


def text_event(wamid, body, sender=PHONE.lstrip('+'), context_id=None, **extra):
    message = {'from': sender, 'id': wamid, 'timestamp': '1700000000', 'type': 'text', 'text': {'body': body},
               **extra}
    if context_id:
        message['context'] = {'id': context_id}
    return envelope({'messages': [message]})


def button_event(wamid, payload, context_id, sender=PHONE.lstrip('+'), text=''):
    return envelope({'messages': [{'from': sender, 'id': wamid, 'timestamp': '1700000000', 'type': 'button',
                                   'context': {'id': context_id}, 'button': {'payload': payload, 'text': text}}]})


def raw(data):
    return json.dumps(data).encode('utf-8')


def whatsapp_text():
    return ConsentTextVersion.objects.get(purpose=PURPOSE_WHATSAPP, is_current=True)


def publish_clean_text():
    """Versión sin marcadores (lo que haría la superusuaria tras la revisión del abogado)."""
    current = whatsapp_text()
    return ConsentTextVersion.objects.create(purpose=PURPOSE_WHATSAPP, version=current.version + 1,
                                             body='Texto revisado por el abogado.', is_current=True)


def activate(user, phone=PHONE):
    """Contacto confirmado + consentimiento vigente (atajo para las pruebas)."""
    record, _ = health_services.grant_consent(user, PURPOSE_WHATSAPP, whatsapp_text().version)
    contact, _ = WhatsAppContact.objects.update_or_create(
        user=user, defaults={'phone_e164': phone, 'status': WhatsAppContact.STATUS_ACTIVE, 'consent': record,
                             'confirmed_at': NOW})
    return contact


def membership(user, end_date, active=True):
    return Membership.objects.update_or_create(user=user, defaults={
        'start_date': end_date - timedelta(days=30), 'end_date': end_date, 'is_active': active})[0]


@override_settings(**WA_SETTINGS)
class WhatsAppTestCase(ScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeWhatsAppClient()


__all__ = ['NOW', 'TODAY', 'date']
