"""payment_webhook: firma, monto/moneda, idempotencia, concurrencia (simulada), estados y fallos."""
import json
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.helpers import ScenarioTestCase
from apps.memberships.models import Membership, MembershipPlan
from apps.payments.models import Payment

from .test_wompi_signature import SECRET, make_event, sign

WOMPI = override_settings(
    WOMPI_EVENTS_SECRET=SECRET,
    WOMPI_INTEGRITY_SECRET='test_integrity_secret',
    WOMPI_PUBLIC_KEY='pub_test_123',
)


@WOMPI
class WebhookBase(ScenarioTestCase):
    AMOUNT = 10000000   # plan de 100.000 COP en centavos

    def setUp(self):
        self.url = reverse('payment_webhook')
        self.payment = Payment.objects.create(
            user=self.client_a, plan=self.plan, amount_cents=self.AMOUNT,
        )

    def event(self, **overrides):
        base = {'reference': self.payment.reference, 'amount_in_cents': self.AMOUNT}
        base.update(overrides)
        return make_event(**base)

    def post(self, event, **extra):
        return self.client.post(
            self.url, data=json.dumps(event), content_type='application/json', **extra,
        )

    def refresh(self):
        self.payment.refresh_from_db()
        return self.payment

    def membership(self):
        return Membership.objects.filter(user=self.client_a).first()


class WebhookSignatureTests(WebhookBase):
    def test_valid_event_approves_and_activates(self):
        response = self.post(self.event())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.refresh().status, Payment.STATUS_APPROVED)
        self.assertEqual(self.payment.wompi_transaction_id, '1234-1610641025-49201')
        self.assertEqual(self.payment.payment_method_type, 'NEQUI')
        membership = self.membership()
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.plan, self.plan)
        self.assertEqual((membership.end_date - membership.start_date).days, self.plan.duration_days)

    def test_header_checksum_accepted_and_checked(self):
        event = self.event()
        response = self.post(event, HTTP_X_EVENT_CHECKSUM=event['signature']['checksum'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.refresh().status, Payment.STATUS_APPROVED)

    def test_wrong_header_checksum_rejected(self):
        response = self.post(self.event(), HTTP_X_EVENT_CHECKSUM='C' * 64)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.refresh().status, Payment.STATUS_PENDING)

    def test_invalid_checksum_rejected_without_changes(self):
        event = self.event()
        event['signature']['checksum'] = 'A' * 64
        self.assertEqual(self.post(event).status_code, 401)
        self.assertEqual(self.refresh().status, Payment.STATUS_PENDING)
        self.assertIsNone(self.membership())

    def test_altered_timestamp_rejected(self):
        event = self.event()
        event['timestamp'] += 5
        self.assertEqual(self.post(event).status_code, 401)
        self.assertEqual(self.refresh().status, Payment.STATUS_PENDING)

    def test_checksum_without_timestamp_rejected(self):
        event = sign(self.event(), include_timestamp=False)
        self.assertEqual(self.post(event).status_code, 401)
        self.assertIsNone(self.membership())

    def test_status_tampering_rejected(self):
        event = self.event(status='DECLINED')
        event['data']['transaction']['status'] = 'APPROVED'   # firmado como DECLINED
        self.assertEqual(self.post(event).status_code, 401)
        self.assertEqual(self.refresh().status, Payment.STATUS_PENDING)

    def test_no_events_secret_returns_503_and_changes_nothing(self):
        with override_settings(WOMPI_EVENTS_SECRET=''):
            event = sign(self.event(), secret='')   # atacante firma con secreto vacío
            self.assertEqual(self.post(event).status_code, 503)
        self.assertEqual(self.refresh().status, Payment.STATUS_PENDING)
        self.assertIsNone(self.membership())

    def test_invalid_json_is_400(self):
        response = self.client.post(self.url, data='no es json', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_json_array_is_400(self):
        response = self.client.post(self.url, data='[]', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_other_event_types_ignored(self):
        event = self.event()
        event['event'] = 'nequi_token.updated'
        self.assertEqual(self.post(event).status_code, 200)
        self.assertEqual(self.refresh().status, Payment.STATUS_PENDING)

    def test_unknown_reference_is_200_and_creates_nothing(self):
        with self.assertLogs('apps.payments.services', level='WARNING'):
            response = self.post(make_event(reference='PROFIT-NOEXISTE', amount_in_cents=self.AMOUNT))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Payment.objects.count(), 1)


class WebhookAmountCurrencyTests(WebhookBase):
    def test_different_amount_does_not_activate(self):
        with self.assertLogs('apps.payments.services', level='WARNING') as logs:
            response = self.post(self.event(amount_in_cents=self.AMOUNT - 100))
        self.assertEqual(response.status_code, 200)   # un reintento no lo arreglaría
        self.assertIsNone(self.membership())
        self.assertEqual(self.refresh().status, Payment.STATUS_ERROR)   # rastro visible en el admin
        self.assertTrue(any('NO coinciden' in line for line in logs.output))

    def test_different_currency_does_not_activate(self):
        with self.assertLogs('apps.payments.services', level='WARNING'):
            response = self.post(self.event(currency='USD'))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.membership())
        self.assertEqual(self.refresh().status, Payment.STATUS_ERROR)

    def test_mismatch_in_declined_event_does_not_change_status(self):
        with self.assertLogs('apps.payments.services', level='WARNING'):
            self.post(self.event(status='DECLINED', amount_in_cents=1))
        self.assertEqual(self.refresh().status, Payment.STATUS_PENDING)

    def test_amount_as_text_is_not_accepted(self):
        with self.assertLogs('apps.payments.services', level='WARNING'):
            self.post(self.event(amount_in_cents=str(self.AMOUNT)))
        self.assertIsNone(self.membership())

    def test_correct_event_after_mismatch_still_works(self):
        with self.assertLogs('apps.payments.services', level='WARNING'):
            self.post(self.event(amount_in_cents=5))
        self.post(self.event())
        self.assertEqual(self.refresh().status, Payment.STATUS_APPROVED)
        self.assertIsNotNone(self.membership())


