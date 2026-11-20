"""Cabecera Permissions-Policy en todas las respuestas (config/middleware.py)."""
import re
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase

from apps.accounts.tests.helpers import NO_MANIFEST
from config.middleware import PermissionsPolicyMiddleware, format_permissions_policy

BASE_DIR = Path(settings.BASE_DIR)


def parse(header):
    """'camera=(), payment=(self "https://x.co")' -> {'camera': [], 'payment': ['self', 'https://x.co']}"""
    policy = {}
    for part in header.split(', '):
        feature, _, value = part.partition('=')
        policy[feature] = [item.strip('"') for item in value.strip('()').split(' ') if item]
    return policy


@NO_MANIFEST     # las páginas usan {% static %}: en pruebas no hay collectstatic ni manifest
class PermissionsPolicyHeaderTests(TestCase):
    def test_every_kind_of_response_carries_the_header(self):
        for label, response in (
            ('portada (200)', self.client.get('/')),
            ('healthcheck (200)', self.client.get('/health/')),
            ('service worker (200)', self.client.get('/sw.js')),
            ('ruta inexistente (404)', self.client.get('/no-existe-esta-ruta/')),
            ('redirección a login (302)', self.client.get('/accounts/dashboard/')),
            ('método no permitido (405)', self.client.post('/sw.js')),
        ):
            with self.subTest(response=label):
                self.assertIn('Permissions-Policy', response, f'{label}: {response.status_code}')

    def test_denies_what_the_site_does_not_use(self):
        policy = parse(self.client.get('/health/')['Permissions-Policy'])
        for feature in ('camera', 'microphone', 'geolocation', 'usb', 'serial', 'bluetooth', 'hid', 'midi',
                        'display-capture', 'magnetometer', 'xr-spatial-tracking'):
            with self.subTest(feature=feature):
                self.assertEqual(policy.get(feature), [], f'{feature} debe quedar vacío (nadie)')

    def test_payment_is_only_for_the_site_and_wompi(self):
        policy = parse(self.client.get('/health/')['Permissions-Policy'])
        self.assertEqual(policy['payment'], ['self', 'https://checkout.wompi.co'])

    def test_header_value_is_stable(self):
        header = self.client.get('/health/')['Permissions-Policy']
        self.assertIn('camera=()', header)
        self.assertIn('payment=(self "https://checkout.wompi.co")', header)


class PermissionsPolicyDoesNotBreakEmbedsTests(SimpleTestCase):
    """No se puede negar una función que un iframe de las plantillas pide con `allow="..."`."""

    def templates(self):
        yield from (BASE_DIR / 'templates').rglob('*.html')
        yield from (BASE_DIR / 'apps').glob('*/templates/**/*.html')

    def test_templates_do_not_delegate_denied_features(self):
        denied = {feature for feature, allowlist in settings.PERMISSIONS_POLICY.items() if not allowlist}
        found = {}
        for path in self.templates():
            for allow in re.findall(r'\ballow="([^"]*)"', path.read_text(encoding='utf-8')):
                for item in allow.split(';'):
                    name = item.strip().split(' ')[0]
                    if name:
                        found.setdefault(name, path.relative_to(BASE_DIR).as_posix())
        for name, where in found.items():
            with self.subTest(feature=name, template=where):
                self.assertNotIn(name, denied, f'{where} pide «{name}» pero Permissions-Policy lo niega')

    def test_youtube_embed_features_are_not_in_the_policy(self):
        # Respaldo de la prueba anterior: lo que usa templates/exercises/detail.html no puede negarse
        for feature in ('accelerometer', 'autoplay', 'clipboard-write', 'encrypted-media', 'gyroscope',
                        'picture-in-picture', 'fullscreen'):
            with self.subTest(feature=feature):
                self.assertNotIn(feature, settings.PERMISSIONS_POLICY)

    def test_the_middleware_is_before_whitenoise_and_right_after_security(self):
        middleware = settings.MIDDLEWARE
        self.assertEqual(middleware.index('config.middleware.PermissionsPolicyMiddleware'),
                         middleware.index('django.middleware.security.SecurityMiddleware') + 1)
        self.assertLess(middleware.index('config.middleware.PermissionsPolicyMiddleware'),
                        middleware.index('whitenoise.middleware.WhiteNoiseMiddleware'))


class PermissionsPolicyMiddlewareUnitTests(SimpleTestCase):
    def call(self, response):
        return PermissionsPolicyMiddleware(lambda request: response)(RequestFactory().get('/'))

    def test_a_view_can_set_its_own_policy(self):
        own = HttpResponse('x')
        own['Permissions-Policy'] = 'camera=(self)'
        self.assertEqual(self.call(own)['Permissions-Policy'], 'camera=(self)')

    def test_formatting(self):
        self.assertEqual(
            format_permissions_policy({'camera': [], 'payment': ['self', 'https://a.co'], 'fullscreen': ['*']}),
            'camera=(), payment=(self "https://a.co"), fullscreen=*')

    def test_empty_policy_adds_no_header(self):
        with self.settings(PERMISSIONS_POLICY={}):
            self.assertNotIn('Permissions-Policy', self.call(HttpResponse('x')))

    def test_invalid_policy_stops_the_server(self):
        for bad in ({'camera': 'self'}, {'': []}, {'cam era': []}, {'camera;x': []}):
            with self.subTest(policy=bad), self.assertRaises(ImproperlyConfigured):
                format_permissions_policy(bad)
