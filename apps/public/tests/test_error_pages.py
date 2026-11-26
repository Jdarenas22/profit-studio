"""Páginas de error propias (400, 403, CSRF, 404, 500) y contraste mínimo del texto en las plantillas."""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.template.loader import render_to_string
from django.test import Client, RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.views.defaults import bad_request, permission_denied, server_error

from apps.accounts.models import User
from apps.accounts.tests.helpers import NO_MANIFEST, make_user


@NO_MANIFEST
class NotFoundPageTests(TestCase):
    def test_unknown_url_shows_the_branded_page_with_a_way_out(self):
        response = self.client.get('/esta-pagina-no-existe/')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'No encontramos esa página', status_code=404)
        self.assertContains(response, 'Error 404', status_code=404)
        self.assertContains(response, reverse('home'), status_code=404)
        self.assertContains(response, 'name="robots" content="noindex"', status_code=404)

    def test_anonymous_is_offered_login_and_member_the_dashboard(self):
        anonymous = self.client.get('/no-existe/')
        self.assertContains(anonymous, 'Iniciar sesión', status_code=404)
        self.assertNotContains(anonymous, 'Ir a mi panel', status_code=404)
        member = make_user('clienta', User.ROLE_MEMBER)
        self.client.force_login(member)
        logged = self.client.get('/no-existe/')
        self.assertContains(logged, 'Ir a mi panel', status_code=404)

    def test_no_technical_details_leak(self):
        html = self.client.get('/admin-secreto/xyz/').content.decode()
        for leak in ('Traceback', 'Request URL', 'Using the URLconf', 'config.urls', 'DEBUG'):
            self.assertNotIn(leak, html)


@NO_MANIFEST
class ForbiddenAndCsrfPageTests(TestCase):
    def request(self):
        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        return request

    def test_403_uses_the_branded_page(self):
        response = permission_denied(self.request(), Exception('detalle interno'))
        self.assertEqual(response.status_code, 403)
        body = response.content.decode()
        self.assertIn('No tienes permiso para ver esto', body)
        self.assertNotIn('detalle interno', body)

    def test_400_uses_the_branded_page(self):
        response = bad_request(self.request(), Exception('detalle interno'))
        self.assertEqual(response.status_code, 400)
        body = response.content.decode()
        self.assertIn('No pudimos procesar la solicitud', body)
        self.assertNotIn('detalle interno', body)

    def test_csrf_failure_explains_what_happened_without_jargon(self):
        make_user('csrf_user', User.ROLE_MEMBER)
        client = Client(enforce_csrf_checks=True)
        response = client.post(reverse('login'), {'username': 'csrf_user', 'password': 'x'})
        self.assertEqual(response.status_code, 403)
        body = response.content.decode()
        self.assertIn('Esta página estuvo abierta demasiado tiempo', body)
        self.assertIn('No se guardó nada', body)
        # Solo el texto que lee la persona (el HTML de base.html lleva etiquetas técnicas)
        visible = re.sub(r'<(script|style)\b.*?</\1>|<[^>]+>', ' ', body, flags=re.S)
        for jargon in ('CSRF', 'cookie', 'Referer', 'Origin checking'):
            self.assertNotIn(jargon, visible)

    def test_api_csrf_failure_is_still_json(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post('/api/v1/assessments/1/ruffier-dickson/', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response['Content-Type'], 'application/json')


class ServerErrorPageTests(SimpleTestCase):
    def test_500_is_standalone_and_renders_without_context(self):
        response = server_error(RequestFactory().get('/'))
        self.assertEqual(response.status_code, 500)
        body = response.content.decode()
        self.assertIn('Algo salió mal de nuestro lado', body)
        self.assertIn('href="/"', body)
        # No debe depender de nada que pueda fallar: ni estáticos, ni plantillas base, ni scripts
        for dependency in ('/static/', '<script', 'stylesheet', '{%', '{{'):
            self.assertNotIn(dependency, body)

    def test_500_template_is_plain_html(self):
        self.assertNotIn('extends', Path(settings.BASE_DIR, 'templates', '500.html').read_text(encoding='utf-8')
                         .split('{% endcomment %}')[-1])
        self.assertIn('Error 500', render_to_string('500.html'))


class TextContrastGuardTests(SimpleTestCase):
    """El texto sobre fondo oscuro necesita al menos `text-white/50` (WCAG AA). `/10 a /40` dan entre 1,7:1 y
    3,7:1 y se lee mal sobre todo en el móvil. Los íconos decorativos (`<i class="fa ...">`) están exentos."""
    WEAK = re.compile(r'(?<![\w/-])(?:placeholder:)?text-white/(10|20|25|30|40)\b')
    ICON = re.compile(r'<i\s[^>]*>')

    def test_no_weak_text_in_templates(self):
        offenders = []
        for path in sorted(Path(settings.BASE_DIR, 'templates').rglob('*.html')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
                if self.WEAK.search(self.ICON.sub('', line)):
                    offenders.append(f'{path.relative_to(settings.BASE_DIR)}:{number}')
        self.assertEqual(offenders, [], 'Texto con contraste insuficiente (usa text-white/60 o más): ' + ', '.join(offenders[:10]))
