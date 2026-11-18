from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.password_reset import IP_LIMIT


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                                       'LOCATION': 'reset-limits-tests'}})
class PasswordResetLimitsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='reset', email='reset@example.com', password='ClaveFicticia-839!')

    def setUp(self):
        cache.clear()
        self.url = reverse('password_reset')
        self.addCleanup(cache.clear)

    def post(self, email='reset@example.com', ip='192.0.2.10'):
        return self.client.post(self.url, {'email': email}, REMOTE_ADDR=ip)

    def test_account_limit_is_case_insensitive_and_survives_ip_changes(self):
        responses = [self.post(), self.post('RESET@example.com', '192.0.2.11'), self.post(ip='192.0.2.12')]
        self.assertTrue(all(r.status_code == 302 for r in responses))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual({r.url for r in responses}, {reverse('password_reset_done')})

    def test_existing_and_unknown_accounts_have_identical_public_response(self):
        for _ in range(4):
            existing = self.post()
            unknown = self.post('unknown@example.com', '192.0.2.99')
            self.assertEqual((existing.status_code, existing.url), (unknown.status_code, unknown.url))

    def test_ip_budget_applies_even_to_unknown_accounts(self):
        for index in range(IP_LIMIT):
            self.post(f'unknown{index}@example.com')
        response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(getattr(mail, 'outbox', [])), 0)

    def test_cache_failure_suppresses_delivery_and_does_not_expose_input(self):
        with mock.patch('apps.accounts.password_reset.cache.add', side_effect=OSError('private infrastructure error')):
            with self.assertLogs('apps.accounts.password_reset', level='WARNING') as logs:
                response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(getattr(mail, 'outbox', [])), 0)
        self.assertNotIn('reset@example.com', str(logs.output))
        self.assertNotIn('private infrastructure error', str(logs.output))

    def test_expired_counter_can_start_a_new_window(self):
        with mock.patch('apps.accounts.password_reset.cache.add', side_effect=[False, True, True]), \
                mock.patch('apps.accounts.password_reset.cache.incr', side_effect=ValueError):
            self.post()
        self.assertEqual(len(mail.outbox), 1)

    def test_budget_is_released_when_cache_entries_expire(self):
        for _ in range(3):
            self.post()
        self.assertEqual(len(mail.outbox), 2)
        cache.clear()
        self.post()
        self.assertEqual(len(mail.outbox), 3)
