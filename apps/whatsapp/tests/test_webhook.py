"""Webhook: límites, idempotencia, estados y respuestas (la firma y el challenge básicos están en
`test_webhook_security.py`)."""
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.urls import reverse

from apps.plans.models import Plan
from apps.whatsapp import queue, services, views
from apps.whatsapp.models import (
    KIND_AUTOREPLY, KIND_CONFIRM_ACK, KIND_OPTIN_CONFIRM, KIND_OPTOUT_ACK, KIND_PLAN_APPROVED, WhatsAppContact,
    WhatsAppInbound, WhatsAppMessage,
)

from .helpers import (
    NOW, PHONE, SECRET, WhatsAppTestCase, activate, button_event, envelope, raw, sign, status_event, text_event,
    whatsapp_text,
)

M = WhatsAppMessage


class WebhookTestCase(WhatsAppTestCase):
    url = reverse('whatsapp_webhook')

    def post(self, data, signature=None):
        body = raw(data) if not isinstance(data, bytes) else data
        return self.client.post(self.url, body, content_type='application/json',
                                HTTP_X_HUB_SIGNATURE_256=signature or sign(body))

    def accepted_plan_message(self, wamid='wamid.OUT1'):
        activate(self.client_a)
        plan = Plan.objects.create(client=self.client_a, kind='exercise', version=1, status=Plan.STATUS_APPROVED)
        message, _ = services.enqueue(self.client_a, KIND_PLAN_APPROVED, f'plan:{wamid}', subject_type=M.SUBJECT_PLAN,
                                      subject_id=plan.pk, kick=False)
        M.objects.filter(pk=message.pk).update(status=M.STATUS_ACCEPTED, wamid=wamid, attempts=1)
        return M.objects.get(pk=message.pk)


