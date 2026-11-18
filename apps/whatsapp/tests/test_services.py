"""Casos de uso: alta, baja, reenvío, encolado idempotente, ganchos y regresión de salud."""
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.health import services as health_services
from apps.health.models import PURPOSE_WHATSAPP, ConsentRecord
from apps.assessments.models import InitialAssessment
from apps.payments.models import Payment
from apps.plans.models import Plan
from apps.whatsapp import services
from apps.whatsapp.models import (
    KIND_MEMBERSHIP_EXPIRED, KIND_MEMBERSHIP_EXPIRING, KIND_OPTIN_CONFIRM, KIND_PAYMENT_APPROVED, KIND_PLAN_APPROVED,
    KIND_TRAINER_REVIEW, WhatsAppContact, WhatsAppMessage,
)

from .helpers import NOW, PHONE, TODAY, WhatsAppTestCase, activate, membership, publish_clean_text, whatsapp_text


class EnableTests(WhatsAppTestCase):
    def test_enable_creates_consent_pending_contact_and_confirmation(self):
        contact = services.enable(self.client_a, '300 123 4567', whatsapp_text().version, now=NOW)
        self.assertEqual((contact.phone_e164, contact.status), (PHONE, WhatsAppContact.STATUS_PENDING))
        self.assertTrue(services.consent_valid(self.client_a))
        message = WhatsAppMessage.objects.get(recipient=self.client_a)
        self.assertEqual((message.kind, message.template_name), (KIND_OPTIN_CONFIRM, 'profit_confirmar_numero_v1'))
        self.assertFalse(services.can_receive(self.client_a))   # hasta confirmar no recibe avisos

    def test_invalid_phone_and_changed_text_save_nothing(self):
        with self.assertRaises(services.WhatsAppError) as ctx:
            services.enable(self.client_a, '601 234 5678', whatsapp_text().version)
        self.assertEqual(ctx.exception.field, 'phone')
        with self.assertRaises(services.WhatsAppError) as ctx:
            services.enable(self.client_a, '3001234567', whatsapp_text().version + 7)
        self.assertEqual(ctx.exception.field, 'accept')
        self.assertFalse(ConsentRecord.objects.filter(user=self.client_a).exists())
        self.assertFalse(WhatsAppContact.objects.exists())

    def test_phone_taken_by_another_account(self):
        activate(self.client_b)
        with self.assertRaises(services.WhatsAppError) as ctx:
            services.enable(self.client_a, PHONE, whatsapp_text().version)
        self.assertNotIn('client_b', ctx.exception.message)
        self.assertFalse(services.consent_valid(self.client_a))

    def test_minor_cannot_enable(self):
        InitialAssessment.objects.filter(user=self.client_a).update(age=16)
        with self.assertRaises(services.WhatsAppError):
            services.enable(self.client_a, PHONE, whatsapp_text().version)

    @override_settings(WHATSAPP_CLOUD_MODE='off')
    def test_off_cannot_enable(self):
        with self.assertRaises(services.WhatsAppError):
            services.enable(self.client_a, PHONE, whatsapp_text().version)

    @override_settings(WHATSAPP_CLOUD_MODE='live', WHATSAPP_CLOUD_ACCESS_TOKEN='t' * 30, WHATSAPP_CLOUD_GRAPH_VERSION='v27.0')
    def test_live_with_legal_pending_cannot_enable(self):
        with self.assertRaises(services.WhatsAppError):
            services.enable(self.client_a, PHONE, whatsapp_text().version)

    def test_changing_phone_requires_new_confirmation(self):
        activate(self.client_a)
        contact = services.enable(self.client_a, '310 000 0000', whatsapp_text().version, now=NOW)
        self.assertEqual(contact.status, WhatsAppContact.STATUS_PENDING)
        same = services.enable(self.client_a, '310 000 0000', whatsapp_text().version, now=NOW)
        self.assertEqual(WhatsAppMessage.objects.filter(kind=KIND_OPTIN_CONFIRM, status='queued').count(), 1)
        self.assertEqual(same.pk, contact.pk)

    def test_new_text_version_stops_notices_until_accepted(self):
        activate(self.client_a)
        self.assertTrue(services.can_receive(self.client_a))
        publish_clean_text()
        self.assertFalse(services.can_receive(self.client_a))


