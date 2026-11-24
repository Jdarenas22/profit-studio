"""Transferir la administración (docs/contracts/transferir-administracion.md).

Escenario base (apps/accounts/tests/helpers.py): boss (superusuaria), trainer_a, trainer_b,
client_a (de A), client_b (de B), client_free (sin asignar).

La pantalla es un esqueleto hasta que frontend-architect entregue T-01: estas pruebas comprueban el
CONTEXTO y los efectos, no el HTML.
"""
import os
from io import StringIO
from unittest import mock

from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from apps.accounts import choices, services
from apps.accounts.models import AccountAuditLog, User
from apps.accounts.views import TRANSFER_ADMIN_ATTEMPTS_LIMIT

from .helpers import PASSWORD, ScenarioTestCase, make_user


def flashes(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


class TransferMixin:
    url_name = 'trainer_admin_transfer'

    def setUp(self):
        super().setUp()
        cache.clear()      # el límite de intentos vive en la caché
        self.addCleanup(cache.clear)

    @property
    def url(self):
        return reverse(self.url_name)

    @staticmethod
    def fresh(user):
        return User.objects.get(pk=user.pk)

    def post_data(self, target=None, **overrides):
        data = {'new_admin': str((target or self.trainer_a).pk), 'password': PASSWORD, 'confirm': 'on'}
        data.update(overrides)
        return {key: value for key, value in data.items() if value is not None}

    def assert_nothing_changed(self):
        self.assertTrue(self.fresh(self.boss).is_superuser)
        self.assertFalse(self.fresh(self.trainer_a).is_superuser)
        self.assertFalse(self.fresh(self.trainer_b).is_superuser)
        self.assertFalse(AccountAuditLog.objects.exists())

    def superusers(self):
        return set(User.objects.filter(is_superuser=True).values_list('username', flat=True))


# ─── Permisos y métodos ───────────────────────────────────────────────────────

class TransferRoutePermissionTests(TransferMixin, ScenarioTestCase):
    def test_anonymous_goes_to_login(self):
        for method in ('get', 'post'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(self.url, self.post_data() if method == 'post' else None)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response['Location'])
        self.assert_nothing_changed()

    def test_member_gets_403(self):
        self.login(self.client_a)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.post(self.url, self.post_data()).status_code, 403)
        self.assert_nothing_changed()

    def test_regular_trainer_cannot_open_or_submit(self):
        """Un entrenador común (no superusuario) vuelve a su panel con el aviso de permiso."""
        self.login(self.trainer_a)
        for method in ('get', 'post'):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    self.url, self.post_data(target=self.trainer_b) if method == 'post' else None)
                self.assertRedirects(response, reverse('trainer_dashboard'), fetch_redirect_response=False)
                self.assertIn('No tienes permiso para acceder a esta sección.', flashes(response))
        self.assertFalse(self.fresh(self.trainer_a).is_superuser)
        self.assertFalse(self.fresh(self.trainer_b).is_superuser)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_other_methods_are_not_allowed(self):
        self.login(self.boss)
        for method in ('put', 'patch', 'delete'):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.client, method)(self.url).status_code, 405)
        self.assert_nothing_changed()

    def test_post_without_csrf_is_rejected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.boss, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(csrf_client.post(self.url, self.post_data()).status_code, 403)
        self.assert_nothing_changed()

    def test_get_never_changes_anything(self):
        self.login(self.boss)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assert_nothing_changed()


# ─── La pantalla ──────────────────────────────────────────────────────────────

