"""Verificación del correo e invitaciones (docs/contracts/verificacion-correo.md).

Sin red: el correo sale por `mail.outbox` y Turnstile se prueba con un abridor falso de `urllib`.
"""
import re
import urllib.parse
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts import email_verification as ev
from apps.accounts import services, turnstile
from apps.accounts.models import AccountAuditLog, User, pending_registration_q
from apps.accounts.permissions import clients_for_trainer

from .helpers import PASSWORD, ScenarioTestCase, make_user
from .test_input_validation import STRONG, messages_of

URL_RE = re.compile(r'https?://\S+')


def link_in(message):
    """La URL absoluta que trae un correo."""
    found = URL_RE.findall(message.body)
    assert found, message.body
    return found[0]


def token_of(url):
    return urllib.parse.urlparse(url).path.rstrip('/').rsplit('/', 1)[-1]


def path_of(url):
    return urllib.parse.urlparse(url).path


class Base(ScenarioTestCase):
    def setUp(self):
        cache.clear()                     # los límites de frecuencia viven en la caché
        self.addCleanup(cache.clear)

    def payload(self, **over):
        data = {
            'first_name': 'Ana', 'last_name': 'Pérez', 'email': 'Ana.Perez@Example.com',
            'username': 'Ana.Perez', 'phone': '+57 300 123 4567', 'gender': 'F',
            'password1': STRONG, 'password2': STRONG, 'training_goal': 'cardio',
        }
        data.update(over)
        return data

    def register(self, **over):
        return self.client.post(reverse('register'), self.payload(**over))

    def confirm(self, url):
        """Abre el enlace del correo y pulsa «Confirmar mi correo»."""
        page = self.client.get(path_of(url))
        self.assertEqual(page.status_code, 200)
        return self.client.post(page.context['confirm_url'], {'token': page.context['token']})


# ═════════════════════════════ Registro público ═════════════════════════════

class RegistrationTests(Base):
    def test_registration_creates_an_inactive_unverified_account_and_sends_one_email(self):
        response = self.register()
        self.assertRedirects(response, reverse('register_check_email'), fetch_redirect_response=False)
        user = User.objects.get(username='ana.perez')
        self.assertFalse(user.is_active)
        self.assertIsNone(user.email_verified_at)
        self.assertTrue(user.is_pending_registration)
        self.assertTrue(user.check_password(STRONG))          # la contraseña sí quedó guardada (con hash)
        self.assertNotIn('_auth_user_id', self.client.session)    # y NO inició sesión
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['ana.perez@example.com'])
        self.assertIn('/accounts/verify/', link_in(message))
        self.assertIn('24 horas', message.body)
        self.assertNotIn(STRONG, message.body)
        self.assertFalse(message.alternatives)                 # texto plano

    def test_superuser_is_not_notified_at_registration(self):
        self.register()
        self.assertEqual([m.to for m in mail.outbox], [['ana.perez@example.com']])

    def test_check_email_page_repeats_what_was_typed_once(self):
        response = self.client.get(self.register().url)
        self.assertEqual(response.context['email'], 'ana.perez@example.com')
        self.assertEqual(self.client.get(reverse('register_check_email')).context['email'], '')

    def test_check_email_page_requires_no_login_and_is_not_cached(self):
        response = self.client.get(reverse('register_check_email'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])

    def test_opening_the_link_does_not_activate_and_confirming_does(self):
        self.register()
        url = link_in(mail.outbox[0])
        page = self.client.get(path_of(url))
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, 'accounts/email_verify_confirm.html')
        self.assertFalse(User.objects.get(username='ana.perez').is_active)        # solo abrirlo no activa
        response = self.client.post(page.context['confirm_url'], {'token': page.context['token']})
        self.assertRedirects(response, reverse('login') + '?next=' + reverse('register_success'),
                             fetch_redirect_response=False)
        user = User.objects.get(username='ana.perez')
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.email_verified_at)
        self.assertNotIn('_auth_user_id', self.client.session)     # lleva al login: no inicia sesión sola

    def test_superuser_is_notified_when_the_email_is_confirmed(self):
        self.register(interested_plan=str(self.plan.pk))
        self.confirm(link_in(mail.outbox[0]))
        self.assertEqual(len(mail.outbox), 2)
        notice = mail.outbox[1]
        self.assertIn(self.boss.email, notice.to)
        self.assertIn('Mensual', notice.body)
        self.assertNotIn(STRONG, notice.body)

    def test_after_confirming_the_person_can_log_in(self):
        self.register()
        self.confirm(link_in(mail.outbox[0]))
        response = self.client.post(reverse('login'), {'username': 'ana.perez', 'password': STRONG})
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

    def test_login_after_confirming_lands_on_the_welcome_page(self):
        self.register()
        response = self.confirm(link_in(mail.outbox[0]))
        response = self.client.post(response.url, {'username': 'ana.perez', 'password': STRONG})
        self.assertRedirects(response, reverse('register_success'), fetch_redirect_response=False)

    def test_the_login_message_after_confirming_is_shown(self):
        self.register()
        response = self.confirm(link_in(mail.outbox[0]))
        response = self.client.get(response.url)
        self.assertTrue(any('confirmamos tu correo' in m for m in messages_of(response)))

    def test_link_cannot_be_used_twice(self):
        self.register()
        url = link_in(mail.outbox[0])
        self.confirm(url)
        again = self.client.get(path_of(url))                       # el enlace ya no sirve ni para verlo
        self.assertEqual(again.context['reason'], 'used')
        self.assertTemplateUsed(again, 'accounts/email_link_invalid.html')
        post = self.client.post(reverse('email_verify_confirm'), {'token': token_of(url)})
        self.assertEqual(post.context['reason'], 'used')
        self.assertEqual(len(mail.outbox), 2)                       # y no vuelve a avisar a la superusuaria

    def test_expired_link(self):
        self.register()
        url = link_in(mail.outbox[0])
        with mock.patch.object(ev, 'VERIFY_MAX_AGE_SECONDS', -1):
            page = self.client.get(path_of(url))
            post = self.client.post(reverse('email_verify_confirm'), {'token': token_of(url)})
        self.assertEqual(page.context['reason'], 'expired')
        self.assertEqual(post.context['reason'], 'expired')
        self.assertFalse(User.objects.get(username='ana.perez').is_active)

    def test_link_that_is_still_fresh_works_near_the_limit(self):
        self.register()
        url = link_in(mail.outbox[0])
        with mock.patch.object(ev, 'VERIFY_MAX_AGE_SECONDS', 24 * 3600):
            self.assertEqual(self.client.get(path_of(url)).status_code, 200)
            self.assertTemplateUsed(self.client.get(path_of(url)), 'accounts/email_verify_confirm.html')

    def test_manipulated_links_do_not_activate_anything(self):
        self.register()
        user = User.objects.get(username='ana.perez')
        token = token_of(link_in(mail.outbox[0]))
        other_purpose = ev.make_token(user, ev.PURPOSE_INVITE)          # firmado, pero para otra cosa
        bad = {
            'último carácter cambiado': token[:-1] + ('A' if token[-1] != 'A' else 'B'),
            'cortado': token[: len(token) // 2],
            'otro propósito': other_purpose,
            'basura': 'esto-no-es-un-enlace',
            'demasiado largo': 'a' * 600,
            'solo dos puntos': '::',
        }
        for name, value in bad.items():
            with self.subTest(name):
                page = self.client.get(reverse('email_verify', args=[value]))
                post = self.client.post(reverse('email_verify_confirm'), {'token': value})
                self.assertEqual(page.context['reason'], 'invalid')
                self.assertEqual(post.context['reason'], 'invalid')
        self.assertEqual(self.client.post(reverse('email_verify_confirm'), {}).context['reason'], 'invalid')
        self.assertFalse(User.objects.get(username='ana.perez').is_active)

    def test_a_link_only_activates_its_own_account(self):
        self.register()
        self.register(username='otra.persona', email='otra@example.com')
        first = link_in(mail.outbox[0])
        self.confirm(first)
        self.assertTrue(User.objects.get(username='ana.perez').is_active)
        self.assertFalse(User.objects.get(username='otra.persona').is_active)

    def test_changing_the_account_email_kills_the_link(self):
        self.register()
        url = link_in(mail.outbox[0])
        User.objects.filter(username='ana.perez').update(email='otro@example.com')
        self.assertEqual(self.client.get(path_of(url)).context['reason'], 'used')

    def test_confirm_endpoint_is_post_only_and_needs_csrf(self):
        self.assertEqual(self.client.get(reverse('email_verify_confirm')).status_code, 405)
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(reverse('email_verify_confirm'), {'token': 'x'}).status_code, 403)

    def test_link_pages_are_private(self):
        self.register()
        page = self.client.get(path_of(link_in(mail.outbox[0])))
        self.assertEqual(page['Referrer-Policy'], 'same-origin')
        self.assertIn('no-store', page['Cache-Control'])
        invalid = self.client.get(reverse('email_verify', args=['basura']))
        self.assertEqual(invalid['Referrer-Policy'], 'same-origin')

    def test_dead_links_do_not_put_the_token_in_django_request_logs(self):
        with self.assertNoLogs('django.request', level='WARNING'):
            self.client.get(reverse('email_verify', args=['basura-con-token-falso']))
            self.client.post(reverse('email_verify_confirm'), {'token': 'basura-con-token-falso'})

    def test_tokens_never_reach_the_application_logs(self):
        with self.assertLogs('apps.accounts', level='INFO') as logs:
            self.register()
            url = link_in(mail.outbox[0])
            self.confirm(url)
        text = '\n'.join(logs.output)
        self.assertNotIn(token_of(url), text)
        self.assertNotIn('ana.perez@example.com', text)
        self.assertIn('email verified', text)

    def test_mail_failure_does_not_break_the_registration(self):
        with mock.patch('apps.accounts.email_verification.send_mail', side_effect=OSError('smtp caído')), \
                self.assertLogs('apps.accounts.email_verification', 'WARNING') as logs:
            response = self.register()
        self.assertRedirects(response, reverse('register_check_email'), fetch_redirect_response=False)
        self.assertTrue(User.objects.get(username='ana.perez').is_pending_registration)
        self.assertNotIn('smtp caído', '\n'.join(logs.output))

    def test_username_taken_is_still_reported_so_the_person_can_choose_another(self):
        response = self.register(username='CLIENT_A')
        self.assertEqual(response.status_code, 200)
        self.assertIn('ya está en uso', response.context['errors']['username'])
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_forms_create_nothing_and_send_nothing(self):
        for over in ({'email': 'no-es-correo'}, {'password2': 'otra'}, {'first_name': '<script>'}):
            response = self.register(**over)
            self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='ana.perez').exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_the_account_does_not_exist_for_the_panel_until_confirmed(self):
        self.register()
        user = User.objects.get(username='ana.perez')
        self.assertFalse(clients_for_trainer(self.boss).filter(pk=user.pk).exists())
        self.confirm(link_in(mail.outbox[0]))
        self.assertTrue(clients_for_trainer(self.boss).filter(pk=user.pk).exists())


