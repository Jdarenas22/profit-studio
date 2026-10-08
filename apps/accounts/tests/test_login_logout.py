"""login: redirección segura con `next`; logout solo por POST; django-axes tras el proxy."""
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.axes_utils import get_client_ip
from apps.accounts.models import User

from .helpers import NO_MANIFEST, PASSWORD, make_user


@NO_MANIFEST
class LoginNextTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user('miembro', User.ROLE_MEMBER)

    def credentials(self, **extra):
        return {'username': 'miembro', 'password': PASSWORD, **extra}

    def test_external_next_in_query_is_ignored(self):
        for bad in ('https://evil.example.com/', '//evil.example.com/', 'http://evil.example.com',
                    'javascript:alert(1)', '\\\\evil.example.com'):
            with self.subTest(next=bad):
                self.client.logout()
                response = self.client.post(reverse('login') + '?next=' + bad, self.credentials())
                self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

    def test_external_next_in_post_body_is_ignored(self):
        for bad in ('https://evil.example.com/', '//evil.example.com/', 'javascript:alert(1)'):
            with self.subTest(next=bad):
                self.client.logout()
                response = self.client.post(reverse('login'), self.credentials(next=bad))
                self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

    def test_internal_next_is_followed_from_query_and_post(self):
        target = reverse('member_routine')
        response = self.client.post(reverse('login') + f'?next={target}', self.credentials())
        self.assertRedirects(response, target, fetch_redirect_response=False)
        self.client.logout()
        response = self.client.post(reverse('login'), self.credentials(next=target))
        self.assertRedirects(response, target, fetch_redirect_response=False)

    def test_wrong_password_does_not_redirect(self):
        response = self.client.post(reverse('login') + '?next=https://evil.example.com/',
                                    self.credentials(password='mala'))
        self.assertEqual(response.status_code, 200)


@NO_MANIFEST
class LogoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = make_user('miembro', User.ROLE_MEMBER)

    def test_get_is_not_allowed_and_keeps_the_session(self):
        self.client.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.assertIn('_auth_user_id', self.client.session)

    def test_other_methods_are_not_allowed(self):
        self.client.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        for method in ('put', 'patch', 'delete'):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.client, method)(reverse('logout')).status_code, 405)

    def test_post_closes_session_and_redirects_home(self):
        self.client.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.post(reverse('logout'))
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_post_requires_csrf_token(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(strict.post(reverse('logout')).status_code, 403)


class ClientIpTests(TestCase):
    """Función de IP real para django-axes (Railway envía X-Real-IP)."""

    class FakeRequest:
        def __init__(self, **meta):
            self.META = meta

    def test_uses_x_real_ip_when_valid(self):
        request = self.FakeRequest(HTTP_X_REAL_IP='203.0.113.7', REMOTE_ADDR='10.0.0.1')
        self.assertEqual(get_client_ip(request), '203.0.113.7')

    def test_supports_ipv6(self):
        request = self.FakeRequest(HTTP_X_REAL_IP='2001:db8::1', REMOTE_ADDR='10.0.0.1')
        self.assertEqual(get_client_ip(request), '2001:db8::1')

    def test_falls_back_to_remote_addr(self):
        self.assertEqual(get_client_ip(self.FakeRequest(REMOTE_ADDR='10.0.0.1')), '10.0.0.1')
        for junk in ('', 'no-es-una-ip', '1.2.3.4, 5.6.7.8', '<script>'):
            with self.subTest(header=junk):
                request = self.FakeRequest(HTTP_X_REAL_IP=junk, REMOTE_ADDR='10.0.0.1')
                self.assertEqual(get_client_ip(request), '10.0.0.1')


@NO_MANIFEST
@override_settings(
    AXES_ENABLED=True,
    AXES_FAILURE_LIMIT=3,
    AXES_CLIENT_IP_CALLABLE='apps.accounts.axes_utils.get_client_ip',
)
class AxesBehindProxyTests(TestCase):
    """El bloqueo es por usuario + IP real: un atacante no bloquea a los demás."""

    @classmethod
    def setUpTestData(cls):
        make_user('victima', User.ROLE_MEMBER)
        make_user('otra', User.ROLE_MEMBER)

    def attempt(self, username, password, ip):
        return self.client.post(
            reverse('login'), {'username': username, 'password': password}, HTTP_X_REAL_IP=ip,
        )

    def test_lockout_is_per_user_and_ip(self):
        attacker = '198.51.100.9'
        for _ in range(3):
            self.attempt('victima', 'mala', attacker)
        # Bloqueado: ni siquiera la contraseña correcta entra desde esa IP, y se muestra la plantilla de bloqueo (429)
        response = self.attempt('victima', PASSWORD, attacker)
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('_auth_user_id', self.client.session)
        # La persona legítima, desde su propia IP, sí entra
        response = self.attempt('victima', PASSWORD, '203.0.113.50')
        self.assertEqual(response.status_code, 302)
        self.client.logout()
        # Otro usuario desde la IP del atacante no queda bloqueado
        response = self.attempt('otra', PASSWORD, attacker)
        self.assertEqual(response.status_code, 302)

    def test_successful_login_resets_failures(self):
        ip = '203.0.113.60'
        for _ in range(2):
            self.attempt('otra', 'mala', ip)
        self.assertEqual(self.attempt('otra', PASSWORD, ip).status_code, 302)
        self.client.logout()
        for _ in range(2):
            self.attempt('otra', 'mala', ip)
        self.assertEqual(self.attempt('otra', PASSWORD, ip).status_code, 302)