class TransferFormScreenTests(TransferMixin, ScenarioTestCase):
    def test_get_context_and_cache_header(self):
        User.objects.filter(pk=self.client_free.pk).update(assigned_trainer=self.boss)
        inactive = make_user('trainer_off', User.ROLE_TRAINER, is_staff=True, is_active=False)
        self.login(self.boss)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'trainer/admin_transfer.html')
        self.assertIn('no-store', response['Cache-Control'])
        targets = response.context['targets']
        self.assertEqual({u.username for u in targets}, {'trainer_a', 'trainer_b'})
        self.assertNotIn(inactive, targets)
        self.assertNotIn(self.boss, targets)
        self.assertNotIn(self.client_a, targets)
        self.assertEqual(response.context['kept_clients_count'], 1)
        self.assertEqual(response.context['errors'], {})
        self.assertContains(response, 'autocomplete="current-password"')
        self.assertContains(response, 'name="confirm"')
        self.assertContains(response, 'csrfmiddlewaretoken')

    def test_lists_only_eligible_trainers(self):
        other_boss = make_user('boss2', User.ROLE_TRAINER, is_staff=True, is_superuser=True)
        erased = make_user('trainer_gone', User.ROLE_TRAINER, is_staff=True)
        services.erase_account(erased, self.boss, choices.REASON_LEFT)
        self.login(self.boss)
        usernames = {u.username for u in self.client.get(self.url).context['targets']}
        self.assertNotIn(other_boss.username, usernames)
        self.assertNotIn(erased.username, usernames)   # su usuario ahora es la lápida
        self.assertFalse([name for name in usernames if name.startswith('cuenta-eliminada')])
        self.assertEqual(usernames, {'trainer_a', 'trainer_b'})

    def test_without_other_trainers_the_form_is_not_offered(self):
        User.objects.filter(pk__in=[self.trainer_a.pk, self.trainer_b.pk]).update(is_active=False)
        self.login(self.boss)
        response = self.client.get(self.url)
        self.assertEqual(response.context['targets'], [])
        self.assertNotContains(response, 'name="password"')
        self.assertContains(response, reverse('trainer_create_trainer'))


# ─── Éxito ────────────────────────────────────────────────────────────────────

