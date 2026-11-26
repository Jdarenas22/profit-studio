"""Página pública /privacidad/: la política que enlazan los consentimientos."""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.tests.helpers import NO_MANIFEST
from apps.public.views import PRIVACY_CONTROLLER


@NO_MANIFEST
class PrivacyPageTests(TestCase):
    def test_page_is_public_and_lives_at_privacidad(self):
        self.assertEqual(reverse('privacy'), '/privacidad/')
        response = self.client.get('/privacidad/')   # sin iniciar sesión
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Política de privacidad')

    def test_names_the_controller_and_the_rights_channel(self):
        html = self.client.get('/privacidad/').content.decode()
        for value in (PRIVACY_CONTROLLER['name'], PRIVACY_CONTROLLER['nit'], PRIVACY_CONTROLLER['email']):
            self.assertIn(value, html)

    def test_covers_the_three_consents_and_the_providers(self):
        html = self.client.get('/privacidad/').content.decode()
        for topic in ('datos de salud', 'inteligencia artificial', 'WhatsApp', 'Gemini', 'Railway', 'Wompi',
                      'Brevo', 'Cloudflare', 'mayores de 18', 'Superintendencia de Industria y Comercio'):
            with self.subTest(topic=topic):
                self.assertIn(topic, html)

    def test_only_safe_methods(self):
        self.assertEqual(self.client.post('/privacidad/').status_code, 405)

    def test_footer_links_to_it(self):
        self.assertContains(self.client.get(reverse('home')), 'href="/privacidad/"')
