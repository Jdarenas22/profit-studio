"""El healthcheck de Railway vive solo en config/urls.py; /health/<...> sigue siendo de la app health."""
from django.test import TestCase
from django.urls import NoReverseMatch, resolve, reverse

from apps.public import views as public_views


class HealthCheckTests(TestCase):
    def test_health_responde_200_con_status_ok(self):
        response = self.client.get('/health/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_health_lo_atiende_config_urls_y_no_la_app_public(self):
        self.assertEqual(resolve('/health/').func.__module__, 'config.urls')
        self.assertFalse(hasattr(public_views, 'health_check'))
        with self.assertRaises(NoReverseMatch):
            reverse('health_check')

    def test_rutas_de_la_app_health_siguen_funcionando(self):
        match = resolve('/health/consent/health_data/')
        self.assertEqual(match.url_name, 'member_consent')
        response = self.client.get('/health/consent/health_data/')
        # Sin sesión: redirige al login (no es el healthcheck ni un 404 de ruta inexistente)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertNotEqual(response.headers.get('Content-Type', ''), 'application/json')