class TransferSuccessTests(TransferMixin, ScenarioTestCase):
    def test_transfers_flags_audits_and_redirects(self):
        self.login(self.boss)
        response = self.client.post(self.url, self.post_data(self.trainer_a))
        self.assertRedirects(response, reverse('trainer_dashboard'), fetch_redirect_response=False)
        messages_ = flashes(response)
        self.assertEqual(len(messages_), 1)
        self.assertIn('Traspasaste la administración a Trainer_a Prueba', messages_[0])

        new, old = self.fresh(self.trainer_a), self.fresh(self.boss)
        self.assertTrue(new.is_superuser and new.is_staff and new.is_active)
        self.assertFalse(old.is_superuser)
        self.assertTrue(old.is_staff)                 # un entrenador común sigue con is_staff=True
        self.assertTrue(old.is_trainer and old.is_active)
        self.assertEqual(self.superusers(), {'trainer_a'})

        log = AccountAuditLog.objects.get()
        self.assertEqual((log.actor_id, log.target_id), (self.boss.pk, self.trainer_a.pk))
        self.assertEqual(log.action, choices.ACTION_TRANSFER_ADMIN)
        self.assertEqual(log.target_role, User.ROLE_TRAINER)
        self.assertEqual((log.reason, log.reassigned_to_id, log.reassigned_count), ('', None, 0))
        log.full_clean()        # 'transfer_admin' cabe en la columna y está en la lista de acciones

    def test_actor_keeps_clients_and_nothing_is_reassigned(self):
        User.objects.filter(pk=self.client_free.pk).update(assigned_trainer=self.boss)
        before = dict(User.objects.filter(role=User.ROLE_MEMBER).values_list('pk', 'assigned_trainer_id'))
        self.login(self.boss)
        self.client.post(self.url, self.post_data(self.trainer_a))
        after = dict(User.objects.filter(role=User.ROLE_MEMBER).values_list('pk', 'assigned_trainer_id'))
        self.assertEqual(before, after)
        self.assertEqual(self.fresh(self.client_free).assigned_trainer_id, self.boss.pk)

    def test_passwords_and_activity_do_not_change(self):
        hashes = {u.pk: self.fresh(u).password for u in (self.boss, self.trainer_a)}
        self.login(self.boss)
        self.client.post(self.url, self.post_data(self.trainer_a))
        for user in (self.boss, self.trainer_a):
            fresh = self.fresh(user)
            self.assertEqual(fresh.password, hashes[user.pk])
            self.assertTrue(fresh.is_active)
            self.assertIsNone(fresh.deactivated_at)

    def test_open_sessions_follow_the_new_permissions_without_logging_anyone_out(self):
        """Django vuelve a cargar la cuenta en cada petición: el cambio rige de inmediato en las
        sesiones ya abiertas y nadie queda desconectado (is_superuser no forma parte del hash de sesión)."""
        new_admin_client = Client()
        new_admin_client.force_login(self.trainer_a, backend='django.contrib.auth.backends.ModelBackend')
        staff_url = reverse('trainer_staff_list')
        self.assertEqual(new_admin_client.get(staff_url).status_code, 302)      # todavía no
        self.login(self.boss)
        self.assertEqual(self.client.get(staff_url).status_code, 200)

        self.client.post(self.url, self.post_data(self.trainer_a))

        self.assertEqual(new_admin_client.get(staff_url).status_code, 200)      # ya sí
        self.assertEqual(self.client.get(reverse('trainer_dashboard')).status_code, 200)   # sigue con sesión
        old_response = self.client.get(staff_url)
        self.assertRedirects(old_response, reverse('trainer_dashboard'), fetch_redirect_response=False)

    def test_old_admin_can_no_longer_open_the_screen(self):
        self.login(self.boss)
        self.client.post(self.url, self.post_data(self.trainer_a))
        response = self.client.get(self.url)
        self.assertRedirects(response, reverse('trainer_dashboard'), fetch_redirect_response=False)
        self.assertIn('No tienes permiso para acceder a esta sección.', flashes(response))

    def test_second_submit_after_the_transfer_does_nothing(self):
        """Doble clic: la segunda petición ya no viene de una superusuaria."""
        self.login(self.boss)
        self.client.post(self.url, self.post_data(self.trainer_a))
        response = self.client.post(self.url, self.post_data(self.trainer_b))
        self.assertRedirects(response, reverse('trainer_dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.superusers(), {'trainer_a'})
        self.assertEqual(AccountAuditLog.objects.count(), 1)

    def test_at_least_one_active_superuser_after_a_round_trip(self):
        self.login(self.boss)
        self.client.post(self.url, self.post_data(self.trainer_a))
        self.login(self.trainer_a)
        self.client.post(self.url, self.post_data(self.boss))
        self.assertEqual(self.superusers(), {'boss'})
        self.assertEqual(services.active_superusers().count(), 1)
        self.assertEqual(AccountAuditLog.objects.filter(action=choices.ACTION_TRANSFER_ADMIN).count(), 2)

    def test_the_old_admin_loses_the_admin_powers_over_accounts(self):
        """Ya no desactiva entrenadores, y la nueva administradora sí puede desactivarla a ella."""
        self.login(self.boss)
        self.client.post(self.url, self.post_data(self.trainer_a))
        response = self.client.get(reverse('trainer_staff_deactivate', args=[self.trainer_b.pk]))
        self.assertRedirects(response, reverse('trainer_dashboard'), fetch_redirect_response=False)
        self.login(self.trainer_a)
        self.client.post(reverse('trainer_staff_deactivate', args=[self.boss.pk]),
                         {'reassign_to': str(self.trainer_a.pk), 'reason': choices.REASON_LEFT})
        self.assertFalse(self.fresh(self.boss).is_active)
        self.assertTrue(services.active_superusers().filter(pk=self.trainer_a.pk).exists())


# ─── Errores del formulario ───────────────────────────────────────────────────

class TransferValidationTests(TransferMixin, ScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.login(self.boss)

    def test_wrong_password_changes_nothing_and_never_echoes_it(self):
        wrong = 'Clave-Equivocada-5521!'
        response = self.client.post(self.url, self.post_data(password=wrong))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['errors'], {'password': services.TRANSFER_PASSWORD_MESSAGE})
        self.assertNotIn('password', response.context['form'])
        self.assertNotContains(response, wrong)
        self.assertEqual(response.context['form']['new_admin'], str(self.trainer_a.pk))
        self.assert_nothing_changed()

    def test_missing_or_empty_password(self):
        for value in ('', None):
            with self.subTest(password=value):
                response = self.client.post(self.url, self.post_data(password=value))
                self.assertEqual(response.status_code, 200)
                self.assertIn('password', response.context['errors'])
        self.assert_nothing_changed()

    def test_confirmation_checkbox_is_required(self):
        response = self.client.post(self.url, self.post_data(confirm=None))
        self.assertEqual(response.status_code, 200)
        self.assertIn('confirm', response.context['errors'])
        self.assert_nothing_changed()

    def test_the_password_must_be_the_one_of_who_hands_over(self):
        """En las pruebas todas las cuentas comparten contraseña: se cambia la de quien entrega y la
        de las demás deja de servir."""
        self.boss.set_password('Otra-Clave-Del-Jefe-7731!')
        self.boss.save()
        self.login(self.boss)
        response = self.client.post(self.url, self.post_data(password=PASSWORD))
        self.assertIn('password', response.context['errors'])
        self.assert_nothing_changed()
        ok = self.client.post(self.url, self.post_data(password='Otra-Clave-Del-Jefe-7731!'))
        self.assertRedirects(ok, reverse('trainer_dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.superusers(), {'trainer_a'})

    def test_invalid_targets_are_a_field_error_never_a_500(self):
        other_boss = make_user('boss2', User.ROLE_TRAINER, is_staff=True, is_superuser=True)
        inactive = make_user('trainer_off', User.ROLE_TRAINER, is_staff=True, is_active=False)
        erased = make_user('trainer_gone', User.ROLE_TRAINER, is_staff=True)
        services.erase_account(erased, self.boss, choices.REASON_LEFT)
        baseline = AccountAuditLog.objects.count()
        bad_values = {
            'otra superusuaria': other_boss.pk, 'inactivo': inactive.pk, 'eliminado': erased.pk,
            'clienta': self.client_a.pk, 'ella misma': self.boss.pk, 'no existe': 999999,
            'texto': 'abc', 'vacío': '', 'negativo': '-1', 'enorme': '9' * 40, 'inyección': "1' OR '1'='1",
        }
        for label, value in bad_values.items():
            with self.subTest(target=label):
                response = self.client.post(self.url, self.post_data(new_admin=value))
                self.assertEqual(response.status_code, 200)
                self.assertIn('new_admin', response.context['errors'])
        self.assertTrue(self.fresh(self.boss).is_superuser)
        self.assertTrue(self.fresh(other_boss).is_superuser)
        self.assertFalse(self.fresh(inactive).is_superuser)
        self.assertEqual(AccountAuditLog.objects.count(), baseline)

    def test_target_deactivated_while_the_page_was_open(self):
        """Pasó el formulario, pero el servicio lo encuentra desactivado (carrera): error del campo y nada cambia."""
        real = services.transfer_admin

        def racing(actor, target, password):
            User.objects.filter(pk=target.pk).update(is_active=False)    # se desactiva "justo ahora"
            return real(actor, target, password)
        with mock.patch.object(services, 'transfer_admin', racing):
            response = self.client.post(self.url, self.post_data(self.trainer_a))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['errors'], {'new_admin': services.TRANSFER_TARGET_MESSAGE})
        self.assertNotIn(self.trainer_a.pk, [u.pk for u in response.context['targets']])
        self.assertEqual(self.superusers(), {'boss'})
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_unexpected_failure_shows_a_generic_message_and_changes_nothing(self):
        with mock.patch.object(services, '_audit', side_effect=RuntimeError('detalle interno secreto')):
            response = self.client.post(self.url, self.post_data(self.trainer_a))
        self.assertRedirects(response, reverse('trainer_dashboard'), fetch_redirect_response=False)
        shown = ' '.join(flashes(response))
        self.assertIn(services.GENERIC_ERROR, shown)
        self.assertNotIn('detalle interno secreto', shown)
        self.assert_nothing_changed()      # la transacción se revirtió completa


# ─── Límite de intentos ───────────────────────────────────────────────────────

class TransferRateLimitTests(TransferMixin, ScenarioTestCase):
    def test_too_many_attempts_are_blocked_even_with_the_right_password(self):
        self.login(self.boss)
        for _ in range(TRANSFER_ADMIN_ATTEMPTS_LIMIT):
            response = self.client.post(self.url, self.post_data(password='Clave-Equivocada-5521!'))
            self.assertEqual(response.status_code, 200)
        blocked = self.client.post(self.url, self.post_data())
        self.assertEqual(blocked.status_code, 429)
        self.assertIn('Retry-After', blocked)
        self.assertIn('Demasiados intentos', ' '.join(flashes(blocked)))
        self.assert_nothing_changed()
        cache.clear()      # pasó la ventana
        ok = self.client.post(self.url, self.post_data())
        self.assertRedirects(ok, reverse('trainer_dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.superusers(), {'trainer_a'})

    def test_form_mistakes_do_not_use_up_the_attempts(self):
        self.login(self.boss)
        for _ in range(TRANSFER_ADMIN_ATTEMPTS_LIMIT + 2):
            self.client.post(self.url, self.post_data(confirm=None))
        ok = self.client.post(self.url, self.post_data())
        self.assertRedirects(ok, reverse('trainer_dashboard'), fetch_redirect_response=False)

    def test_the_limit_is_per_account(self):
        other_boss = make_user('boss2', User.ROLE_TRAINER, is_staff=True, is_superuser=True)
        self.login(other_boss)
        for _ in range(TRANSFER_ADMIN_ATTEMPTS_LIMIT):
            self.client.post(self.url, self.post_data(password='Clave-Equivocada-5521!'))
        self.login(self.boss)
        response = self.client.post(self.url, self.post_data(password='Clave-Equivocada-5521!'))
        self.assertEqual(response.status_code, 200)       # la cuenta de boss no está bloqueada


# ─── El servicio (defensa en profundidad) ─────────────────────────────────────

class TransferServiceTests(TransferMixin, ScenarioTestCase):
    def assert_rejected(self, code, actor, target, password=PASSWORD, error=services.AccountNotAllowed):
        with self.assertRaises(error) as ctx:
            services.transfer_admin(actor, target, password)
        self.assertEqual(ctx.exception.code, code)

    def test_success(self):
        result = services.transfer_admin(self.boss, self.trainer_a, PASSWORD)
        self.assertTrue(result.changed)
        self.assertEqual(self.superusers(), {'trainer_a'})
        # los objetos que tiene quien llama quedan con el estado guardado
        self.assertFalse(self.boss.is_superuser)
        self.assertTrue(self.trainer_a.is_superuser)

    def test_only_an_active_superuser_trainer_can_hand_over(self):
        self.assert_rejected('not_allowed', self.trainer_a, self.trainer_b)
        self.assert_rejected('not_allowed', self.client_a, self.trainer_b)
        self.assert_rejected('not_allowed', None, self.trainer_b)
        User.objects.filter(pk=self.boss.pk).update(is_active=False)
        self.assert_rejected('not_allowed', self.boss, self.trainer_a)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_actor_is_reloaded_from_the_database(self):
        """Quien perdió el permiso después de cargar la página (objeto viejo en memoria) no puede."""
        stale_boss = User.objects.get(pk=self.boss.pk)
        User.objects.filter(pk=self.boss.pk).update(is_superuser=False)
        self.assertTrue(stale_boss.is_superuser)
        self.assert_rejected('not_allowed', stale_boss, self.trainer_a)
        self.assertFalse(self.fresh(self.trainer_a).is_superuser)

    def test_target_rules(self):
        other_boss = make_user('boss2', User.ROLE_TRAINER, is_staff=True, is_superuser=True)
        self.assert_rejected('account_self', self.boss, self.boss)
        self.assert_rejected('account_protected', self.boss, other_boss)
        self.assert_rejected('not_allowed', self.boss, self.client_a)
        User.objects.filter(pk=self.trainer_a.pk).update(is_active=False)
        self.assert_rejected('target_unavailable', self.boss, self.trainer_a, error=services.AccountFieldError)
        erased = make_user('trainer_gone', User.ROLE_TRAINER, is_staff=True)
        services.erase_account(erased, self.boss, choices.REASON_LEFT)
        self.assert_rejected('target_unavailable', self.boss, erased, error=services.AccountFieldError)
        self.assertEqual(self.superusers(), {'boss', 'boss2'})

    def test_wrong_password_is_a_generic_field_error_and_is_logged_without_secrets(self):
        for bad in ('Clave-Equivocada-5521!', '', None, ' ' + PASSWORD, PASSWORD.upper()):
            with self.subTest(password=bad), self.assertLogs('apps.accounts.services', level='WARNING') as logs:
                with self.assertRaises(services.AccountFieldError) as ctx:
                    services.transfer_admin(self.boss, self.trainer_a, bad)
            self.assertEqual((ctx.exception.code, ctx.exception.field), ('password_invalid', 'password'))
            self.assertEqual(ctx.exception.message, services.TRANSFER_PASSWORD_MESSAGE)
            self.assertIn('password_invalid', logs.output[0])
            self.assertNotIn(PASSWORD, logs.output[0])
        self.assert_nothing_changed()

    def test_never_leaves_zero_active_superusers(self):
        """Aunque se salten las demás reglas (llamada directa), nunca quedan cero superusuarias activas."""
        User.objects.filter(pk=self.trainer_a.pk).update(is_active=False)
        with mock.patch.object(services, 'check_can_transfer_admin'):
            self.assert_rejected('last_superuser', self.boss, self.trainer_a)
        self.assertTrue(self.fresh(self.boss).is_superuser)
        self.assertFalse(self.fresh(self.trainer_a).is_superuser)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_everything_or_nothing(self):
        with mock.patch.object(services, '_audit', side_effect=RuntimeError('falla a la mitad')):
            with self.assertRaises(RuntimeError):
                services.transfer_admin(self.boss, self.trainer_a, PASSWORD)
        self.assert_nothing_changed()

    def test_is_staff_is_normalised_for_both(self):
        User.objects.filter(pk=self.boss.pk).update(is_staff=False)
        User.objects.filter(pk=self.trainer_a.pk).update(is_staff=False)
        services.transfer_admin(self.boss, self.trainer_a, PASSWORD)
        self.assertTrue(self.fresh(self.boss).is_staff)
        self.assertTrue(self.fresh(self.trainer_a).is_staff)

    def test_no_new_foreign_key_to_user(self):
        """La auditoría ya existía: el traspaso no agrega claves foráneas a User."""
        self.assertEqual(services.user_foreign_keys() - set(services.ERASE_POLICY), set())

    def test_audit_entries_are_kept_when_the_actor_is_later_erased_as_a_trainer(self):
        """La auditoría apunta con PROTECT: la antigua administradora, ya entrenadora común, se
        elimina como lápida y su registro de traspaso se conserva."""
        services.transfer_admin(self.boss, self.trainer_a, PASSWORD)
        services.erase_account(self.boss, self.trainer_a, choices.REASON_LEFT, reassign_to=self.trainer_a)
        self.assertEqual(AccountAuditLog.objects.filter(action=choices.ACTION_TRANSFER_ADMIN).count(), 1)
        self.assertTrue(self.fresh(self.boss).is_erased)


# ─── start.sh no deshace el traspaso ──────────────────────────────────────────

class CreateTrainerDoesNotUndoTheTransferTests(TransferMixin, ScenarioTestCase):
    """start.sh corre `create_trainer --username yiseth --superuser` en CADA despliegue."""

    def run_create_trainer(self, **options):
        out = StringIO()
        env = {'TRAINER_INITIAL_PASSWORD': 'Clave-Inicial-Segura-8841!', 'TRAINER_PASSWORD': ''}
        with mock.patch.dict(os.environ, env):
            call_command('create_trainer', stdout=out, **options)
        return out.getvalue()

    def test_old_admin_is_not_promoted_again_on_the_next_deploy(self):
        services.transfer_admin(self.boss, self.trainer_a, PASSWORD)
        output = self.run_create_trainer(username='boss', superuser=True)
        self.assertIn('Ya hay otra superusuaria activa', output)
        self.assertFalse(self.fresh(self.boss).is_superuser)
        self.assertEqual(self.superusers(), {'trainer_a'})

    def test_an_erased_old_admin_name_is_not_recreated_as_superuser(self):
        services.transfer_admin(self.boss, self.trainer_a, PASSWORD)
        services.erase_account(self.boss, self.trainer_a, choices.REASON_LEFT, reassign_to=self.trainer_a)
        self.run_create_trainer(username='boss', superuser=True)
        self.assertFalse(User.objects.filter(username='boss').exists())     # el nombre quedó libre
        self.assertEqual(self.superusers(), {'trainer_a'})

    def test_without_another_active_superuser_it_works_as_before(self):
        User.objects.filter(pk=self.boss.pk).update(is_superuser=False)      # no queda ninguna
        self.assertEqual(self.superusers(), set())
        self.run_create_trainer(username='boss', superuser=True)             # existente: se promueve
        self.assertEqual(self.superusers(), {'boss'})
        self.run_create_trainer(username='boss', superuser=True)             # y repetirlo no cambia nada
        self.assertEqual(self.superusers(), {'boss'})
        User.objects.filter(pk=self.boss.pk).update(is_superuser=False)
        self.run_create_trainer(username='nueva_jefa', superuser=True)       # nueva: se crea
        self.assertTrue(User.objects.get(username='nueva_jefa').is_superuser)

    def test_without_the_flag_nothing_is_blocked(self):
        self.run_create_trainer(username='otro_entrenador')
        created = User.objects.get(username='otro_entrenador')
        self.assertFalse(created.is_superuser)
