from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests.helpers import NO_MANIFEST
from apps.memberships.models import Membership
from apps.payments.models import ManualPayment


@NO_MANIFEST
class ListPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.boss = User.objects.create(username='page_boss', role='trainer', is_superuser=True, password='!')
        cls.trainer = User.objects.create(username='page_trainer', role='trainer', password='!')
        User.objects.bulk_create([User(username=f'page_client_{i}', first_name=f'Cliente {i:03d}',
                                       role='member', password='!', assigned_trainer=cls.trainer)
                                  for i in range(26)])
        cls.members = list(User.objects.filter(role='member').order_by('pk'))
        today = timezone.localdate()
        Membership.objects.bulk_create([Membership(user=user, start_date=today, end_date=today) for user in cls.members])
        ManualPayment.objects.bulk_create([ManualPayment(user=cls.members[0], trainer=cls.trainer,
                                                        amount=100, payment_date=today) for _ in range(26)])

    def login(self, user):
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')

    def test_client_pages_are_bounded_disjoint_and_preserve_status_filter(self):
        self.login(self.boss)
        url = reverse('trainer_clients')
        first = self.client.get(url, {'status': 'all'})
        second = self.client.get(url, {'status': 'all', 'page': 2})
        first_ids = {u.pk for u in first.context['clients']}
        second_ids = {u.pk for u in second.context['clients']}
        self.assertEqual((len(first_ids), len(second_ids)), (25, 1))
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(len(first_ids | second_ids), 26)
        self.assertContains(first, '26 en total')
        self.assertContains(first, 'status=all&amp;page=2')

    def test_query_count_does_not_grow_with_clients_or_memberships(self):
        self.login(self.boss)
        url = reverse('trainer_clients')
        self.client.get(url)  # calienta contexto/caché
        with CaptureQueriesContext(connection) as before:
            self.client.get(url)
        User.objects.bulk_create([User(username=f'extra_{i}', first_name=f'Extra {i}', role='member', password='!')
                                  for i in range(100)])
        with CaptureQueriesContext(connection) as after:
            response = self.client.get(url)
        self.assertLessEqual(len(after), 12)
        self.assertEqual(len(before), len(after))
        self.assertEqual(len(response.context['clients']), 25)

    def test_pagination_never_adds_another_trainers_clients(self):
        User.objects.create(username='foreign_client', role='member', password='!')
        self.login(self.trainer)
        for page in (1, 2, 'invalid', 999):
            response = self.client.get(reverse('trainer_clients'), {'page': page})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(all(u.assigned_trainer_id == self.trainer.pk for u in response.context['clients']))

    def test_payment_total_and_count_cover_all_pages(self):
        self.login(self.boss)
        response = self.client.get(reverse('trainer_manual_payment_list'), {'client': self.members[0].pk})
        self.assertEqual(len(response.context['payments']), 25)
        self.assertEqual(response.context['total'], 2600)
        self.assertEqual(response.context['payments_count'], 26)
        self.assertContains(response, f'client={self.members[0].pk}&amp;page=2')

    def test_member_history_is_paginated_and_scoped(self):
        ManualPayment.objects.create(user=self.members[1], trainer=self.trainer,
                                     amount=999, payment_date=timezone.localdate())
        self.login(self.members[0])
        response = self.client.get(reverse('member_payment_history'), {'page': 2})
        self.assertEqual(len(response.context['payments']), 1)
        self.assertEqual(response.context['page_obj'].paginator.count, 26)
        self.assertTrue(all(p.user_id == self.members[0].pk for p in response.context['payments']))