class WebhookIdempotencyTests(WebhookBase):
    def test_duplicate_delivery_extends_membership_only_once(self):
        event = self.event()
        self.assertEqual(self.post(event).status_code, 200)
        end_after_first = self.membership().end_date
        self.assertEqual(self.post(event).status_code, 200)
        self.assertEqual(self.membership().end_date, end_after_first)
        self.assertEqual(Membership.objects.filter(user=self.client_a).count(), 1)

    def test_simultaneous_deliveries_simulated_extend_once(self):
        """Dos entregas seguidas (la 2.a llega mientras la 1.a ya confirmó): una sola extensión."""
        events = [self.event(), self.event()]
        responses = [self.post(e) for e in events]
        self.assertEqual([r.status_code for r in responses], [200, 200])
        membership = self.membership()
        self.assertEqual((membership.end_date - membership.start_date).days, self.plan.duration_days)

    def test_webhook_uses_row_lock(self):
        """El pago se lee con select_for_update dentro de la transacción (candado de fila)."""
        from django.db.models.query import QuerySet
        with mock.patch.object(QuerySet, 'select_for_update', autospec=True,
                               side_effect=QuerySet.select_for_update) as locked:
            self.post(self.event())
        self.assertTrue(locked.called)

    def test_late_declined_after_approved_is_ignored(self):
        self.post(self.event())
        self.post(self.event(status='DECLINED'))
        self.assertEqual(self.refresh().status, Payment.STATUS_APPROVED)

    def test_voided_after_approved_logs_warning_and_keeps_membership(self):
        self.post(self.event())
        with self.assertLogs('apps.payments.services', level='WARNING') as logs:
            self.post(self.event(status='VOIDED'))
        self.assertTrue(any('VOIDED' in line for line in logs.output))
        self.assertEqual(self.refresh().status, Payment.STATUS_APPROVED)
        self.assertTrue(self.membership().is_active)

    def test_same_wompi_transaction_cannot_approve_two_payments(self):
        """La referencia no va firmada: el id de transacción solo sirve para un Payment."""
        other = Payment.objects.create(user=self.client_b, plan=self.plan, amount_cents=self.AMOUNT)
        self.post(self.event())
        with self.assertLogs('apps.payments.services', level='WARNING'):
            self.post(make_event(reference=other.reference, amount_in_cents=self.AMOUNT))
        other.refresh_from_db()
        self.assertEqual(other.status, Payment.STATUS_PENDING)
        self.assertFalse(Membership.objects.filter(user=self.client_b).exists())


