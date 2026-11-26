"""Mejoras de formularios: script compartido, foco en el login y teclado móvil sin mayúscula inicial."""
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.accounts.tests.helpers import NO_MANIFEST

SCRIPT = Path(settings.BASE_DIR) / 'static' / 'js' / 'ux-forms.js'


@NO_MANIFEST
class UxFormsScriptTests(TestCase):
    def test_script_is_loaded_on_every_page_that_extends_base(self):
        for name in ('login', 'home', 'register', 'privacy'):
            with self.subTest(page=name):
                html = self.client.get(reverse(name)).content.decode()
                self.assertIn('js/ux-forms.js', html)

    def test_script_covers_passwords_and_double_submit(self):
        source = SCRIPT.read_text(encoding='utf-8')
        # mostrar/ocultar accesible
        for needle in ('aria-pressed', 'Mostrar contraseña', 'Ocultar contraseña', 'input[type="password"]'):
            self.assertIn(needle, source)
        # antes de enviar la contraseña vuelve a type=password (gestores de contraseñas)
        self.assertIn("input.type = 'password'", source)
        # anti doble envío: respeta GET, HTMX, formularios que otro código ya maneja y la excepción explícita
        for needle in ('data-allow-resubmit', 'defaultPrevented', "=== 'get'", 'hx-', 'pageshow', 'RESET_AFTER_MS'):
            self.assertIn(needle, source)

    def test_buttons_are_disabled_after_the_submit_is_built(self):
        """Un botón desactivado no envía su name/value: se desactiva en un setTimeout, no dentro del evento."""
        source = SCRIPT.read_text(encoding='utf-8')
        self.assertIn('setTimeout(function () { setBusy(form, true); }, 0)', source)

    def test_login_focuses_the_username_and_does_not_capitalize_it(self):
        html = self.client.get(reverse('login')).content.decode()
        tag = html[html.index('id="login-username"') - 60: html.index('id="login-username"') + 260]
        self.assertIn('autofocus', tag)
        self.assertIn('autocapitalize="none"', tag)     # el teclado del móvil ya no escribe «Usuario» con mayúscula