# ═════════════════════════ Enumeración de correos ═══════════════════════════

class EnumerationTests(Base):
    def test_new_and_existing_emails_get_the_same_response(self):
        new = self.register(email='nueva@example.com', username='nueva.persona')
        existing = self.register(email='CLIENT_A@example.com', username='otra.persona')
        self.assertEqual((new.status_code, new.url), (existing.status_code, existing.url))
        for response in (new, existing):
            page = self.client.get(response.url)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.context['resend_url'], reverse('verification_resend'))
        self.assertFalse(User.objects.filter(username='otra.persona').exists())      # no se creó nada

    def test_existing_active_account_gets_a_notice_without_a_confirmation_link(self):
        self.register(email='CLIENT_A@example.com', username='otra.persona')
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['client_a@example.com'])
        self.assertIn(reverse('login'), message.body)
        self.assertIn(reverse('password_reset'), message.body)
        self.assertNotIn('/accounts/verify/', message.body)
        self.assertNotIn('/accounts/invitation/', message.body)

    def test_existing_pending_account_gets_a_fresh_link_and_no_second_account(self):
        self.register()
        self.register(username='segundo.intento', password1=STRONG + 'x', password2=STRONG + 'x')
        self.assertEqual(User.objects.filter(email='ana.perez@example.com').count(), 1)
        self.assertFalse(User.objects.filter(username='segundo.intento').exists())
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(mail.outbox[1].to, ['ana.perez@example.com'])
        self.assertIn('/accounts/verify/', link_in(mail.outbox[1]))
        # La cuenta pendiente conserva la contraseña del PRIMER registro (nadie la reescribe desde fuera)
        self.assertTrue(User.objects.get(username='ana.perez').check_password(STRONG))

    def test_existing_invitation_gets_a_fresh_invitation(self):
        self.login(self.trainer_a)
        self.client.post(reverse('trainer_add_client'), {
            'first_name': 'Nueva', 'last_name': 'Persona', 'email': 'nueva@example.com', 'username': 'nueva.persona'})
        self.client.logout()
        mail.outbox.clear()
        self.register(email='NUEVA@example.com', username='otra.persona')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/accounts/invitation/', link_in(mail.outbox[0]))

    def test_the_email_budget_applies_even_when_the_email_exists(self):
        responses = [self.register(email='CLIENT_A@example.com', username=f'otra.persona{i}') for i in range(5)]
        self.assertEqual({r.url for r in responses}, {reverse('register_check_email')})
        self.assertEqual(len(mail.outbox), ev.EMAIL_SEND_LIMIT)

    def test_existing_email_still_pays_for_the_slow_hash(self):
        with mock.patch('apps.accounts.views.make_password') as slow:
            self.register(email='CLIENT_A@example.com', username='otra.persona')
            slow.assert_called_once()
            slow.reset_mock()
            self.register(email='nueva@example.com', username='nueva.persona')
            slow.assert_not_called()

    def test_registration_attempt_limit_still_counts_existing_emails(self):
        with mock.patch('apps.accounts.views.REGISTER_SUCCESS_LIMIT', 2):
            codes = [self.register(email='CLIENT_A@example.com', username=f'otra{i}persona').status_code
                     for i in range(3)]
        self.assertEqual(codes, [302, 302, 429])


# ══════════════════════════════ Reenviar el correo ══════════════════════════════

