"""Ruta /sw.js: el service worker se entrega desde la raíz para poder controlar todo el sitio."""
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import resolve, reverse

from apps.accounts.models import User
from apps.accounts.tests.helpers import NO_MANIFEST

SOURCE = Path(settings.BASE_DIR) / 'static' / 'sw.js'


@NO_MANIFEST   # el 404 ahora es la página de marca (extiende base.html y usa {% static %}); el CI no tiene manifiesto
class ServiceWorkerViewTests(TestCase):
    def test_route_is_at_the_root(self):
        self.assertEqual(reverse('service_worker'), '/sw.js')
        self.assertEqual(resolve('/sw.js').view_name, 'service_worker')

    def test_serves_the_repository_file_with_the_required_headers(self):
        response = self.client.get('/sw.js')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, SOURCE.read_bytes())
        self.assertEqual(response['Content-Type'], 'application/javascript; charset=utf-8')
        self.assertEqual(response['Cache-Control'], 'no-cache')
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_needs_no_session_and_never_touches_the_database(self):
        with self.assertNumQueries(0):
            anonymous = self.client.get('/sw.js')
        self.assertEqual(anonymous.status_code, 200)
        self.assertNotIn('Set-Cookie', anonymous)

        user = User.objects.create_user('clienta_sw', password='Clave-De-Prueba-9182!', role=User.ROLE_MEMBER)
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        with self.assertNumQueries(0):       # con cookie de sesión tampoco se consulta la sesión ni el usuario
            logged_in = self.client.get('/sw.js')
        self.assertEqual(logged_in.status_code, 200)
        self.assertEqual(logged_in.content, anonymous.content)

    def test_reads_the_source_file_and_not_static_root(self):
        """Con STATIC_ROOT apuntando a una copia distinta (la de collectstatic), se entrega la fuente."""
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'sw.js').write_text('// copia vieja en STATIC_ROOT', encoding='utf-8')
            with override_settings(STATIC_ROOT=root):
                response = self.client.get('/sw.js')
        self.assertEqual(response.content, SOURCE.read_bytes())
        self.assertNotIn(b'copia vieja', response.content)

    def test_head_is_allowed_and_has_no_body(self):
        response = self.client.head('/sw.js')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'')
        self.assertEqual(response['Service-Worker-Allowed'], '/')

    def test_other_methods_answer_405(self):
        for method in ('post', 'put', 'patch', 'delete', 'options'):
            with self.subTest(method=method):
                response = getattr(self.client, method)('/sw.js')
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response['Allow'], 'GET, HEAD')

    def test_missing_file_answers_404_and_leaves_a_warning_in_the_log(self):
        with mock.patch('apps.public.views.finders.find', return_value=None):
            with self.assertLogs('apps.public.views', level='WARNING') as logs:
                self.assertEqual(self.client.get('/sw.js').status_code, 404)
        self.assertIn('sw.js', logs.output[0])
        with mock.patch('apps.public.views.finders.find', return_value=[]):
            with self.assertLogs('apps.public.views', level='WARNING'):
                self.assertEqual(self.client.get('/sw.js').status_code, 404)

    def test_unreadable_file_answers_404_without_leaking_the_path(self):
        with mock.patch('apps.public.views.finders.find', return_value=str(SOURCE.parent / 'no-existe-sw.js')):
            with self.assertLogs('apps.public.views', level='WARNING') as logs:
                response = self.client.get('/sw.js')
        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b'no-existe-sw', response.content)
        self.assertNotIn(str(SOURCE.parent), ' '.join(logs.output))     # el log tampoco lleva rutas del servidor

    def test_file_is_read_on_every_request(self):
        """Sin estado en el proceso: si el archivo cambia, la siguiente petición lo refleja."""
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'sw.js'
            path.write_text('// version 1', encoding='utf-8')
            with mock.patch('apps.public.views.finders.find', return_value=str(path)):
                first = self.client.get('/sw.js')
                path.write_text('// version 2', encoding='utf-8')
                second = self.client.get('/sw.js')
        self.assertEqual(first.content, b'// version 1')
        self.assertEqual(second.content, b'// version 2')
        self.assertNotEqual(first['ETag'], second['ETag'])

    def test_etag_allows_a_304_without_body(self):
        first = self.client.get('/sw.js')
        again = self.client.get('/sw.js', HTTP_IF_NONE_MATCH=first['ETag'])
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.content, b'')
        self.assertEqual(again['Cache-Control'], 'no-cache')
        stale = self.client.get('/sw.js', HTTP_IF_NONE_MATCH='"otra-version"')
        self.assertEqual(stale.status_code, 200)
        self.assertEqual(stale.content, first.content)
