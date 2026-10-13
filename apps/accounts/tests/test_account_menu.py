"""Submenú de cuenta del banner (templates/partials/navbar.html + account_menu_items.html).

El nombre de la persona abre un submenú con sus opciones de perfil y el cierre de sesión. Las reglas por
rol viven en un solo parcial que se incluye dos veces (escritorio y menú móvil), así que cada enlace
aparece dos veces en la página. Se comprueba el HTML renderizado (no el comportamiento de Alpine, que
se verificó en un navegador).
"""
import re

from django.test import override_settings
from django.urls import reverse

from .helpers import ScenarioTestCase

FLAGS_ON = override_settings(HEALTH_FEATURES_ENABLED=True, AI_PLANS_MODE='simulated')
FLAGS_OFF = override_settings(HEALTH_FEATURES_ENABLED=False)


def logout_forms(html):
    return re.findall(r'<form[^>]*action="%s"[^>]*>.*?</form>' % re.escape(reverse('logout')), html, re.S)


class AnonymousBannerTests(ScenarioTestCase):
    def test_visitors_keep_login_and_register_and_get_no_account_menu(self):
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Iniciar sesión')
        self.assertContains(response, 'Inscribirme')
        self.assertNotContains(response, 'account-menu')
        self.assertNotContains(response, 'Cerrar sesión')
        self.assertEqual(logout_forms(response.content.decode()), [])


@FLAGS_ON
class MemberMenuTests(ScenarioTestCase):
    def page(self):
        self.login(self.client_a)
        return self.client.get(reverse('home'))

    def test_the_name_opens_a_menu_with_the_profile_options(self):
        response = self.page()
        self.assertContains(response, 'aria-controls="account-menu"')
        self.assertContains(response, 'aria-haspopup="true"')
        for label in ('Mi panel', 'Editar mi perfil', 'Mis medidas', 'Mi ficha', 'Mi plan', 'Cerrar sesión'):
            self.assertContains(response, label)
        for name in ('dashboard', 'member_profile_edit', 'member_measurement_list', 'member_health_profile',
                     'member_plans'):
            self.assertContains(response, f'href="{reverse(name)}"')

    def test_the_loose_links_and_the_old_exit_button_are_gone_from_the_bar(self):
        html = self.page().content.decode()
        self.assertNotIn('>Salir<', html)
        self.assertNotIn('Salir\n', html)
        # cada opción aparece dos veces (menú de escritorio y menú móvil), no tres
        self.assertEqual(html.count('Mis medidas</span>'), 2)
        self.assertEqual(html.count('Mi ficha</span>'), 2)
        self.assertEqual(html.count('Mi plan</span>'), 2)

    def test_the_options_follow_the_active_features(self):
        with override_settings(HEALTH_FEATURES_ENABLED=False):
            response = self.page()
        self.assertContains(response, 'Editar mi perfil')
        for label in ('Mis medidas', 'Mi ficha', 'Mi plan'):
            self.assertNotContains(response, label + '</span>')
        with override_settings(AI_PLANS_MODE='off'):
            response = self.page()
        self.assertContains(response, 'Mis medidas')
        self.assertNotContains(response, 'Mi plan</span>')

    def test_logout_stays_a_protected_post_in_both_menus(self):
        forms = logout_forms(self.page().content.decode())
        self.assertEqual(len(forms), 2)                               # escritorio y móvil
        for form in forms:
            self.assertIn('method="post"', form)
            self.assertIn('csrfmiddlewaretoken', form)
            self.assertIn("localStorage.removeItem('pfs_remember')", form)

    def test_the_menu_does_not_show_trainer_only_labels(self):
        self.assertNotContains(self.page(), 'Administradora')
        self.assertNotContains(self.page(), reverse('trainer_dashboard'))

    def test_the_name_is_escaped(self):
        self.client_a.first_name = '<b>Ana</b>'
        self.client_a.save()
        html = self.page().content.decode()
        self.assertNotIn('<b>Ana</b>', html)
        self.assertIn('&lt;b&gt;Ana&lt;/b&gt;', html)


@FLAGS_ON
class TrainerMenuTests(ScenarioTestCase):
    def page(self, user, name='home'):
        self.login(user)
        return self.client.get(reverse(name))

    def test_trainers_get_panel_and_profile_but_no_member_only_links(self):
        response = self.page(self.trainer_a)
        self.assertContains(response, 'aria-controls="account-menu"')
        self.assertContains(response, f'href="{reverse("trainer_dashboard")}"')
        self.assertContains(response, f'href="{reverse("member_profile_edit")}"')
        self.assertContains(response, 'Mi perfil')
        self.assertContains(response, 'Cerrar sesión')
        for label in ('Mis medidas', 'Mi ficha', 'Editar mi perfil'):
            self.assertNotContains(response, label)
        self.assertNotContains(response, 'Mi plan</span>')
        self.assertNotContains(response, 'Administradora')

    def test_the_superuser_is_labeled_as_administrator(self):
        response = self.page(self.boss)
        self.assertContains(response, 'Administradora')
        self.assertContains(response, f'href="{reverse("trainer_dashboard")}"')

    def test_the_trainer_tab_strip_no_longer_repeats_profile_and_exit(self):
        html = self.page(self.boss, 'trainer_dashboard').content.decode()
        self.assertNotIn('>Salir<', html)
        self.assertNotIn('Salir\n', html)
        self.assertEqual(html.count('Mi perfil</span>'), 2)           # solo el submenú (escritorio y móvil)
        self.assertEqual(len(logout_forms(html)), 2)
        for tab in ('Panel', 'Clientes', 'Ejercicios', 'Rutinas', 'Pagos', 'Entrenadores'):
            self.assertContains(self.client.get(reverse('trainer_dashboard')), tab)
