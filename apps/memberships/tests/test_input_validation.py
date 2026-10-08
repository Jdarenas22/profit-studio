"""trainer_membership_manage: duración, plan, acción y notas validados."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests.helpers import ScenarioTestCase
from apps.memberships.models import Membership, MembershipPlan


class MembershipManageValidationTests(ScenarioTestCase):
    def setUp(self):
        self.login(self.trainer_a)
        self.url = reverse('trainer_membership_manage', args=[self.client_a.pk])

    def post(self, **data):
        return self.client.post(self.url, data)

    def test_activate_uses_plan_duration_by_default(self):
        response = self.post(action='activate', plan_id=self.plan.pk)
        self.assertRedirects(response, reverse('trainer_client_detail', args=[self.client_a.pk]))
        m = Membership.objects.get(user=self.client_a)
        self.assertEqual(m.end_date, timezone.localdate() + timedelta(days=30))
        self.assertEqual(m.activated_by, self.trainer_a)

    def test_custom_duration_within_range(self):
        for days, expected in (('1', 1), ('365', 365), ('3650', 3650)):
            with self.subTest(days=days):
                self.post(action='activate', plan_id=self.plan.pk, duration_days=days)
                m = Membership.objects.get(user=self.client_a)
                self.assertEqual((m.end_date - m.start_date).days, expected)

    def test_invalid_duration_never_500_and_changes_nothing(self):
        for bad in ('abc', '0', '-5', '3651', '99999999999999999999', '1.5', '1e3', ' ', '9' * 5000):
            with self.subTest(duration=bad[:15]):
                response = self.post(action='activate', plan_id=self.plan.pk, duration_days=bad)
                if bad.strip() == '':
                    continue          # vacío = duración del plan (caso válido)
                self.assertEqual(response.status_code, 200)
                self.assertIn('duration_days', response.context['errors'])
                self.assertTrue([str(m) for m in response.context['messages']])
        self.assertFalse(Membership.objects.filter(user=self.client_a, end_date__gt=timezone.localdate() + timedelta(days=31)).exists())

    def test_invalid_duration_does_not_touch_an_existing_membership(self):
        self.post(action='activate', plan_id=self.plan.pk)
        before = Membership.objects.get(user=self.client_a).end_date
        self.post(action='renew', plan_id=self.plan.pk, duration_days='99999')
        self.assertEqual(Membership.objects.get(user=self.client_a).end_date, before)

    def test_plan_must_exist_be_active_and_be_numeric(self):
        inactive = MembershipPlan.objects.create(name='Vieja', duration_days=10, reference_price=1, is_active=False)
        for bad in ('', 'abc', '99999', '-1', str(inactive.pk)):
            with self.subTest(plan=bad):
                response = self.post(action='activate', plan_id=bad)
                self.assertEqual(response.status_code, 200)
                self.assertIn('plan_id', response.context['errors'])
        self.assertFalse(Membership.objects.filter(user=self.client_a).exists())

    def test_unknown_or_missing_action_is_rejected(self):
        for data in ({}, {'action': 'hackear'}, {'action': ''}, {'plan_id': self.plan.pk}):
            with self.subTest(data=data):
                response = self.post(**data)
                self.assertEqual(response.status_code, 200)
                self.assertIn('action', response.context['errors'])

    def test_notes_are_limited_and_saved(self):
        response = self.post(action='activate', plan_id=self.plan.pk, notes='x' * 1001)
        self.assertIn('notes', response.context['errors'])
        self.assertFalse(Membership.objects.filter(user=self.client_a).exists())
        self.post(action='activate', plan_id=self.plan.pk, notes='Pagó en efectivo')
        self.assertEqual(Membership.objects.get(user=self.client_a).notes, 'Pagó en efectivo')

    def test_renew_extends_from_current_end_and_can_fall_back_to_current_plan(self):
        self.post(action='activate', plan_id=self.plan.pk)
        self.post(action='renew')            # sin plan ni duración: usa el plan actual (30 días)
        m = Membership.objects.get(user=self.client_a)
        self.assertEqual(m.end_date, timezone.localdate() + timedelta(days=60))

    def test_renew_without_any_plan_or_duration_is_an_error(self):
        response = self.post(action='renew')
        self.assertIn('duration_days', response.context['errors'])

    def test_deactivate(self):
        response = self.post(action='deactivate')       # sin membresía: aviso, no 500
        self.assertEqual(response.status_code, 302)
        self.post(action='activate', plan_id=self.plan.pk)
        self.post(action='deactivate')
        self.assertFalse(Membership.objects.get(user=self.client_a).is_active)

    def test_get_for_client_without_membership_renders(self):
        """Antes `except Exception` envolvía esta consulta; ahora solo ObjectDoesNotExist."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['membership'])