class ResendAndDisableTests(WhatsAppTestCase):
    def test_resend_limits(self):
        services.enable(self.client_a, PHONE, whatsapp_text().version, now=NOW)
        with self.assertRaises(services.WhatsAppError):
            services.resend_confirmation(self.client_a, now=NOW + timedelta(minutes=5))
        WhatsAppMessage.objects.update(created_at=NOW - timedelta(hours=1))
        services.resend_confirmation(self.client_a, now=NOW)
        WhatsAppMessage.objects.update(created_at=NOW - timedelta(minutes=30))
        services.resend_confirmation(self.client_a, now=NOW)
        WhatsAppMessage.objects.update(created_at=NOW - timedelta(minutes=20))
        with self.assertRaises(services.WhatsAppError):   # tercero del día ya alcanzado
            services.resend_confirmation(self.client_a, now=NOW)
        self.assertEqual(WhatsAppMessage.objects.filter(status='queued').count(), 1)

    def test_disable_revokes_and_cancels(self):
        activate(self.client_a)
        services.enqueue(self.client_a, KIND_PLAN_APPROVED, 'plan:x', kick=False)
        services.disable(self.client_a)
        contact = WhatsAppContact.objects.get(user=self.client_a)
        self.assertEqual(contact.status, WhatsAppContact.STATUS_OPTED_OUT)
        self.assertFalse(services.consent_valid(self.client_a))
        self.assertEqual(WhatsAppMessage.objects.get().status, WhatsAppMessage.STATUS_CANCELLED)


class EnqueueTests(WhatsAppTestCase):
    def test_dedupe_key_is_idempotent(self):
        first, created = services.enqueue(self.client_a, KIND_PLAN_APPROVED, 'plan:1', kick=False)
        second, created_again = services.enqueue(self.client_a, KIND_PLAN_APPROVED, 'plan:1', kick=False)
        self.assertEqual((first.pk, created, created_again), (second.pk, True, False))

    def test_notice_needs_active_contact_and_consent(self):
        plan = Plan.objects.create(client=self.client_a, kind='exercise', version=1, status=Plan.STATUS_APPROVED)
        self.assertIsNone(services.notify_plan_approved(plan))
        activate(self.client_a)
        message = services.notify_plan_approved(plan)
        self.assertEqual((message.kind, message.subject_id), (KIND_PLAN_APPROVED, plan.pk))
        self.assertEqual(services.notify_plan_approved(plan).pk, message.pk)

    @override_settings(WHATSAPP_CLOUD_MODE='off')
    def test_hooks_do_nothing_when_off(self):
        activate(self.client_a)
        plan = Plan.objects.create(client=self.client_a, kind='exercise', version=1, status=Plan.STATUS_APPROVED)
        self.assertIsNone(services.notify_plan_approved(plan))
        self.assertFalse(WhatsAppMessage.objects.exists())

    def test_hooks_never_raise(self):
        activate(self.client_a)
        plan = Plan.objects.create(client=self.client_a, kind='exercise', version=1, status=Plan.STATUS_APPROVED)
        with mock.patch.object(services, 'enqueue', side_effect=RuntimeError('boom')), \
                self.assertLogs('apps.whatsapp.services', 'WARNING'):
            self.assertIsNone(services.notify_plan_approved(plan))

    def test_trainer_review_grouped_per_three_hours(self):
        activate(self.trainer_a)
        services.notify_trainer_review(self.client_a, now=NOW)
        services.notify_trainer_review(self.client_a, now=NOW + timedelta(minutes=50))
        self.assertEqual(WhatsAppMessage.objects.filter(kind=KIND_TRAINER_REVIEW).count(), 1)
        services.notify_trainer_review(self.client_a, now=NOW + timedelta(hours=3))
        self.assertEqual(WhatsAppMessage.objects.filter(kind=KIND_TRAINER_REVIEW).count(), 2)
        # Sin entrenador asignado: las superusuarias
        activate(self.boss, phone='+573109998877')
        services.notify_trainer_review(self.client_free, now=NOW)
        self.assertTrue(WhatsAppMessage.objects.filter(kind=KIND_TRAINER_REVIEW, recipient=self.boss).exists())

    def test_payment_notice_only_with_switch(self):
        activate(self.client_a)
        payment = Payment.objects.create(user=self.client_a, plan=self.plan, amount_cents=100000,
                                         status=Payment.STATUS_APPROVED)
        self.assertIsNone(services.notify_payment_approved(payment.pk))
        with override_settings(WHATSAPP_CLOUD_PAYMENT_NOTICE=True):
            self.assertEqual(services.notify_payment_approved(payment.pk).kind, KIND_PAYMENT_APPROVED)

    def test_membership_notices(self):
        activate(self.client_a)
        activate(self.client_b, phone='+573110000000')
        membership(self.client_a, TODAY + timedelta(days=2))
        membership(self.client_b, TODAY - timedelta(days=1))
        membership(self.client_free, TODAY + timedelta(days=1))          # sin avisos activados
        self.assertEqual(services.enqueue_membership_notices(today=TODAY), 2)
        self.assertEqual(services.enqueue_membership_notices(today=TODAY), 0)   # idempotente
        self.assertEqual(WhatsAppMessage.objects.get(recipient=self.client_a).kind, KIND_MEMBERSHIP_EXPIRING)
        self.assertEqual(WhatsAppMessage.objects.get(recipient=self.client_b).kind, KIND_MEMBERSHIP_EXPIRED)
        membership(self.client_a, TODAY + timedelta(days=10))               # renovó: nada nuevo
        self.assertEqual(services.enqueue_membership_notices(today=TODAY), 0)


