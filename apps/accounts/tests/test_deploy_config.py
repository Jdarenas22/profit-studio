"""Configuración de despliegue: settings de producción, /media/, start.sh y create_trainer.

Las pruebas de production.py corren en un subproceso con variables inventadas (no
dependen de tu .env ni de Railway y no tocan ninguna base real).
"""
import importlib
import json
import os
import re
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import clear_url_caches

from apps.accounts.models import User

BASE_DIR = Path(settings.BASE_DIR)
GOOD_SECRET = 'k9#Vz!2mQx7$Lp0wRt5&Yb3nHs8^Df1JcGu6Ae4Ko-Zi_Xv'

# Variables que neutralizan lo que pudiera venir del .env local (se leen con setdefault)
CLEAN_ENV = {
    'SECRET_KEY': GOOD_SECRET,
    'ALLOWED_HOSTS': 'profit-test.up.railway.app,www.ejemplo.com',
    'RAILWAY_PUBLIC_DOMAIN': '',
    'DATABASE_URL': 'sqlite:///:memory:',
    'REDIS_URL': '',
    'R2_ACCESS_KEY_ID': '', 'R2_SECRET_ACCESS_KEY': '', 'R2_BUCKET_NAME': '',
    'R2_ENDPOINT_URL': '', 'R2_PUBLIC_URL': '',
    'EMAIL_HOST_USER': '',
}

PROBE = (
    "import json, django, os;"
    "os.environ['DJANGO_SETTINGS_MODULE']='config.settings.production';"
    "from django.conf import settings;"
    "print(json.dumps({'hosts': settings.ALLOWED_HOSTS, 'csrf': settings.CSRF_TRUSTED_ORIGINS,"
    " 'secret_ok': bool(settings.SECRET_KEY), 'debug': settings.DEBUG,"
    " 'axes_ip': settings.AXES_CLIENT_IP_CALLABLE,"
    " 'axes_params': settings.AXES_LOCKOUT_PARAMETERS,"
    " 'axes_reset': settings.AXES_RESET_ON_SUCCESS}))"
)


def load_production(**overrides):
    """Importa config.settings.production en un subproceso. Devuelve (código, dict|None, stderr)."""
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'SYSTEMROOT', 'TEMP', 'TMP', 'HOME')}
    env.update(CLEAN_ENV)
    env.update(overrides)
    done = subprocess.run(
        [sys.executable, '-c', PROBE], cwd=BASE_DIR, env=env,
        capture_output=True, text=True, timeout=120,
    )
    data = json.loads(done.stdout.strip().splitlines()[-1]) if done.returncode == 0 else None
    return done.returncode, data, done.stderr


class ProductionSettingsTests(SimpleTestCase):
    def test_valid_configuration_loads(self):
        code, data, err = load_production()
        self.assertEqual(code, 0, err)
        self.assertFalse(data['debug'])
        self.assertEqual(data['axes_ip'], 'apps.accounts.axes_utils.get_client_ip')
        self.assertEqual(data['axes_params'], [['username', 'ip_address']])
        self.assertTrue(data['axes_reset'])

    def test_secret_key_is_required_and_not_a_placeholder(self):
        for bad in ('', '   ', 'django-insecure-dev-key-change-in-production',
                    'cambia-esta-clave-secreta-en-produccion'):
            with self.subTest(secret=bad):
                code, _, err = load_production(SECRET_KEY=bad)
                self.assertNotEqual(code, 0)
                self.assertIn('ImproperlyConfigured', err)
                self.assertIn('SECRET_KEY', err)

    def test_allowed_hosts_is_required_and_has_no_wildcard(self):
        for bad in ('', '*', 'midominio.com,*'):
            with self.subTest(hosts=bad):
                code, _, err = load_production(ALLOWED_HOSTS=bad)
                self.assertNotEqual(code, 0)
                self.assertIn('ImproperlyConfigured', err)
                self.assertIn('ALLOWED_HOSTS', err)

    def test_healthcheck_and_railway_domain_are_allowed_without_breaking_csrf(self):
        code, data, err = load_production(RAILWAY_PUBLIC_DOMAIN='profit-prod.up.railway.app')
        self.assertEqual(code, 0, err)
        self.assertIn('healthcheck.railway.app', data['hosts'])
        self.assertIn('profit-prod.up.railway.app', data['hosts'])
        self.assertNotIn('*', data['hosts'])
        # CSRF: los dominios reales (incluido el de Railway) sí; el del healthcheck no hace falta
        self.assertIn('https://profit-test.up.railway.app', data['csrf'])
        self.assertIn('https://www.ejemplo.com', data['csrf'])
        self.assertIn('https://profit-prod.up.railway.app', data['csrf'])
        self.assertNotIn('https://healthcheck.railway.app', data['csrf'])
        self.assertFalse([o for o in data['csrf'] if o.startswith('https://*.up.railway')])

    def test_leading_dot_host_becomes_wildcard_origin(self):
        code, data, err = load_production(ALLOWED_HOSTS='.ejemplo.com')
        self.assertEqual(code, 0, err)
        self.assertEqual(data['csrf'], ['https://*.ejemplo.com'])