class LimitsTests(WebhookTestCase):
    def test_secret_missing_503_and_off_404(self):
        with override_settings(WHATSAPP_CLOUD_APP_SECRET=''):
            self.assertEqual(self.post(envelope({})).status_code, 503)
        with override_settings(WHATSAPP_CLOUD_VERIFY_TOKEN=''):
            self.assertEqual(self.client.get(self.url, {'hub.mode': 'subscribe'}).status_code, 503)
        with override_settings(WHATSAPP_CLOUD_MODE='off'):
            self.assertEqual(self.post(envelope({})).status_code, 404)

    def test_too_large_and_signed_garbage(self):
        with mock.patch.object(views, 'MAX_WEBHOOK_BYTES', 10):
            self.assertEqual(self.post(envelope({})).status_code, 413)
        self.assertEqual(self.post(b'no es json').status_code, 200)

    def test_other_phone_number_or_account_ignored(self):
        message = self.accepted_plan_message()
        for data in (envelope({'statuses': [{'id': 'wamid.OUT1', 'status': 'delivered'}]}, phone_id='999'),
                     envelope({'statuses': [{'id': 'wamid.OUT1', 'status': 'delivered'}]}, waba='999')):
            self.assertEqual(self.post(data).status_code, 200)
        self.assertEqual(M.objects.get(pk=message.pk).status, M.STATUS_ACCEPTED)

    def test_get_missing_params(self):
        self.assertEqual(self.client.get(self.url).status_code, 400)

    def test_csrf_still_required_elsewhere(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.client_a, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(strict.post(reverse('whatsapp_disable')).status_code, 403)
        body = raw(envelope({}))
        self.assertEqual(strict.post(self.url, body, content_type='application/json',
                                     HTTP_X_HUB_SIGNATURE_256=sign(body)).status_code, 200)


class StatusTests(WebhookTestCase):
    def test_progress_only_moves_forward_and_is_idempotent(self):
        message = self.accepted_plan_message()
        pricing = {'category': 'utility', 'type': 'regular', 'pricing_model': 'PMP'}
        self.post(status_event('wamid.OUT1', 'read', pricing=pricing))
        self.post(status_event('wamid.OUT1', 'delivered'))
        self.post(status_event('wamid.OUT1', 'read', pricing=pricing))
        message.refresh_from_db()
        self.assertEqual((message.status, message.pricing_category, message.pricing_type),
                         (M.STATUS_READ, 'utility', 'regular'))
        self.assertIsNotNone(message.delivered_at)

    def test_failed_status_applies_error_table(self):
        message = self.accepted_plan_message()
        with self.assertLogs('apps.whatsapp', 'INFO') as logs:
            self.post(status_event('wamid.OUT1', 'failed', code=131026))
        message.refresh_from_db()
        self.assertEqual((message.status, message.error_code), (M.STATUS_FAILED, 'meta_131026'))
        self.assertNotIn(PHONE.lstrip('+'), '\n'.join(logs.output))
        self.post(status_event('wamid.OUT1', 'failed', code=131026))      # repetido: no cuenta dos veces
        self.assertEqual(WhatsAppContact.objects.get(user=self.client_a).undeliverable_count, 1)

    def test_transient_async_failure_requeues(self):
        message = self.accepted_plan_message()
        self.post(status_event('wamid.OUT1', 'failed', code=131000))
        message.refresh_from_db()
        self.assertEqual((message.status, message.wamid), (M.STATUS_QUEUED, None))

    def test_reconciliation_by_ref_when_wamid_was_not_saved(self):
        message = self.accepted_plan_message()
        M.objects.filter(pk=message.pk).update(status=M.STATUS_SENDING, wamid=None)
        self.post(status_event('wamid.LATE', 'sent', ref=message.ref, recipient_user_id='CO.123abc'))
        message.refresh_from_db()
        self.assertEqual((message.status, message.wamid), (M.STATUS_SENT, 'wamid.LATE'))
        self.assertEqual(WhatsAppContact.objects.get(user=self.client_a).bsuid, 'CO.123abc')

    def test_unknown_message_ignored(self):
        self.assertEqual(self.post(status_event('wamid.NADA', 'delivered')).status_code, 200)


class InboundTests(WebhookTestCase):
    def pending_with_optin(self):
        services.enable(self.client_a, PHONE, whatsapp_text().version)
        optin = M.objects.get(kind=KIND_OPTIN_CONFIRM)
        M.objects.filter(pk=optin.pk).update(status=M.STATUS_ACCEPTED, wamid='wamid.OPTIN')
        return optin

    def test_confirm_button_activates_and_thanks(self):
        self.pending_with_optin()
        self.post(button_event('wamid.IN1', 'confirm', 'wamid.OPTIN'))
        self.post(button_event('wamid.IN1', 'confirm', 'wamid.OPTIN'))     # reintento de Meta
        contact = WhatsAppContact.objects.get(user=self.client_a)
        self.assertEqual(contact.status, WhatsAppContact.STATUS_ACTIVE)
        self.assertEqual(M.objects.filter(kind=KIND_CONFIRM_ACK).count(), 1)
        self.assertEqual(WhatsAppInbound.objects.count(), 1)
        self.assertTrue(services.can_receive(self.client_a))

    def test_confirm_by_button_text_without_payload(self):
        self.pending_with_optin()
        self.post(button_event('wamid.IN9', '', 'wamid.OPTIN', text='Sí, soy yo'))
        self.assertEqual(WhatsAppContact.objects.get(user=self.client_a).status, WhatsAppContact.STATUS_ACTIVE)

    def test_not_me_marks_wrong_number(self):
        self.pending_with_optin()
        self.post(button_event('wamid.IN2', 'not_me', 'wamid.OPTIN'))
        self.assertEqual(WhatsAppContact.objects.get(user=self.client_a).status, WhatsAppContact.STATUS_WRONG_NUMBER)
        self.assertFalse(services.can_receive(self.client_a))

    def test_optout_button_and_text(self):
        message = self.accepted_plan_message()
        services.enqueue(self.client_a, KIND_PLAN_APPROVED, 'pendiente', kick=False)
        self.post(button_event('wamid.IN3', 'optout', message.wamid))
        contact = WhatsAppContact.objects.get(user=self.client_a)
        self.assertEqual(contact.status, WhatsAppContact.STATUS_OPTED_OUT)
        self.assertFalse(services.consent_valid(self.client_a))
        self.assertEqual(M.objects.get(dedupe_key='pendiente').status, M.STATUS_CANCELLED)
        self.assertEqual(M.objects.filter(kind=KIND_OPTOUT_ACK).count(), 1)
        self.post(text_event('wamid.IN4', 'BAJA'))        # ya dado de baja: sin segundo acuse
        self.assertEqual(M.objects.filter(kind=KIND_OPTOUT_ACK).count(), 1)

    def test_text_stop_from_phone(self):
        activate(self.client_a)
        self.post(text_event('wamid.IN5', ' Stop. '))
        self.assertEqual(WhatsAppContact.objects.get(user=self.client_a).status, WhatsAppContact.STATUS_OPTED_OUT)

    def test_other_text_gets_one_autoreply_and_is_not_stored(self):
        activate(self.client_a)
        self.post(text_event('wamid.IN6', 'no puedo parar de entrenar'))
        self.post(text_event('wamid.IN7', 'hola otra vez'))
        self.assertEqual(WhatsAppContact.objects.get(user=self.client_a).status, WhatsAppContact.STATUS_ACTIVE)
        self.assertEqual(M.objects.filter(kind=KIND_AUTOREPLY).count(), 1)
        self.assertEqual(set(WhatsAppInbound.objects.values_list('kind', flat=True)), {'other'})
        fields = {f.name for f in WhatsAppInbound._meta.get_fields()}
        self.assertFalse({'body', 'text', 'phone'} & fields)

    def test_sender_identified_by_bsuid_only(self):
        contact = activate(self.client_a)
        WhatsAppContact.objects.filter(pk=contact.pk).update(bsuid='CO.999xyz')
        data = envelope({'messages': [{'from_user_id': 'CO.999xyz', 'id': 'wamid.IN8', 'type': 'text',
                                       'text': {'body': 'baja'}}]})
        self.post(data)
        self.assertEqual(WhatsAppContact.objects.get(pk=contact.pk).status, WhatsAppContact.STATUS_OPTED_OUT)

    def test_unknown_sender_not_stored(self):
        self.post(text_event('wamid.X', 'hola', sender='573999999999'))
        self.assertFalse(WhatsAppInbound.objects.exists())
        self.assertFalse(M.objects.exists())

    def test_service_reply_is_sent_within_window(self):
        activate(self.client_a)
        self.post(text_event('wamid.IN10', 'hola'))
        reply = M.objects.get(kind=KIND_AUTOREPLY)
        self.assertEqual(queue.process_one(reply.pk, client=self.fake), M.STATUS_ACCEPTED)
        self.assertIn('BAJA', self.fake.calls[0]['text'])
        late = M.objects.create(recipient=self.client_a, kind=KIND_AUTOREPLY, dedupe_key='late',
                                next_attempt_at=NOW, expires_at=NOW + timedelta(days=9))
        WhatsAppContact.objects.filter(user=self.client_a).update(last_inbound_at=NOW - timedelta(days=2))
        self.assertEqual(queue.process_one(late.pk, now=NOW, client=self.fake), M.STATUS_EXPIRED)

    def test_signature_uses_raw_bytes(self):
        body = raw(envelope({}))
        self.assertEqual(self.post(body + b' ', signature=sign(body)).status_code, 401)
        self.assertNotEqual(SECRET, '')