class WebhookStatusTests(WebhookBase):
    def test_declined(self):
        self.post(self.event(status='DECLINED'))
        self.assertEqual(self.refresh().status, Payment.STATUS_DECLINED)
        self.assertIsNone(self.membership())

    def test_voided(self):
        self.post(self.event(status='VOIDED'))
        self.assertEqual(self.refresh().status, Payment.STATUS_VOIDED)
        self.assertIsNone(self.membership())

    def test_error_status(self):
        self.post(self.event(status='ERROR'))
        self.assertEqual(self.refresh().status, Payment.STATUS_ERROR)

    def test_pending_status_keeps_pending_but_stores_transaction(self):
        self.post(self.event(status='PENDING'))
        self.assertEqual(self.refresh().status, Payment.STATUS_PENDING)
        self.assertEqual(self.payment.wompi_transaction_id, '1234-1610641025-49201')

    def test_declined_then_approved_with_new_transaction(self):
        """En el widget se puede reintentar con la misma referencia."""
        self.post(self.event(status='DECLINED', id='tx-1'))
        self.post(self.event(status='APPROVED', id='tx-2'))
        self.assertEqual(self.refresh().status, Payment.STATUS_APPROVED)
        self.assertEqual(self.payment.wompi_transaction_id, 'tx-2')
        self.assertIsNotNone(self.membership())


class WebhookMembershipDatesTests(WebhookBase):
    def test_renews_from_current_end_when_still_valid(self):
        today = timezone.now().date()
        Membership.objects.create(
            user=self.client_a, plan=self.plan, start_date=today - timedelta(days=5),
            end_date=today + timedelta(days=10), is_active=True,
        )
        self.post(self.event())
        membership = self.membership()
        self.assertEqual(membership.end_date, today + timedelta(days=10 + self.plan.duration_days))

    def test_expired_membership_restarts_from_today(self):
        today = timezone.now().date()
        Membership.objects.create(
            user=self.client_a, plan=self.plan, start_date=today - timedelta(days=60),
            end_date=today - timedelta(days=30), is_active=True,
        )
        self.post(self.event())
        membership = self.membership()
        self.assertEqual(membership.start_date, today)
        self.assertEqual(membership.end_date, today + timedelta(days=self.plan.duration_days))

    def test_deactivated_membership_does_not_inherit_future_days(self):
        today = timezone.now().date()
        Membership.objects.create(
            user=self.client_a, plan=self.plan, start_date=today - timedelta(days=5),
            end_date=today + timedelta(days=20), is_active=False,
        )
        self.post(self.event())
        membership = self.membership()
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.end_date, today + timedelta(days=self.plan.duration_days))

    def test_plan_changes_on_renewal(self):
        longer = MembershipPlan.objects.create(name='Trimestral', duration_days=90, reference_price=100000)
        self.payment.plan = longer
        self.payment.save()
        today = timezone.now().date()
        Membership.objects.create(
            user=self.client_a, plan=self.plan, start_date=today, end_date=today + timedelta(days=3),
            is_active=True,
        )
        self.post(self.event())
        membership = self.membership()
        self.assertEqual(membership.plan, longer)
        self.assertEqual(membership.end_date, today + timedelta(days=93))


class WebhookActivationFailureTests(WebhookBase):
    def test_activation_failure_rolls_back_and_asks_retry(self):
        with mock.patch('apps.payments.services.activate_membership', side_effect=RuntimeError('boom')):
            with self.assertLogs('apps.payments.views', level='ERROR'):
                response = self.post(self.event())
        self.assertEqual(response.status_code, 500)
        # El pago NO queda aprobado sin membresía
        self.assertEqual(self.refresh().status, Payment.STATUS_PENDING)
        self.assertEqual(self.payment.wompi_transaction_id, '')
        self.assertIsNone(self.membership())

        # Wompi reintenta y esta vez funciona
        self.assertEqual(self.post(self.event()).status_code, 200)
        self.assertEqual(self.refresh().status, Payment.STATUS_APPROVED)
        self.assertIsNotNone(self.membership())

    def test_membership_save_error_rolls_back_payment(self):
        with mock.patch.object(Membership, 'activate', side_effect=RuntimeError('db')):
            with self.assertLogs('apps.payments.views', level='ERROR'):
                response = self.post(self.event())
        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.refresh().status, Payment.STATUS_PENDING)

    def test_approved_payment_without_plan_is_flagged_not_silenced(self):
        Payment.objects.filter(pk=self.payment.pk).update(plan=None)
        with self.assertLogs('apps.payments.services', level='ERROR'):
            response = self.post(self.event())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.refresh().status, Payment.STATUS_ERROR)
        self.assertIsNone(self.membership())