class ResendTests(Base):
    def post(self, email='ana.perez@example.com', **extra):
        return self.client.post(reverse('verification_resend'), {'email': email}, **extra)

    def test_get_shows_the_form(self):
        response = self.client.get(reverse('verification_resend'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['sent'])
        self.assertEqual(response.context['turnstile_site_key'], '')

    def test_pending_registration_gets_a_new_confirmation_link(self):
        self.register()
        mail.outbox.clear()
        response = self.post()
        self.assertTrue(response.context['sent'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ana.perez@example.com'])
        self.assertIn('/accounts/verify/', link_in(mail.outbox[0]))

    def test_response_is_the_same_for_pending_unknown_and_active_emails(self):
        self.register()
        mail.outbox.clear()
        results = {}
        for name, email in (('pendiente', 'ana.perez@example.com'), ('desconocido', 'nadie@example.com'),
                            ('activa', 'client_a@example.com')):
            response = self.post(email)
            results[name] = (response.status_code, response.context['sent'], dict(response.context['errors']))
        self.assertEqual(len(set(map(str, results.values()))), 1, results)
        self.assertEqual([m.to for m in mail.outbox], [['ana.perez@example.com']])      # solo la pendiente

    def test_deactivated_account_gets_nothing(self):
        User.objects.filter(pk=self.client_a.pk).update(is_active=False, deactivated_at=timezone.now())
        self.post('client_a@example.com')
        self.assertEqual(len(mail.outbox), 0)

    def test_pending_invitation_gets_a_new_invitation(self):
        self.login(self.trainer_a)
        self.client.post(reverse('trainer_add_client'), {
            'first_name': 'Nueva', 'last_name': 'Persona', 'email': 'nueva@example.com', 'username': 'nueva.persona'})
        self.client.logout()
        mail.outbox.clear()
        self.post('nueva@example.com')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/accounts/invitation/', link_in(mail.outbox[0]))

    def test_a_new_link_does_not_kill_the_previous_one_until_one_is_used(self):
        self.register()
        first = link_in(mail.outbox[0])
        self.post()
        second = link_in(mail.outbox[1])
        self.confirm(second)
        self.assertEqual(self.client.get(path_of(first)).context['reason'], 'used')

    def test_per_email_limit_is_silent(self):
        self.register()
        mail.outbox.clear()
        responses = [self.post() for _ in range(ev.EMAIL_SEND_LIMIT + 2)]
        # El registro ya gastó un envío de la cuota de esa dirección
        self.assertEqual(len(mail.outbox), ev.EMAIL_SEND_LIMIT - 1)
        self.assertEqual({(r.status_code, r.context['sent']) for r in responses}, {(200, True)})

    def test_per_ip_limit_answers_429(self):
        with mock.patch.object(ev, 'RESEND_IP_LIMIT', 2):
            codes = [self.post(f'x{i}@example.com').status_code for i in range(4)]
            response = self.post('x9@example.com')
        self.assertEqual(codes, [200, 200, 429, 429])
        self.assertEqual(response.status_code, 429)
        self.assertIn('Retry-After', response)
        self.assertEqual(len(mail.outbox), 0)

    def test_limit_is_per_ip(self):
        with mock.patch.object(ev, 'RESEND_IP_LIMIT', 1):
            self.assertEqual(self.post('a@example.com').status_code, 200)
            self.assertEqual(self.post('a@example.com').status_code, 429)
            self.assertEqual(self.post('a@example.com', REMOTE_ADDR='203.0.113.9').status_code, 200)

    def test_invalid_email_is_a_field_error(self):
        response = self.post('no-es-correo')
        self.assertEqual(response.status_code, 200)
        self.assertIn('email', response.context['errors'])
        self.assertFalse(response.context['sent'])
        self.assertEqual(len(mail.outbox), 0)

    def test_email_is_matched_case_insensitively(self):
        self.register()
        mail.outbox.clear()
        self.post('ANA.PEREZ@EXAMPLE.COM')
        self.assertEqual(len(mail.outbox), 1)

    def test_mail_failure_does_not_change_the_response(self):
        self.register()
        with mock.patch('apps.accounts.email_verification.send_mail', side_effect=OSError('x')), \
                self.assertLogs('apps.accounts.email_verification', 'WARNING'):
            response = self.post()
        self.assertEqual((response.status_code, response.context['sent']), (200, True))

    def test_resend_requires_csrf(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(reverse('verification_resend'), {'email': 'a@example.com'}).status_code, 403)


# ══════════════════════════ Inicio de sesión pendiente ═════════════════════════

class PendingLoginTests(Base):
    def login_post(self, username='ana.perez', password=STRONG):
        return self.client.post(reverse('login'), {'username': username, 'password': password})

    def test_correct_password_of_a_pending_account_says_to_confirm_the_email(self):
        self.register()
        response = self.login_post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['email_pending'])
        self.assertEqual(response.context['resend_url'], reverse('verification_resend'))
        self.assertTrue(any('confirmar tu correo' in m for m in messages_of(response)))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_wrong_password_gives_the_generic_message_and_no_hint(self):
        self.register()
        response = self.login_post(password='Otra-clave-1234!')
        self.assertFalse(response.context['email_pending'])
        self.assertEqual(messages_of(response), ['Usuario o contraseña incorrectos.'])

    def test_unknown_user_gives_the_same_generic_message(self):
        response = self.login_post('no.existe', STRONG)
        self.assertFalse(response.context['email_pending'])
        self.assertEqual(messages_of(response), ['Usuario o contraseña incorrectos.'])

    def test_a_deactivated_account_is_not_treated_as_pending(self):
        User.objects.filter(pk=self.client_a.pk).update(is_active=False, deactivated_at=timezone.now())
        response = self.login_post('client_a', PASSWORD)
        self.assertFalse(response.context['email_pending'])
        self.assertEqual(messages_of(response), ['Usuario o contraseña incorrectos.'])

    def test_active_login_is_unchanged(self):
        self.assertRedirects(self.login_post('client_a', PASSWORD), reverse('dashboard'),
                             fetch_redirect_response=False)

    def test_pending_hint_is_not_given_while_axes_has_the_connection_locked_out(self):
        self.register()
        with override_settings(AXES_ENABLED=True, AXES_FAILURE_LIMIT=3,
                               AXES_CLIENT_IP_CALLABLE='apps.accounts.axes_utils.get_client_ip'):
            for _ in range(3):
                self.login_post(password='Otra-clave-1234!')
            response = self.login_post()          # la contraseña es CORRECTA, pero la conexión está bloqueada
        self.assertEqual(response.status_code, 429)
        self.assertNotIn('confirmar tu correo', response.content.decode())

    def test_login_page_offers_the_resend_link_only_when_pending(self):
        self.register()
        pending = self.login_post()
        self.assertContains(pending, reverse('verification_resend'))
        plain = self.client.get(reverse('login'))
        self.assertNotContains(plain, reverse('verification_resend'))


# ═══════════════════════════ Panel del entrenador ═════════════════════════════

class PanelVisibilityTests(Base):
    def make_pending(self):
        self.register()
        return User.objects.get(username='ana.perez')

    def test_pending_registrations_are_not_clients_for_anyone(self):
        user = self.make_pending()
        for trainer in (self.boss, self.trainer_a):
            self.assertFalse(clients_for_trainer(trainer).filter(pk=user.pk).exists())

    def test_boss_cannot_open_or_list_a_pending_registration(self):
        user = self.make_pending()
        self.login(self.boss)
        self.assertEqual(self.client.get(reverse('trainer_client_detail', args=[user.pk])).status_code, 404)
        for status in ('active', 'inactive', 'all'):
            response = self.client.get(reverse('trainer_clients') + f'?status={status}')
            self.assertNotIn(user, list(response.context['clients']), status)
        self.assertEqual(self.client.get(reverse('trainer_assign_client', args=[user.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('trainer_client_reactivate', args=[user.pk])).status_code, 404)

    def test_no_account_action_works_on_a_pending_registration(self):
        user = self.make_pending()
        for action in (services.choices.ACTION_DEACTIVATE, services.choices.ACTION_REACTIVATE,
                       services.choices.ACTION_ERASE):
            self.assertFalse(services.can_act(self.boss, user, action), action)
        with self.assertRaises(services.AccountNotAllowed) as caught:
            services.reactivate_account(user, self.boss)       # activarla así saltaría la verificación
        self.assertEqual(caught.exception.code, 'account_pending')
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_pending_registrations_do_not_count_on_the_dashboard(self):
        self.login(self.boss)
        before = self.client.get(reverse('trainer_dashboard')).context['total_clients']
        self.client.logout()
        self.make_pending()
        self.login(self.boss)
        self.assertEqual(self.client.get(reverse('trainer_dashboard')).context['total_clients'], before)

    def test_after_confirming_the_boss_sees_it_as_unassigned(self):
        self.register()
        self.confirm(link_in(mail.outbox[0]))
        user = User.objects.get(username='ana.perez')
        self.login(self.boss)
        self.assertEqual(self.client.get(reverse('trainer_client_detail', args=[user.pk])).status_code, 200)

    def test_state_helpers(self):
        user = self.make_pending()
        self.assertTrue(user.is_pending_registration)
        self.assertFalse(user.is_invitation_pending)
        self.assertTrue(User.objects.filter(pending_registration_q(), pk=user.pk).exists())
        User.objects.filter(pk=user.pk).update(deactivated_at=timezone.now())
        user.refresh_from_db()
        self.assertFalse(user.is_pending_registration)       # desactivada, no pendiente
        self.assertFalse(User.objects.filter(pending_registration_q(), pk=user.pk).exists())
        # Las cuentas de siempre (activas, con contraseña) no son ni lo uno ni lo otro
        for account in (self.client_a, self.trainer_a, self.boss):
            self.assertFalse(account.is_pending_registration)
            self.assertFalse(account.is_invitation_pending)


# ═══════════════════ Cuenta creada por el entrenador (invitación) ═════════════════════

class InvitationTests(Base):
    def add_client(self, **over):
        data = {'first_name': 'Nueva', 'last_name': 'Persona', 'email': 'nueva@example.com',
                'username': 'nueva.persona'}
        data.update(over)
        return self.client.post(reverse('trainer_add_client'), data)

    def invited(self):
        self.login(self.trainer_a)
        self.add_client()
        return User.objects.get(username='nueva.persona')

    def test_trainer_no_longer_sets_the_password(self):
        self.login(self.trainer_a)
        # Aunque alguien mande un campo `password`, se ignora: la cuenta nace sin contraseña utilizable
        response = self.add_client(password=STRONG)
        client = User.objects.get(username='nueva.persona')
        self.assertRedirects(response, reverse('trainer_client_detail', args=[client.pk]))
        self.assertFalse(client.has_usable_password())
        self.assertFalse(client.check_password(STRONG))
        self.assertTrue(client.is_active)
        self.assertIsNone(client.email_verified_at)
        self.assertTrue(client.is_invitation_pending)
        self.assertEqual(client.assigned_trainer, self.trainer_a)
        self.assertEqual(client.role, User.ROLE_MEMBER)

    def test_invitation_email_has_the_link_the_username_and_72_hours(self):
        self.invited()
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['nueva@example.com'])
        self.assertIn('/accounts/invitation/', link_in(message))
        self.assertIn('nueva.persona', message.body)
        self.assertIn('72 horas', message.body)
        self.assertNotIn('/accounts/verify/', message.body)

    def test_the_pending_client_is_visible_and_marked_for_the_trainer(self):
        client = self.invited()
        detail = self.client.get(reverse('trainer_client_detail', args=[client.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.context['client'].is_invitation_pending)
        self.assertContains(detail, 'Invitación pendiente')
        self.assertContains(detail, reverse('trainer_client_resend_invitation', args=[client.pk]))
        listing = self.client.get(reverse('trainer_clients'))
        self.assertIn(client, list(listing.context['clients']))
        self.assertContains(listing, 'Invitación pendiente')

    def test_trainer_can_activate_a_plan_while_the_invitation_is_pending(self):
        self.login(self.trainer_a)
        self.add_client(activate_plan=str(self.plan.pk))
        client = User.objects.get(username='nueva.persona')
        self.assertTrue(client.membership.is_valid)
        self.assertTrue(client.is_invitation_pending)

    def test_the_form_shows_no_password_field(self):
        self.login(self.trainer_a)
        page = self.client.get(reverse('trainer_add_client'))
        self.assertNotContains(page, 'name="password"')

    def test_other_trainers_cannot_see_the_pending_client(self):
        client = self.invited()
        self.login(self.trainer_b)
        self.assertEqual(self.client.get(reverse('trainer_client_detail', args=[client.pk])).status_code, 404)

    def test_the_invited_client_cannot_log_in_until_she_sets_a_password(self):
        self.invited()
        self.client.logout()
        for password in (PASSWORD, STRONG, '', '!'):
            response = self.client.post(reverse('login'), {'username': 'nueva.persona', 'password': password})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('_auth_user_id', self.client.session)

    def test_mail_failure_still_creates_the_account_and_warns(self):
        self.login(self.trainer_a)
        with mock.patch('apps.accounts.email_verification.send_mail', side_effect=OSError('x')), \
                self.assertLogs('apps.accounts.email_verification', 'WARNING'):
            response = self.add_client()
        client = User.objects.get(username='nueva.persona')
        self.assertRedirects(response, reverse('trainer_client_detail', args=[client.pk]), fetch_redirect_response=False)
        followed = self.client.get(reverse('trainer_client_detail', args=[client.pk]))
        self.assertTrue(any('Reenviar invitación' in m for m in messages_of(followed)))

    # ── La clienta crea su contraseña ──

    def open_invitation(self):
        client = self.invited()
        url = link_in(mail.outbox[0])
        self.client.logout()
        page = self.client.get(path_of(url))
        return client, url, page

    def test_the_link_shows_the_form_with_the_username(self):
        client, url, page = self.open_invitation()
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, 'accounts/invitation_set_password.html')
        self.assertEqual(page.context['username'], 'nueva.persona')
        self.assertEqual(page.context['token'], token_of(url))
        self.assertEqual(page['Referrer-Policy'], 'same-origin')

    def set_password(self, page, password1=STRONG, password2=None):
        return self.client.post(page.context['accept_url'], {
            'token': page.context['token'], 'password1': password1,
            'password2': password1 if password2 is None else password2})

    def test_setting_the_password_verifies_the_account_and_allows_login(self):
        client, url, page = self.open_invitation()
        response = self.set_password(page)
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        client.refresh_from_db()
        self.assertTrue(client.check_password(STRONG))
        self.assertIsNotNone(client.email_verified_at)
        self.assertFalse(client.is_invitation_pending)
        self.assertTrue(client.is_active)
        self.assertNotIn('_auth_user_id', self.client.session)
        login = self.client.post(reverse('login'), {'username': 'nueva.persona', 'password': STRONG})
        self.assertRedirects(login, reverse('dashboard'), fetch_redirect_response=False)

    def test_weak_or_mismatched_passwords_are_rejected_and_never_echoed(self):
        client, url, page = self.open_invitation()
        for password1, password2, field in (
                ('password', None, 'password1'), ('12345678', None, 'password1'),
                ('corta', None, 'password1'), ('nueva.persona1', None, 'password1'),
                (STRONG, STRONG + 'x', 'password2'), ('Aa1!' * 100, None, 'password1')):
            with self.subTest(password1=password1[:12]):
                response = self.set_password(page, password1, password2)
                self.assertEqual(response.status_code, 200)
                self.assertIn(field, response.context['errors'])
                self.assertEqual(response.context['token'], token_of(url))
        client.refresh_from_db()
        self.assertTrue(client.is_invitation_pending)

    def test_the_form_never_gets_a_password_back_in_the_context_or_the_page(self):
        client, url, page = self.open_invitation()
        response = self.set_password(page, STRONG, STRONG + 'x')
        self.assertEqual(response.context['form'], {})
        self.assertNotContains(response, STRONG)

    def test_the_link_cannot_be_used_twice(self):
        client, url, page = self.open_invitation()
        self.set_password(page)
        again = self.client.get(path_of(url))
        self.assertEqual(again.context['reason'], 'used')
        second = self.set_password(page, 'Otra-clave-7788!')
        self.assertEqual(second.context['reason'], 'used')
        client.refresh_from_db()
        self.assertTrue(client.check_password(STRONG))          # la segunda no cambió nada

    def test_expired_invitation(self):
        client, url, page = self.open_invitation()
        with mock.patch.object(ev, 'INVITE_MAX_AGE_SECONDS', -1):
            self.assertEqual(self.client.get(path_of(url)).context['reason'], 'expired')
            self.assertEqual(self.set_password(page).context['reason'], 'expired')
        client.refresh_from_db()
        self.assertTrue(client.is_invitation_pending)

    def test_the_invitation_is_valid_for_72_hours_and_the_confirmation_for_24(self):
        self.assertEqual(ev.INVITE_MAX_AGE_SECONDS, 72 * 3600)
        self.assertEqual(ev.VERIFY_MAX_AGE_SECONDS, 24 * 3600)

    def test_a_confirmation_link_does_not_work_as_an_invitation_and_vice_versa(self):
        client = self.invited()
        verify_token = ev.make_token(client, ev.PURPOSE_VERIFY)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('invitation_accept', args=[verify_token])).context['reason'],
                         'invalid')
        self.register()
        pending = User.objects.get(username='ana.perez')
        invite_token = ev.make_token(pending, ev.PURPOSE_INVITE)
        self.assertEqual(self.client.get(reverse('email_verify', args=[invite_token])).context['reason'],
                         'invalid')
        # y aunque la firma fuera buena, una cuenta registrada no está en estado de invitación
        self.assertEqual(self.client.get(reverse('invitation_accept', args=[invite_token])).context['reason'],
                         'used')

    def test_changing_the_email_kills_the_old_invitation_and_sends_a_new_one(self):
        client, url, page = self.open_invitation()
        self.login(self.trainer_a)
        mail.outbox.clear()
        response = self.client.post(reverse('trainer_client_edit', args=[client.pk]), {
            'first_name': 'Nueva', 'last_name': 'Persona', 'email': 'corregido@example.com',
            'username': 'nueva.persona'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['corregido@example.com'])
        self.client.logout()
        self.assertEqual(self.client.get(path_of(url)).context['reason'], 'used')
        fresh = self.client.get(path_of(link_in(mail.outbox[0])))
        self.assertEqual(fresh.status_code, 200)
        self.assertTemplateUsed(fresh, 'accounts/invitation_set_password.html')

    def test_editing_without_changing_the_email_sends_nothing(self):
        client = self.invited()
        mail.outbox.clear()
        self.client.post(reverse('trainer_client_edit', args=[client.pk]), {
            'first_name': 'Nueva', 'last_name': 'Cambiada', 'email': 'nueva@example.com',
            'username': 'nueva.persona'})
        self.assertEqual(len(mail.outbox), 0)

    def test_trainer_cannot_set_a_password_for_a_pending_invitation(self):
        client = self.invited()
        response = self.client.post(reverse('trainer_client_edit', args=[client.pk]), {
            'first_name': 'Nueva', 'last_name': 'Persona', 'email': 'nueva@example.com',
            'username': 'nueva.persona', 'new_password': STRONG})
        self.assertEqual(response.status_code, 200)
        self.assertIn('new_password', response.context['errors'])
        client.refresh_from_db()
        self.assertTrue(client.is_invitation_pending)
        self.assertFalse(client.has_usable_password())

    def test_trainer_can_still_set_a_password_for_a_normal_client(self):
        self.login(self.trainer_a)
        response = self.client.post(reverse('trainer_client_edit', args=[self.client_a.pk]), {
            'first_name': 'Cliente', 'last_name': 'A', 'email': 'client_a@example.com',
            'username': 'client_a', 'new_password': STRONG})
        self.assertEqual(response.status_code, 302)
        self.client_a.refresh_from_db()
        self.assertTrue(self.client_a.check_password(STRONG))

    def test_password_reset_does_not_email_pending_accounts(self):
        # Django solo escribe a cuentas activas con contraseña utilizable: la invitación y el registro
        # pendientes usan sus propios enlaces («Reenviar el correo»)
        self.invited()
        self.client.logout()
        self.register()
        mail.outbox.clear()
        cache.clear()
        for email in ('nueva@example.com', 'ana.perez@example.com'):
            self.assertEqual(self.client.post(reverse('password_reset'), {'email': email}).status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

    def test_accepting_requires_csrf(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(reverse('invitation_set_password'), {'token': 'x'}).status_code, 403)

    def test_accept_endpoint_is_post_only(self):
        self.assertEqual(self.client.get(reverse('invitation_set_password')).status_code, 405)


class ResendInvitationTests(Base):
    def setUp(self):
        super().setUp()
        self.login(self.trainer_a)
        self.client.post(reverse('trainer_add_client'), {
            'first_name': 'Nueva', 'last_name': 'Persona', 'email': 'nueva@example.com',
            'username': 'nueva.persona'})
        self.target = User.objects.get(username='nueva.persona')
        self.url = reverse('trainer_client_resend_invitation', args=[self.target.pk])
        mail.outbox.clear()

    def test_owner_trainer_resends_the_invitation(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse('trainer_client_detail', args=[self.target.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['nueva@example.com'])
        self.assertIn('/accounts/invitation/', link_in(mail.outbox[0]))

    def test_the_new_invitation_works(self):
        self.client.post(self.url)
        self.client.logout()
        page = self.client.get(path_of(link_in(mail.outbox[0])))
        self.assertTemplateUsed(page, 'accounts/invitation_set_password.html')

    def test_boss_can_resend_too(self):
        self.login(self.boss)
        self.client.post(self.url)
        self.assertEqual(len(mail.outbox), 1)

    def test_another_trainer_gets_404_and_nothing_is_sent(self):
        self.login(self.trainer_b)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_only_post_is_accepted(self):
        for method in ('get', 'put', 'delete'):
            self.assertEqual(getattr(self.client, method)(self.url).status_code, 405, method)
        self.assertEqual(len(mail.outbox), 0)

    def test_anonymous_goes_to_login_and_a_client_gets_403(self):
        self.client.logout()
        self.assertRedirects(self.client.post(self.url), reverse('login'), fetch_redirect_response=False)
        self.login(self.client_a)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertEqual(len(mail.outbox), 0)

    def test_requires_csrf(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.trainer_a, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(strict.post(self.url).status_code, 403)

    def test_it_is_rate_limited_per_client(self):
        for _ in range(ev.INVITE_RESEND_CLIENT_LIMIT):
            self.client.post(self.url)
        self.assertEqual(len(mail.outbox), ev.INVITE_RESEND_CLIENT_LIMIT)
        with self.assertLogs('apps.accounts.verification_views', 'WARNING'):
            response = self.client.post(self.url, follow=True)
        self.assertEqual(len(mail.outbox), ev.INVITE_RESEND_CLIENT_LIMIT)
        self.assertTrue(any('varias veces' in m for m in messages_of(response)))

    def test_it_is_rate_limited_per_trainer(self):
        with mock.patch.object(ev, 'INVITE_RESEND_ACTOR_LIMIT', 1),                 self.assertLogs('apps.accounts.verification_views', 'WARNING'):
            self.client.post(self.url)
            self.client.post(self.url)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_client_without_a_pending_invitation_gets_nothing(self):
        response = self.client.post(reverse('trainer_client_resend_invitation', args=[self.client_a.pk]),
                                    follow=True)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(any('ya no tiene una invitación pendiente' in m for m in messages_of(response)))

    def test_a_deactivated_client_is_read_only(self):
        User.objects.filter(pk=self.target.pk).update(is_active=False, deactivated_at=timezone.now())
        response = self.client.post(self.url, follow=True)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(any('desactivada' in m for m in messages_of(response)))

    def test_mail_failure_is_reported(self):
        with mock.patch('apps.accounts.email_verification.send_mail', side_effect=OSError('x')), \
                self.assertLogs('apps.accounts.email_verification', 'WARNING'):
            response = self.client.post(self.url, follow=True)
        self.assertTrue(any('No pudimos enviar' in m for m in messages_of(response)))


# ═════════════════════════ Equipo: cuentas ya verificadas ═════════════════════════

class TeamAccountsTests(Base):
    def test_trainers_created_from_the_panel_are_verified(self):
        self.login(self.boss)
        response = self.client.post(reverse('trainer_create_trainer'), {
            'first_name': 'Nuevo', 'last_name': 'Entrenador', 'email': 'nuevo.ent@example.com',
            'username': 'nuevo.ent', 'password': STRONG, 'password2': STRONG})
        self.assertEqual(response.status_code, 302)
        trainer = User.objects.get(username='nuevo.ent')
        self.assertIsNotNone(trainer.email_verified_at)
        self.assertTrue(trainer.is_active)
        self.assertEqual(len(mail.outbox), 0)

    def test_create_trainer_command_marks_the_account_verified(self):
        call_command('create_trainer', '--username', 'entrenadora.nueva', '--email', 'en@example.com',
                     '--first-name', 'Elena', '--last-name', 'Gómez', '--password', STRONG, stdout=StringIO())
        self.assertIsNotNone(User.objects.get(username='entrenadora.nueva').email_verified_at)


# ═══════════════════════ Comando purge_unverified_accounts ═══════════════════════

class PurgeUnverifiedAccountsTests(Base):
    def setUp(self):
        super().setUp()
        self.old = timezone.now() - timedelta(days=10)

    def pending(self, username, days_old=10, **extra):
        fields = {'role': User.ROLE_MEMBER, 'is_active': False, **extra}
        user = User.objects.create_user(username=username, email=f'{username}@example.com',
                                        password=PASSWORD, **fields)
        User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(days=days_old))
        return user

    def run_command(self, *args):
        out = StringIO()
        call_command('purge_unverified_accounts', *args, stdout=out)
        return out.getvalue()

    def test_default_only_counts_and_deletes_nothing(self):
        self.pending('vieja.uno')
        self.pending('vieja.dos')
        output = self.run_command()
        self.assertIn('2', output)
        self.assertIn('No se borró nada', output)
        self.assertEqual(User.objects.filter(username__startswith='vieja').count(), 2)

    def test_delete_removes_only_old_pending_accounts_without_data(self):
        old = self.pending('vieja.sola')
        recent = self.pending('reciente', days_old=2)
        confirmed = self.pending('confirmada')
        User.objects.filter(pk=confirmed.pk).update(email_verified_at=timezone.now(), is_active=True)
        deactivated = self.pending('desactivada')
        User.objects.filter(pk=deactivated.pk).update(deactivated_at=timezone.now(), email_verified_at=self.old)
        invited = User.objects.create_user(username='invitada', email='i@example.com', password=None,
                                           role=User.ROLE_MEMBER)
        User.objects.filter(pk=invited.pk).update(date_joined=self.old)
        output = self.run_command('--delete')
        self.assertIn('Borradas: 1', output)
        self.assertFalse(User.objects.filter(pk=old.pk).exists())
        for survivor in (recent, confirmed, deactivated, invited, self.boss, self.trainer_a, self.client_a):
            self.assertTrue(User.objects.filter(pk=survivor.pk).exists(), survivor.username)

    def test_an_account_with_any_related_row_is_never_deleted(self):
        protected = self.pending('con.datos')
        AccountAuditLog.objects.create(actor=self.boss, target=protected, target_role=User.ROLE_MEMBER,
                                       action='deactivate')
        free = self.pending('sin.datos')
        output = self.run_command('--delete')
        self.assertTrue(User.objects.filter(pk=protected.pk).exists())
        self.assertFalse(User.objects.filter(pk=free.pk).exists())
        self.assertIn('Con alguna relación', output)

    def test_trainers_and_staff_are_never_touched(self):
        staff = self.pending('personal', is_staff=True)
        trainer = self.pending('entrenador.pendiente', role=User.ROLE_TRAINER)
        self.run_command('--delete')
        self.assertTrue(User.objects.filter(pk=staff.pk).exists())
        self.assertTrue(User.objects.filter(pk=trainer.pk).exists())

    def test_days_option_changes_the_cutoff(self):
        self.pending('de.cinco.dias', days_old=5)
        self.assertTrue(self.run_command().splitlines()[0].endswith(': 0.'))
        self.assertTrue(self.run_command('--days', '3').splitlines()[0].endswith(': 1.'))

    def test_days_must_be_at_least_one(self):
        with self.assertRaises(CommandError):
            self.run_command('--days', '0')

    def test_output_and_logs_never_name_the_accounts(self):
        self.pending('nombre.secreto')
        output = self.run_command('--delete')
        self.assertNotIn('nombre.secreto', output)
        self.assertNotIn('example.com', output)

    def test_login_attempt_records_of_deleted_accounts_are_removed(self):
        from axes.models import AccessAttempt
        self.pending('con.intentos')
        AccessAttempt.objects.create(username='con.intentos', ip_address='203.0.113.5', user_agent='x',
                                     failures_since_start=2)
        self.run_command('--delete')
        self.assertFalse(AccessAttempt.objects.filter(username='con.intentos').exists())

    def test_safe_to_repeat(self):
        self.pending('vieja.sola')
        self.run_command('--delete')
        self.assertIn('Borradas: 0', self.run_command('--delete'))


# ═══════════════════════════════ Turnstile ═══════════════════════════════════════

class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Un abridor falso de `urllib`: guarda lo que se le pidió y responde (o falla) como se le diga."""

    def __init__(self, body=b'{"success": true}', error=None):
        self.body, self.error, self.requests, self.timeouts = body, error, [], []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.error:
            raise self.error
        return FakeResponse(self.body)


KEYS = dict(TURNSTILE_SITE_KEY='site-key-publica', TURNSTILE_SECRET_KEY='secreto-solo-servidor')


class TurnstileUnitTests(Base):
    @override_settings(**KEYS)
    def test_enabled_needs_both_keys(self):
        self.assertTrue(turnstile.enabled())
        with override_settings(TURNSTILE_SECRET_KEY=''):
            self.assertFalse(turnstile.enabled())
            self.assertTrue(turnstile.half_configured())
            self.assertEqual(turnstile.widget_context(), {'turnstile_site_key': ''})
        with override_settings(TURNSTILE_SITE_KEY='', TURNSTILE_SECRET_KEY=''):
            self.assertFalse(turnstile.enabled())
            self.assertFalse(turnstile.half_configured())

    @override_settings(TURNSTILE_SITE_KEY='', TURNSTILE_SECRET_KEY='')
    def test_off_by_default_without_network(self):
        self.assertFalse(turnstile.enabled())
        opener = FakeOpener(error=AssertionError('no debe tocar la red'))
        self.assertTrue(turnstile.verify('', '203.0.113.1', opener=opener))
        self.assertEqual(opener.requests, [])
        self.assertEqual(turnstile.widget_context(), {'turnstile_site_key': ''})

    @override_settings(**KEYS)
    def test_success_and_the_exact_request_sent_to_cloudflare(self):
        opener = FakeOpener()
        self.assertTrue(turnstile.verify('token-del-widget', '203.0.113.7', opener=opener))
        request = opener.requests[0]
        self.assertEqual(request.full_url, 'https://challenges.cloudflare.com/turnstile/v0/siteverify')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(request.get_header('Content-type'), 'application/x-www-form-urlencoded')
        sent = urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(sent, {'secret': ['secreto-solo-servidor'], 'response': ['token-del-widget'],
                                'remoteip': ['203.0.113.7']})
        self.assertEqual(opener.timeouts, [turnstile.TIMEOUT_SECONDS])
        self.assertLessEqual(turnstile.TIMEOUT_SECONDS, 10)

    @override_settings(**KEYS)
    def test_remote_ip_is_optional(self):
        opener = FakeOpener()
        turnstile.verify('t', None, opener=opener)
        self.assertNotIn('remoteip', urllib.parse.parse_qs(opener.requests[0].data.decode()))

    @override_settings(**KEYS)
    def test_rejections_and_failures_all_fail_closed(self):
        cases = {
            'success falso': FakeOpener(b'{"success": false, "error-codes": ["invalid-input-response"]}'),
            'success no booleano': FakeOpener(b'{"success": "true"}'),
            'sin success': FakeOpener(b'{}'),
            'no es JSON': FakeOpener(b'<html>502</html>'),
            'JSON que no es un objeto': FakeOpener(b'[true]'),
            'respuesta enorme': FakeOpener(b'{"success": true, "x": "' + b'a' * 70000 + b'"}'),
            'red caída': FakeOpener(error=OSError('sin red')),
            'tiempo agotado': FakeOpener(error=TimeoutError('lento')),
            'error HTTP': FakeOpener(error=Exception('HTTP 500')),
        }
        with self.assertLogs('apps.accounts.turnstile', 'WARNING'):        # los fallos de red se registran
            for name, opener in cases.items():
                with self.subTest(name):
                    self.assertFalse(turnstile.verify('token', '203.0.113.7', opener=opener))

    @override_settings(**KEYS)
    def test_missing_or_oversized_tokens_never_reach_cloudflare(self):
        opener = FakeOpener()
        for token in (None, '', '   ', 5, 'a' * (turnstile.MAX_TOKEN_LENGTH + 1)):
            self.assertFalse(turnstile.verify(token, '203.0.113.7', opener=opener), repr(token)[:20])
        self.assertEqual(opener.requests, [])

    @override_settings(**KEYS)
    def test_logs_never_contain_the_token_or_the_secret(self):
        with self.assertLogs('apps.accounts.turnstile', 'WARNING') as logs:
            turnstile.verify('token-super-privado', '203.0.113.7',
                             opener=FakeOpener(error=OSError('fallo con token-super-privado y secreto-solo-servidor')))
        text = '\n'.join(logs.output)
        for secret in ('token-super-privado', 'secreto-solo-servidor'):
            self.assertNotIn(secret, text)

    @override_settings(**KEYS)
    def test_redirects_are_not_followed(self):
        handler = turnstile._NoRedirect()
        self.assertIsNone(handler.redirect_request(mock.Mock(), None, 302, 'Found', {}, 'https://otro.example/'))


@override_settings(**KEYS)
class TurnstileInViewsTests(Base):
    def opener_patch(self, opener):
        build = mock.Mock()
        build.return_value.open = opener
        return mock.patch('apps.accounts.turnstile.urllib.request.build_opener', build)

    def test_register_page_gets_the_public_key_but_never_the_secret(self):
        response = self.client.get(reverse('register'))
        self.assertEqual(response.context['turnstile_site_key'], 'site-key-publica')
        self.assertContains(response, 'cf-turnstile')
        self.assertContains(response, 'challenges.cloudflare.com/turnstile/v0/api.js')
        self.assertNotContains(response, 'secreto-solo-servidor')

    def test_register_without_a_token_is_rejected_before_anything_else(self):
        opener = FakeOpener(error=AssertionError('no debe consultar sin token'))
        with self.opener_patch(opener):
            response = self.register()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['errors'], {'captcha': turnstile.MESSAGE})
        self.assertFalse(User.objects.filter(username='ana.perez').exists())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(opener.requests, [])

    def test_register_with_a_valid_token_goes_through(self):
        opener = FakeOpener()
        with self.opener_patch(opener):
            response = self.client.post(reverse('register'), {**self.payload(), turnstile.FORM_FIELD: 'tok'})
        self.assertRedirects(response, reverse('register_check_email'), fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(username='ana.perez').exists())
        self.assertEqual(urllib.parse.parse_qs(opener.requests[0].data.decode())['response'], ['tok'])

    def test_register_with_a_rejected_token_creates_nothing(self):
        with self.opener_patch(FakeOpener(b'{"success": false}')):
            response = self.client.post(reverse('register'), {**self.payload(), turnstile.FORM_FIELD: 'malo'})
        self.assertEqual(response.context['errors'], {'captcha': turnstile.MESSAGE})
        self.assertFalse(User.objects.filter(username='ana.perez').exists())

    def test_register_fails_closed_when_cloudflare_is_unreachable(self):
        with self.opener_patch(FakeOpener(error=OSError('sin red'))), \
                self.assertLogs('apps.accounts.turnstile', 'WARNING'):
            response = self.client.post(reverse('register'), {**self.payload(), turnstile.FORM_FIELD: 'tok'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['errors'], {'captcha': turnstile.MESSAGE})
        self.assertFalse(User.objects.filter(username='ana.perez').exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_resend_page_and_post_require_the_token_too(self):
        page = self.client.get(reverse('verification_resend'))
        self.assertEqual(page.context['turnstile_site_key'], 'site-key-publica')
        self.assertContains(page, 'challenges.cloudflare.com/turnstile/v0/api.js')
        self.register_pending_without_captcha()
        with self.opener_patch(FakeOpener(error=AssertionError('sin token no se consulta'))):
            response = self.client.post(reverse('verification_resend'), {'email': 'ana.perez@example.com'})
        self.assertEqual(response.context['errors'], {'captcha': turnstile.MESSAGE})
        self.assertEqual(len(mail.outbox), 0)
        with self.opener_patch(FakeOpener()):
            ok = self.client.post(reverse('verification_resend'),
                                  {'email': 'ana.perez@example.com', turnstile.FORM_FIELD: 'tok'})
        self.assertTrue(ok.context['sent'])
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_fails_closed_when_cloudflare_is_unreachable(self):
        self.register_pending_without_captcha()
        with self.opener_patch(FakeOpener(error=OSError('x'))), self.assertLogs('apps.accounts.turnstile', 'WARNING'):
            response = self.client.post(reverse('verification_resend'),
                                        {'email': 'ana.perez@example.com', turnstile.FORM_FIELD: 'tok'})
        self.assertEqual(response.context['errors'], {'captcha': turnstile.MESSAGE})
        self.assertEqual(len(mail.outbox), 0)

    def register_pending_without_captcha(self):
        User.objects.create_user(username='ana.perez', email='ana.perez@example.com', password=STRONG,
                                 role=User.ROLE_MEMBER, is_active=False)

    def test_half_configured_turnstile_stays_off(self):
        with override_settings(TURNSTILE_SECRET_KEY=''):
            response = self.client.get(reverse('register'))
            self.assertEqual(response.context['turnstile_site_key'], '')
            self.assertNotContains(response, 'cf-turnstile')
            self.assertRedirects(self.register(), reverse('register_check_email'), fetch_redirect_response=False)


@override_settings(TURNSTILE_SITE_KEY='', TURNSTILE_SECRET_KEY='')
class TurnstileOffTests(Base):
    def test_registration_and_resend_work_without_any_token_or_network(self):
        with mock.patch('apps.accounts.turnstile.urllib.request.build_opener',
                        side_effect=AssertionError('Turnstile apagado: nada de red')):
            self.assertEqual(self.register().status_code, 302)
            page = self.client.get(reverse('register'))
            self.assertEqual(page.context['turnstile_site_key'], '')
            self.assertNotContains(page, 'cf-turnstile')
            self.assertNotContains(page, 'challenges.cloudflare.com')
            response = self.client.post(reverse('verification_resend'), {'email': 'ana.perez@example.com'})
            self.assertTrue(response.context['sent'])


# ═════════════════════════════ Migración de datos ═════════════════════════════════

class Migration0007Tests(TransactionTestCase):
    """La migración 0007 deja a TODAS las cuentas existentes como verificadas y es reversible."""
    serialized_rollback = True
    before = [('accounts', '0006_transfer_admin_action')]
    after = [('accounts', '0007_email_verification')]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())     # deja la base al día para lo que sigue
        super().tearDown()

    def test_every_existing_account_is_marked_verified_with_its_join_date(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        Old = executor.loader.project_state(self.before).apps.get_model('accounts', 'User')
        joined = timezone.now() - timedelta(days=400)
        ids = {}
        for name, extra in (('activa', {}), ('desactivada', {'is_active': False}),
                            ('entrenadora', {'role': 'trainer', 'is_staff': True}),
                            ('dueña', {'role': 'trainer', 'is_staff': True, 'is_superuser': True}),
                            ('sin.contraseña', {'password': '!sin-contrasena'})):
            ids[name] = Old.objects.create(username=name, password=extra.pop('password', 'x'),
                                           date_joined=joined, **extra).pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        New = executor.loader.project_state(self.after).apps.get_model('accounts', 'User')
        for name, pk in ids.items():
            row = New.objects.get(pk=pk)
            self.assertEqual(row.email_verified_at, joined, name)
        # Nadie queda en el estado «registro pendiente»
        pending = New.objects.filter(is_active=False, email_verified_at__isnull=True, deactivated_at__isnull=True)
        self.assertFalse(pending.exists())

    def test_the_migration_reverts(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        columns = {col.name for col in connection.introspection.get_table_description(
            connection.cursor(), 'accounts_user')}
        self.assertNotIn('email_verified_at', columns)

    def test_the_model_matches_the_migration(self):
        executor = MigrationExecutor(connection)
        self.assertFalse(executor.loader.detect_conflicts())
        field = User._meta.get_field('email_verified_at')
        self.assertTrue(field.null)


class ShowInvitationLinkTests(Base):
    """La entrenadora ve el enlace de invitación en pantalla (una sola vez) por si el correo no llega."""

    def setUp(self):
        super().setUp()
        self.login(self.trainer_a)
        self.created = self.client.post(reverse('trainer_add_client'), {
            'first_name': 'Nueva', 'last_name': 'Persona', 'email': 'nueva@example.com',
            'username': 'nueva.persona'})
        self.target = User.objects.get(username='nueva.persona')
        self.detail = reverse('trainer_client_detail', args=[self.target.pk])

    def test_link_is_shown_once_after_creating_and_it_works(self):
        page = self.client.get(self.detail)
        link = page.context['invitation_link']
        self.assertIn('/accounts/invitation/', link)
        self.assertContains(page, link)
        self.assertEqual(page['Cache-Control'], 'no-store')
        self.assertIsNone(self.client.get(self.detail).context['invitation_link'])
        self.client.logout()
        accept = self.client.get(path_of(link))
        self.assertTemplateUsed(accept, 'accounts/invitation_set_password.html')

    def test_button_shows_the_link_again(self):
        self.client.get(self.detail)   # el enlace de la creación se consume
        response = self.client.post(reverse('trainer_client_invitation_link', args=[self.target.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/invitation/', self.client.get(self.detail).context['invitation_link'])

    def test_other_trainer_gets_404(self):
        self.login(self.trainer_b)
        response = self.client.post(reverse('trainer_client_invitation_link', args=[self.target.pk]))
        self.assertEqual(response.status_code, 404)

    def test_link_is_not_shown_after_the_client_set_a_password(self):
        link = self.client.get(self.detail).context['invitation_link']
        self.client.post(reverse('trainer_client_invitation_link', args=[self.target.pk]))
        self.target.set_password('Contrasena-Segura-91!')
        self.target.save()
        self.assertIsNone(self.client.get(self.detail).context['invitation_link'])

    def test_no_link_without_an_email(self):
        User.objects.filter(pk=self.target.pk).update(email='')
        self.client.get(self.detail)
        self.client.post(reverse('trainer_client_invitation_link', args=[self.target.pk]))
        self.assertIsNone(self.client.get(self.detail).context['invitation_link'])