class MediaRouteTests(SimpleTestCase):
    """/media/ solo se sirve en producción si NO hay R2 y MEDIA_ROOT tiene valor."""

    def tearDown(self):
        import config.urls
        importlib.reload(config.urls)
        clear_url_caches()

    def media_patterns(self, **overrides):
        import config.urls
        with override_settings(DEBUG=False, **overrides):
            importlib.reload(config.urls)
            return [p for p in config.urls.urlpatterns if 'media' in str(p.pattern)]

    def test_served_without_r2_when_media_root_is_set(self):
        patterns = self.media_patterns(MEDIA_ROOT=str(BASE_DIR / 'media'), AWS_STORAGE_BUCKET_NAME='')
        self.assertEqual(len(patterns), 1)

    def test_not_served_with_r2(self):
        self.assertEqual(self.media_patterns(MEDIA_ROOT=str(BASE_DIR / 'media'),
                                             AWS_STORAGE_BUCKET_NAME='bucket'), [])

    def test_not_served_with_empty_media_root(self):
        for empty in ('', '   ', None):
            with self.subTest(media_root=empty):
                self.assertEqual(self.media_patterns(MEDIA_ROOT=empty, AWS_STORAGE_BUCKET_NAME=''), [])


class StartScriptTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raw = (BASE_DIR / 'start.sh').read_bytes()
        cls.text = cls.raw.decode('utf-8')

    def test_no_hardcoded_password_anywhere(self):
        self.assertNotIn('ProFit2024', self.text)
        self.assertNotIn('--password', self.text)

    def test_does_not_print_database_url_value(self):
        for line in self.text.splitlines():
            if line.strip().startswith('echo'):
                self.assertNotRegex(line, r'\$\{?DATABASE_URL', line)

    def test_migrate_failure_aborts(self):
        match = re.search(r'migrate --noinput.*?\n\s*else\n(.*?)\n\s*fi', self.text, re.S)
        self.assertIsNotNone(match)
        self.assertIn('exit 1', match.group(1))

    def test_superuser_created_only_with_env_password(self):
        self.assertIn('TRAINER_INITIAL_PASSWORD', self.text)
        self.assertRegex(self.text, r'if \[ -n "\$\{TRAINER_INITIAL_PASSWORD:-\}" \]')

    def test_keeps_media_directories(self):
        self.assertIn('mkdir -p', self.text)
        self.assertIn('media/profiles', self.text)

    def test_unix_line_endings(self):
        self.assertNotIn(b'\r', self.raw)
        attributes = (BASE_DIR / '.gitattributes').read_text()
        self.assertIn('*.sh text eol=lf', attributes)


class CreateTrainerCommandTests(TestCase):
    def run_command(self, env=None, **options):
        env = {'TRAINER_INITIAL_PASSWORD': '', 'TRAINER_PASSWORD': '', **(env or {})}
        out = StringIO()
        with mock.patch.dict(os.environ, env):
            call_command('create_trainer', stdout=out, **options)
        return out.getvalue()

    def test_reads_password_from_trainer_initial_password(self):
        self.run_command({'TRAINER_INITIAL_PASSWORD': 'Entorno-Seguro-4471!'},
                         username='yiseth', first_name='Yiseth', superuser=True)
        user = User.objects.get(username='yiseth')
        self.assertTrue(user.check_password('Entorno-Seguro-4471!'))
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.role, User.ROLE_TRAINER)

    def test_accepts_legacy_trainer_password_name(self):
        self.run_command({'TRAINER_PASSWORD': 'Antigua-Variable-3390!'}, username='yiseth', superuser=True)
        self.assertTrue(User.objects.get(username='yiseth').check_password('Antigua-Variable-3390!'))

    def test_new_name_wins_over_legacy_name(self):
        self.run_command({'TRAINER_INITIAL_PASSWORD': 'Nueva-Variable-7712!',
                          'TRAINER_PASSWORD': 'Antigua-Variable-3390!'}, username='yiseth')
        self.assertTrue(User.objects.get(username='yiseth').check_password('Nueva-Variable-7712!'))

    def test_weak_passwords_are_rejected(self):
        for weak in ('12345678', 'password', 'yiseth123', 'corta'):
            with self.subTest(password=weak):
                with self.assertRaises(CommandError):
                    self.run_command({'TRAINER_INITIAL_PASSWORD': weak}, username='yiseth')
                self.assertFalse(User.objects.filter(username='yiseth').exists())

    def test_existing_account_is_not_touched(self):
        User.objects.create_user('yiseth', password='Original-Segura-1122!', role=User.ROLE_TRAINER)
        self.run_command({'TRAINER_INITIAL_PASSWORD': 'Otra-Distinta-9988!'}, username='yiseth', superuser=True)
        user = User.objects.get(username='yiseth')
        self.assertTrue(user.check_password('Original-Segura-1122!'))
        self.assertTrue(user.is_superuser)
