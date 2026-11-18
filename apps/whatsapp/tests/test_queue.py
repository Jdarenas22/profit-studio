"""Cola: envío, ventana horaria, tope diario, cancelaciones, reintentos, pausas y recuperación."""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings

from apps.plans.models import Plan
from apps.whatsapp import queue, services
from apps.whatsapp.client import WhatsAppAPIError
from apps.whatsapp.client.fake import FakeWhatsAppClient
from apps.whatsapp.models import (
    KIND_MEMBERSHIP_EXPIRING, KIND_OPTIN_CONFIRM, KIND_PLAN_APPROVED, WhatsAppChannelState, WhatsAppContact,
    WhatsAppMessage,
)

from .helpers import NOW, PHONE, TODAY, WhatsAppTestCase, activate, membership, whatsapp_text

M = WhatsAppMessage


def api_error(code, retry_after=None):
    return WhatsAppAPIError(f'meta_{code}', meta_code=code, http_status=400, retry_after=retry_after)


class QueueTestCase(WhatsAppTestCase):
    def setUp(self):
        super().setUp()
        self.contact = activate(self.client_a)
        self.plan_obj = Plan.objects.create(client=self.client_a, kind='exercise', version=1,
                                            status=Plan.STATUS_APPROVED)

    def queue_plan(self, key='plan:1'):
        message, _ = services.enqueue(self.client_a, KIND_PLAN_APPROVED, key, subject_type=M.SUBJECT_PLAN,
                                      subject_id=self.plan_obj.pk, now=NOW - timedelta(minutes=1), kick=False)
        return message

    def run_one(self, message, client=None, now=NOW):
        return queue.process_one(message.pk, now=now, client=client or self.fake)


class SendTests(QueueTestCase):
    def test_success_sends_name_and_marks_accepted(self):
        message = self.queue_plan()
        self.assertEqual(self.run_one(message), M.STATUS_ACCEPTED)
        message.refresh_from_db()
        self.assertEqual((message.status, message.attempts), (M.STATUS_ACCEPTED, 1))
        self.assertTrue(message.wamid.startswith('wamid.SIMULATED'))
        call = self.fake.calls[0]
        self.assertEqual((call['to'], call['template_name'], call['language']),
                         (PHONE, 'profit_plan_aprobado_v1', 'es_CO'))
        self.assertEqual(call['body_params'], [('nombre', 'Client_a')])
        self.assertEqual(call['button_payloads'], [(0, 'optout')])
        self.assertEqual(call['ref'], str(message.ref))
        self.assertEqual(self.run_one(message), 'skipped')   # no se reenvía

    def test_outside_window_defers_without_counting_attempt(self):
        message = self.queue_plan()
        sunday = NOW + timedelta(days=5)          # domingo 22
        self.assertEqual(self.run_one(message, now=sunday), 'deferred')
        message.refresh_from_db()
        self.assertEqual((message.status, message.attempts, message.error_code), (M.STATUS_QUEUED, 0, 'outside_hours'))
        self.assertEqual(message.next_attempt_at.isoformat()[:16], '2026-11-23T12:00')   # lunes 7:00 Bogotá
        self.assertEqual(self.fake.calls, [])

    def test_daily_limit(self):
        with override_settings(WHATSAPP_CLOUD_DAILY_LIMIT=1):
            self.assertEqual(self.run_one(self.queue_plan('plan:a')), M.STATUS_ACCEPTED)
            self.assertEqual(self.run_one(self.queue_plan('plan:b')), 'deferred')

    def test_cancelled_when_consent_revoked_or_plan_withdrawn(self):
        message = self.queue_plan()
        Plan.objects.filter(pk=self.plan_obj.pk).update(status=Plan.STATUS_ARCHIVED)
        self.assertEqual(self.run_one(message), M.STATUS_CANCELLED)
        self.assertEqual(M.objects.get(pk=message.pk).error_code, 'subject_changed')
        Plan.objects.filter(pk=self.plan_obj.pk).update(status=Plan.STATUS_APPROVED)
        other = self.queue_plan('plan:2')
        from apps.health import services as health_services
        health_services.revoke_consent(self.client_a, 'whatsapp_notices')
        self.assertEqual(self.run_one(other), M.STATUS_CANCELLED)
        self.assertEqual(M.objects.get(pk=other.pk).error_code, 'no_consent')
        self.assertEqual(self.fake.calls, [])

    def test_membership_renewed_cancels(self):
        m = membership(self.client_a, TODAY + timedelta(days=2))
        message, _ = services.enqueue(self.client_a, KIND_MEMBERSHIP_EXPIRING, 'mexp', subject_type=M.SUBJECT_MEMBERSHIP,
                                      subject_id=m.pk, subject_date=m.end_date, now=NOW - timedelta(minutes=1),
                                      kick=False)
        membership(self.client_a, TODAY + timedelta(days=30))
        self.assertEqual(self.run_one(message), M.STATUS_CANCELLED)

    def test_membership_expiring_params(self):
        m = membership(self.client_a, TODAY + timedelta(days=2))
        message, _ = services.enqueue(self.client_a, KIND_MEMBERSHIP_EXPIRING, 'mexp', subject_type=M.SUBJECT_MEMBERSHIP,
                                      subject_id=m.pk, subject_date=m.end_date, now=NOW - timedelta(minutes=1),
                                      kick=False)
        self.run_one(message)
        self.assertEqual(self.fake.calls[0]['body_params'], [('nombre', 'Client_a'), ('fecha', '19 de noviembre de 2026')])

    def test_optin_goes_to_pending_number_with_two_buttons(self):
        services.enable(self.client_b, '311 222 3333', whatsapp_text().version, now=NOW - timedelta(minutes=1))
        message = M.objects.get(kind=KIND_OPTIN_CONFIRM)
        self.assertEqual(self.run_one(message), M.STATUS_ACCEPTED)
        call = self.fake.calls[0]
        self.assertEqual((call['to'], call['body_params'], call['button_payloads']),
                         ('+573112223333', [], [(0, 'confirm'), (1, 'not_me')]))
        self.assertIsNotNone(WhatsAppContact.objects.get(user=self.client_b).confirmation_sent_at)