@WOMPI
class CheckoutTests(ScenarioTestCase):
    def test_checkout_generates_signed_form(self):
        self.login(self.client_a)
        response = self.client.get(reverse('payment_checkout', args=[self.plan.pk]))
        self.assertEqual(response.status_code, 200)
        payment = Payment.objects.get(user=self.client_a)
        self.assertEqual(payment.amount_cents, 10000000)
        self.assertEqual(len(response.context['integrity_sig']), 64)

    def test_checkout_reuses_pending_payment_with_same_price(self):
        self.login(self.client_a)
        url = reverse('payment_checkout', args=[self.plan.pk])
        self.client.get(url)
        self.client.get(url)
        self.assertEqual(Payment.objects.filter(user=self.client_a).count(), 1)

    def test_checkout_new_payment_when_price_changed(self):
        self.login(self.client_a)
        url = reverse('payment_checkout', args=[self.plan.pk])
        self.client.get(url)
        MembershipPlan.objects.filter(pk=self.plan.pk).update(reference_price=120000)
        self.client.get(url)
        self.assertEqual(Payment.objects.filter(user=self.client_a).count(), 2)

    def test_checkout_without_integrity_secret_fails_clearly(self):
        self.login(self.client_a)
        with override_settings(WOMPI_INTEGRITY_SECRET=''):
            with self.assertLogs('apps.payments.views', level='ERROR'):
                response = self.client.get(reverse('payment_checkout', args=[self.plan.pk]))
        self.assertRedirects(response, reverse('plans'), fetch_redirect_response=False)
        self.assertFalse(Payment.objects.exists())   # no se crea un pago huérfano

    def test_checkout_without_public_key_fails_clearly(self):
        self.login(self.client_a)
        with override_settings(WOMPI_PUBLIC_KEY=''):
            with self.assertLogs('apps.payments.views', level='ERROR'):
                response = self.client.get(reverse('payment_checkout', args=[self.plan.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Payment.objects.exists())

    def test_checkout_requires_login(self):
        response = self.client.get(reverse('payment_checkout', args=[self.plan.pk]))
        self.assertEqual(response.status_code, 302)


@WOMPI
class PaymentReturnTests(ScenarioTestCase):
    def test_other_users_payment_is_not_shown(self):
        theirs = Payment.objects.create(
            user=self.client_a, plan=self.plan, amount_cents=10000000,
            wompi_transaction_id='tx-ajena', status=Payment.STATUS_APPROVED,
        )
        self.login(self.client_b)
        response = self.client.get(reverse('payment_return') + '?id=tx-ajena')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['payment'])
        self.assertNotContains(response, theirs.reference)

    def test_other_users_payment_does_not_leak_even_if_user_has_pending(self):
        Payment.objects.create(
            user=self.client_a, plan=self.plan, amount_cents=10000000,
            wompi_transaction_id='tx-ajena', status=Payment.STATUS_APPROVED,
        )
        mine = Payment.objects.create(user=self.client_b, plan=self.plan, amount_cents=10000000)
        self.login(self.client_b)
        response = self.client.get(reverse('payment_return') + '?id=tx-ajena')
        self.assertEqual(response.context['payment'], mine)

    def test_owner_sees_own_payment(self):
        mine = Payment.objects.create(
            user=self.client_a, plan=self.plan, amount_cents=10000000,
            wompi_transaction_id='tx-mia', status=Payment.STATUS_APPROVED,
        )
        self.login(self.client_a)
        response = self.client.get(reverse('payment_return') + '?id=tx-mia')
        self.assertEqual(response.context['payment'], mine)

    def test_requires_login(self):
        self.assertEqual(self.client.get(reverse('payment_return')).status_code, 302)