class PlanApprovalIntegrationTests(WhatsAppTestCase):
    def test_email_hook_also_queues_whatsapp_and_survives_failures(self):
        from apps.plans import notifications
        activate(self.client_a)
        plan = Plan.objects.create(client=self.client_a, kind='exercise', version=1, status=Plan.STATUS_APPROVED)
        self.assertTrue(notifications.client_plan_approved(plan))      # el correo sale igual
        self.assertTrue(WhatsAppMessage.objects.filter(kind=KIND_PLAN_APPROVED).exists())
        with mock.patch('apps.whatsapp.services.enqueue_notice', side_effect=RuntimeError):
            self.assertTrue(notifications.client_plan_approved(plan))


class HealthRegressionTests(WhatsAppTestCase):
    def test_whatsapp_purpose_is_not_a_health_purpose(self):
        self.assertFalse(health_services.is_valid_purpose(PURPOSE_WHATSAPP))
        purposes = [card['purpose'] for card in health_services.consent_cards(self.client_a)]
        self.assertEqual(purposes, ['health_data', 'ai_processing'])

    def test_whatsapp_placeholders_do_not_block_ai(self):
        from apps.health.models import ConsentTextVersion
        for purpose in ('health_data', 'ai_processing'):
            ConsentTextVersion.objects.create(purpose=purpose, version=50, body='Texto final.', is_current=True)
        self.assertTrue(whatsapp_text().has_placeholders)
        self.assertEqual(health_services.purposes_with_placeholders(), [])
        self.assertTrue(health_services.ai_texts_ready())

    @override_settings(HEALTH_FEATURES_ENABLED=True)
    def test_health_consent_route_rejects_whatsapp(self):
        membership(self.client_a, timezone.localdate() + timedelta(days=20))
        self.login(self.client_a)
        response = self.client.get(reverse('member_consent', args=['whatsapp_notices']))
        self.assertEqual(response.status_code, 404)