class ErrorTests(QueueTestCase):
    def test_transient_retries_with_backoff_then_fails(self):
        message = self.queue_plan()
        client = FakeWhatsAppClient(fail_with=api_error(131000))
        self.assertEqual(self.run_one(message, client), 'retry')
        message.refresh_from_db()
        self.assertEqual((message.status, message.error_code), (M.STATUS_QUEUED, 'meta_131000'))
        wait = (message.next_attempt_at - NOW).total_seconds()
        self.assertTrue(48 <= wait <= 72, wait)
        M.objects.filter(pk=message.pk).update(attempts=5, next_attempt_at=NOW)
        message.refresh_from_db()
        M.objects.filter(pk=message.pk).update(attempts=4)
        self.assertEqual(self.run_one(message, client), M.STATUS_FAILED)

    def test_retry_after_is_respected(self):
        message = self.queue_plan()
        self.run_one(message, FakeWhatsAppClient(fail_with=api_error(130429, retry_after=900)))
        message.refresh_from_db()
        self.assertGreaterEqual((message.next_attempt_at - NOW).total_seconds(), 900)

    def test_undeliverable_twice_pauses_contact(self):
        client = FakeWhatsAppClient(fail_with=[api_error(131026), api_error(131026)])
        self.assertEqual(self.run_one(self.queue_plan('a'), client), M.STATUS_FAILED)
        self.assertEqual(WhatsAppContact.objects.get(pk=self.contact.pk).status, WhatsAppContact.STATUS_ACTIVE)
        self.run_one(self.queue_plan('b'), client)
        self.assertEqual(WhatsAppContact.objects.get(pk=self.contact.pk).status, WhatsAppContact.STATUS_UNDELIVERABLE)

    def test_template_error_pauses_only_that_template(self):
        message = self.queue_plan()
        self.assertEqual(self.run_one(message, FakeWhatsAppClient(fail_with=api_error(132001))), M.STATUS_FAILED)
        state = WhatsAppChannelState.get()
        self.assertFalse(state.paused)
        self.assertEqual(state.paused_templates, ['profit_plan_aprobado_v1'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn(PHONE, mail.outbox[0].body)
        self.assertEqual(self.run_one(self.queue_plan('otro')), 'deferred')

    def test_account_error_pauses_channel_and_keeps_message(self):
        message = self.queue_plan()
        self.assertEqual(self.run_one(message, FakeWhatsAppClient(fail_with=api_error(190))), 'retry')
        message.refresh_from_db()
        self.assertEqual((message.status, message.attempts), (M.STATUS_QUEUED, 0))
        self.assertTrue(WhatsAppChannelState.get().paused)
        self.assertEqual(self.run_one(message), 'paused')
        self.run_one(self.queue_plan('x'), FakeWhatsAppClient(fail_with=api_error(190)))
        self.assertEqual(len(mail.outbox), 1)     # una sola alerta por motivo cada 24 h

    def test_expired_message(self):
        message = self.queue_plan()
        self.assertEqual(self.run_one(message, now=NOW + timedelta(days=8)), M.STATUS_EXPIRED)


class RecoveryTests(QueueTestCase):
    def test_stale_sending_goes_back_to_queue(self):
        message = self.queue_plan()
        M.objects.filter(pk=message.pk).update(status=M.STATUS_SENDING, locked_at=NOW - timedelta(minutes=10))
        self.assertEqual(queue.recover_stale(NOW), 0)
        M.objects.filter(pk=message.pk).update(locked_at=NOW - timedelta(minutes=41))
        self.assertEqual(queue.recover_stale(NOW), 1)
        self.assertEqual(M.objects.get(pk=message.pk).status, M.STATUS_QUEUED)

    def test_claim_only_once(self):
        message = self.queue_plan()
        self.assertTrue(queue.claim(message.pk, NOW))
        self.assertFalse(queue.claim(message.pk, NOW))

    def test_process_due_and_purge(self):
        self.queue_plan('a')
        self.queue_plan('b')
        with mock.patch.object(queue, '_sleep') as sleep:
            summary = queue.process_due(now=NOW, client=self.fake)
        self.assertEqual(summary, {M.STATUS_ACCEPTED: 2})
        sleep.assert_called_once_with(queue.PAUSE_BETWEEN_SENDS)
        M.objects.update(created_at=NOW - timedelta(days=181))
        self.assertEqual(queue.purge(NOW), (2, 0))

    @override_settings(WHATSAPP_CLOUD_MODE='live', WHATSAPP_CLOUD_ACCESS_TOKEN='t' * 30)
    def test_live_with_legal_pending_sends_nothing(self):
        message = self.queue_plan()
        self.assertEqual(self.run_one(message), 'blocked')
        self.assertEqual(self.fake.calls, [])
