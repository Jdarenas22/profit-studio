"""Desactivar, reactivar y eliminar cuentas (docs/contracts/cuentas-eliminar.md, sección 17).

Escenario base (apps/accounts/tests/helpers.py): boss (superusuaria), trainer_a, trainer_b,
client_a (de A), client_b (de B), client_free (sin asignar). `HealthScenarioTestCase` les da
además membresía vigente.

Las pantallas nuevas son esqueletos hasta que frontend-architect entregue C-01 a C-08: estas
pruebas comprueban el CONTEXTO y los efectos, no el HTML.
"""
import json
import uuid
from datetime import date, timedelta
from unittest import mock

from django.conf import settings
from django.contrib import admin
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.hashers import check_password
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.contrib.sessions.models import Session
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts import choices, services
from apps.accounts.admin import AccountAuditLogAdmin, CustomUserAdmin
from apps.accounts.models import AccountAuditLog, User
from apps.accounts.permissions import ClientInactiveError
from apps.assessments import services as assessment_services
from apps.assessments.models import BodyMeasurement, InitialAssessment
from apps.health import services as health_services
from apps.health.models import ConsentRecord, HealthAccessLog, HealthProfile
from apps.health.tests.helpers import HealthScenarioTestCase
from apps.memberships.models import Membership
from apps.payments.models import ManualPayment, Payment
from apps.payments.services import apply_transaction_event
from apps.plans import jobs as plan_jobs
from apps.plans import notifications as plan_notifications
from apps.plans import services as plan_services
from apps.plans.models import (
    MealChoice, MealOption, MealSlot, NutritionPlan, Plan, PlanEvent, PlanGenerationJob, TrainerCredential,
)
from apps.routines.models import Routine, RoutineDayLog
from apps.whatsapp.models import WhatsAppContact, WhatsAppMessage

from .helpers import PASSWORD, ScenarioTestCase, make_user

IN_MEMORY = override_settings(STORAGES={
    **settings.STORAGES,
    'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
})
DB_SESSIONS = override_settings(SESSION_ENGINE='django.contrib.sessions.backends.db')


def flashes(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


def queue_message(user, status=WhatsAppMessage.STATUS_QUEUED, **extra):
    return WhatsAppMessage.objects.create(
        recipient=user, kind='membership_expiring', status=status,
        dedupe_key=f'test:{user.pk}:{uuid.uuid4().hex}', **extra,
    )


def queue_job(client, status=PlanGenerationJob.STATUS_QUEUED, kind='exercise'):
    return PlanGenerationJob.objects.create(
        kind=kind, client=client, requested_by=client, requested_by_role='member',
        status=status, request_token=uuid.uuid4(),
    )


def make_plan(client, status=Plan.STATUS_DRAFT_AI, reviewed_by=None, version=1, kind='exercise'):
    return Plan.objects.create(client=client, kind=kind, version=version, status=status, reviewed_by=reviewed_by)


class LifecycleMixin:
    def deactivate_url(self, user):
        name = 'trainer_staff_deactivate' if user.is_trainer else 'trainer_client_deactivate'
        return reverse(name, args=[user.pk])

    def reactivate_url(self, user):
        name = 'trainer_staff_reactivate' if user.is_trainer else 'trainer_client_reactivate'
        return reverse(name, args=[user.pk])

    def erase_url(self, user):
        name = 'trainer_staff_delete' if user.is_trainer else 'trainer_client_delete'
        return reverse(name, args=[user.pk])

    @staticmethod
    def fresh(user):
        return User.objects.get(pk=user.pk)

    def erase_post(self, user, **overrides):
        data = {'confirm_name': user.display_name, 'reason': choices.REASON_HOLDER_REQUEST,
                'ack_payments': 'on'}
        data.update(overrides)
        return {key: value for key, value in data.items() if value is not None}


# ─── 1-3. Permisos, IDOR, métodos y CSRF ──────────────────────────────────────

class RoutePermissionTests(LifecycleMixin, ScenarioTestCase):
    def client_routes(self, client):
        return {
            'deactivate': ('get', reverse('trainer_client_deactivate', args=[client.pk])),
            'reactivate': ('post', reverse('trainer_client_reactivate', args=[client.pk])),
            'delete': ('get', reverse('trainer_client_delete', args=[client.pk])),
        }

    def staff_routes(self, trainer):
        return {
            'deactivate': ('get', reverse('trainer_staff_deactivate', args=[trainer.pk])),
            'reactivate': ('post', reverse('trainer_staff_reactivate', args=[trainer.pk])),
            'delete': ('get', reverse('trainer_staff_delete', args=[trainer.pk])),
        }

    def test_anonymous_goes_to_login(self):
        for name, (method, url) in {**self.client_routes(self.client_a),
                                     **{f's-{k}': v for k, v in self.staff_routes(self.trainer_a).items()}}.items():
            with self.subTest(route=name):
                response = getattr(self.client, method)(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response.url)

    def test_member_gets_403(self):
        self.login(self.client_b)
        for name, (method, url) in {**self.client_routes(self.client_a),
                                     **{f's-{k}': v for k, v in self.staff_routes(self.trainer_a).items()}}.items():
            with self.subTest(route=name):
                self.assertEqual(getattr(self.client, method)(url).status_code, 403)

    def test_trainer_gets_404_on_foreign_or_unassigned_clients(self):
        self.login(self.trainer_a)
        for victim in (self.client_b, self.client_free):
            for name in ('deactivate', 'reactivate'):
                method, url = self.client_routes(victim)[name]
                with self.subTest(victim=victim.username, route=name):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 404)
                    self.assertEqual(self.client.post(url).status_code, 404)
        self.assertTrue(self.fresh(self.client_b).is_active)
        self.assertTrue(self.fresh(self.client_free).is_active)

    def test_trainer_can_open_deactivation_of_own_client(self):
        self.login(self.trainer_a)
        response = self.client.get(self.deactivate_url(self.client_a))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['client'], self.client_a)
        self.assertEqual(response.context['reasons'], choices.REASON_CHOICES)
        self.assertIn('no-store', response['Cache-Control'])

    def test_regular_trainer_cannot_erase_or_touch_staff(self):
        """Respuesta 2a: un entrenador común ya no elimina; las rutas /staff/ son de la superusuaria."""
        self.login(self.trainer_a)
        urls = [(self.erase_url(self.client_a), 'get')] + [
            (url, method) for method, url in self.staff_routes(self.trainer_b).values()]
        for url, first_method in urls:
            for method in sorted({first_method, 'post'}):   # las rutas solo POST responden 405 al GET
                with self.subTest(url=url, method=method):
                    response = getattr(self.client, method)(url)
                    self.assertRedirects(response, reverse('trainer_dashboard'), fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(pk=self.client_a.pk, erased_at__isnull=True).exists())
        self.assertTrue(self.fresh(self.trainer_b).is_active)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_nobody_acts_on_superuser_self_wrong_role_or_erased(self):
        self.login(self.boss)
        other_boss = make_user('boss2', User.ROLE_TRAINER, is_staff=True, is_superuser=True)
        erased = make_user('borrada', User.ROLE_MEMBER)
        User.objects.filter(pk=erased.pk).update(is_active=False, erased_at=timezone.now())
        cases = [
            self.staff_routes(self.boss), self.staff_routes(other_boss),   # superusuarias (y ella misma)
            self.staff_routes(self.client_a),                             # rol equivocado en /staff/
            self.client_routes(self.trainer_a),                           # rol equivocado en /clients/
            self.client_routes(self.boss),
            self.client_routes(erased),                                   # cuenta eliminada
        ]
        for routes in cases:
            for name, (method, url) in routes.items():
                with self.subTest(url=url):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 404)
                    self.assertEqual(self.client.post(url).status_code, 404)
        self.assertTrue(self.fresh(self.boss).is_active)
        self.assertTrue(self.fresh(other_boss).is_active)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_get_never_changes_anything_and_post_routes_reject_get(self):
        self.login(self.boss)
        for user in (self.client_a, self.trainer_a):
            self.client.get(self.deactivate_url(user))
            self.client.get(self.erase_url(user))
            self.assertEqual(self.client.get(self.reactivate_url(user)).status_code, 405)
            self.assertEqual(self.client.put(self.deactivate_url(user)).status_code, 405)
            self.assertEqual(self.client.delete(self.erase_url(user)).status_code, 405)
            fresh = self.fresh(user)
            self.assertTrue(fresh.is_active)
            self.assertIsNone(fresh.erased_at)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_post_without_csrf_is_rejected(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.boss, backend='django.contrib.auth.backends.ModelBackend')
        for url, data in ((self.deactivate_url(self.client_a), {}),
                          (self.erase_url(self.client_free), self.erase_post(self.client_free)),
                          (self.deactivate_url(self.trainer_b), {'reassign_to': self.boss.pk})):
            with self.subTest(url=url):
                self.assertEqual(csrf_client.post(url, data).status_code, 403)
        self.assertTrue(self.fresh(self.client_a).is_active)
        self.assertIsNone(self.fresh(self.client_free).erased_at)
        self.assertTrue(self.fresh(self.trainer_b).is_active)


class ServiceRulesTests(ScenarioTestCase):
    """Las reglas viven también en el servicio (defensa en profundidad)."""

    def assert_rejected(self, func, code, *args, **kwargs):
        with self.assertRaises(services.AccountNotAllowed) as ctx:
            func(*args, **kwargs)
        self.assertEqual(ctx.exception.code, code)

    def test_service_rejects_protected_self_erased_and_non_superuser(self):
        other_boss = make_user('boss2', User.ROLE_TRAINER, is_staff=True, is_superuser=True)
        self.assert_rejected(services.deactivate_account, 'account_protected', other_boss, self.boss)
        self.assert_rejected(services.erase_account, 'account_protected', self.boss, other_boss, 'other')
        self.assert_rejected(services.deactivate_account, 'account_self', self.trainer_a, self.trainer_a)
        self.assert_rejected(services.erase_account, 'not_allowed', self.client_a, self.trainer_a, 'other')
        self.assert_rejected(services.deactivate_account, 'not_allowed', self.trainer_b, self.trainer_a)
        self.assert_rejected(services.deactivate_account, 'not_allowed', self.client_b, self.trainer_a)
        self.assert_rejected(services.deactivate_account, 'not_allowed', self.client_a, self.client_b)
        services.erase_account(self.client_free, self.boss, 'duplicate')
        self.assert_rejected(services.reactivate_account, 'account_erased', self.client_free, self.boss)
        self.assert_rejected(services.erase_account, 'account_erased', self.client_free, self.boss, 'other')
        self.assertTrue(self.fresh_active(self.boss) and self.fresh_active(other_boss))

    @staticmethod
    def fresh_active(user):
        return User.objects.get(pk=user.pk).is_active

    def test_actor_is_reloaded_from_the_database(self):
        """Quien perdió el permiso después de cargar la página ya no puede actuar."""
        User.objects.filter(pk=self.trainer_a.pk).update(is_active=False)
        self.assert_rejected(services.deactivate_account, 'not_allowed', self.client_a, self.trainer_a)
        self.assertTrue(self.fresh_active(self.client_a))

    def test_last_active_superuser_is_always_kept(self):
        """Aunque se salten las demás reglas (llamada directa), nunca quedan cero superusuarias."""
        with mock.patch.object(services, 'check_can_act'):
            self.assert_rejected(services.deactivate_account, 'last_superuser', self.boss, self.trainer_a)
            self.assert_rejected(services.erase_account, 'last_superuser', self.boss, self.trainer_a, 'other')
        boss = User.objects.get(pk=self.boss.pk)
        self.assertTrue(boss.is_active)
        self.assertIsNone(boss.erased_at)
        self.assertEqual(boss.username, 'boss')
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_reason_must_be_in_the_closed_list(self):
        with self.assertRaises(services.AccountFieldError):
            services.deactivate_account(self.client_a, self.trainer_a, reason='texto libre')
        with self.assertRaises(services.AccountFieldError):
            services.erase_account(self.client_a, self.boss, reason='')
        self.assertTrue(self.fresh_active(self.client_a))

    def test_name_matching_rules(self):
        self.assertTrue(services.name_matches('  maría   JOSÉ ', 'María José'))
        self.assertTrue(services.name_matches('María José', 'María José'))   # otra codificación
        self.assertFalse(services.name_matches('Maria Jose', 'María José'))              # las tildes cuentan
        self.assertFalse(services.name_matches('', 'María José'))
        self.assertFalse(services.name_matches('', ''))

    def test_tombstone_constraint_in_the_database(self):
        """Una cuenta eliminada no puede quedar activa (restricción en la base)."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=self.client_free.pk).update(erased_at=timezone.now(), is_active=True)


# ─── 4-9. Desactivar y reactivar clientas ─────────────────────────────────────

@DB_SESSIONS
class DeactivateClientTests(LifecycleMixin, HealthScenarioTestCase):
    def test_trainer_deactivates_own_client(self):
        self.login(self.trainer_a)
        response = self.client.post(self.deactivate_url(self.client_a), {'reason': choices.REASON_LEFT})
        self.assertRedirects(response, reverse('trainer_client_detail', args=[self.client_a.pk]),
                             fetch_redirect_response=False)
        client = self.fresh(self.client_a)
        self.assertFalse(client.is_active)
        self.assertIsNotNone(client.deactivated_at)
        self.assertTrue(check_password(PASSWORD, client.password))   # la contraseña no cambia
        log = AccountAuditLog.objects.get()
        self.assertEqual((log.actor, log.target, log.action, log.reason, log.target_role, log.reassigned_count),
                         (self.trainer_a, client, 'deactivate', 'left', 'member', 0))
        self.assertIn('Desactivaste la cuenta de Client_a Prueba.', flashes(response)[0])
        # Nada se borró y la membresía sigue igual (respuesta 5a)
        self.assertTrue(Membership.objects.get(user=client).is_valid)
        self.assertEqual(ManualPayment.objects.filter(user=client).count(), 1)
        self.assertEqual(InitialAssessment.objects.filter(user=client).count(), 1)

    def test_invalid_reason_is_a_field_error(self):
        self.login(self.trainer_a)
        response = self.client.post(self.deactivate_url(self.client_a), {'reason': 'inventado'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['errors'], {'reason': 'Elige un motivo.'})
        self.assertTrue(self.fresh(self.client_a).is_active)

    def test_double_post_is_idempotent(self):
        self.login(self.trainer_a)
        self.client.post(self.deactivate_url(self.client_a))
        response = self.client.post(self.deactivate_url(self.client_a))
        self.assertIn('Esa cuenta ya estaba desactivada.', flashes(response))
        self.assertEqual(AccountAuditLog.objects.count(), 1)

    def test_old_session_is_closed_and_login_is_rejected(self):
        member = Client()
        self.assertTrue(member.login(username='client_a', password=PASSWORD))
        self.assertEqual(member.get(reverse('dashboard')).status_code, 200)
        self.assertEqual(Session.objects.count(), 1)
        self.login(self.trainer_a)
        self.client.post(self.deactivate_url(self.client_a))
        # La fila de la sesión de la clienta se borró (la de quien desactiva sigue)
        remaining = [s.get_decoded().get('_auth_user_id') for s in Session.objects.all()]
        self.assertNotIn(str(self.client_a.pk), remaining)
        response = member.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)
        # Mismo mensaje genérico que una contraseña equivocada
        response = member.post(reverse('login'), {'username': 'client_a', 'password': PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Usuario o contraseña incorrectos.', flashes(response))
        self.assertNotIn('_auth_user_id', member.session)

    @override_settings(SESSION_ENGINE='django.contrib.sessions.backends.cache')
    def test_old_session_is_rejected_with_cache_sessions_too(self):
        """Con Redis no se pueden borrar las sesiones: Django rechaza la cuenta inactiva igual."""
        member = Client()
        self.assertTrue(member.login(username='client_a', password=PASSWORD))
        self.assertEqual(member.get(reverse('dashboard')).status_code, 200)
        services.deactivate_account(self.client_a, self.trainer_a)
        response = member.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_password_reset_ignores_inactive_accounts(self):
        services.deactivate_account(self.client_a, self.trainer_a)
        self.client.post(reverse('password_reset'), {'email': 'client_a@example.com'})
        self.assertEqual(len(mail.outbox), 0)
        self.client.post(reverse('password_reset'), {'email': 'client_b@example.com'})
        self.assertEqual(len(mail.outbox), 1)

    def test_whatsapp_queue_and_ai_jobs_are_cancelled(self):
        queued = queue_message(self.client_a)
        sending = queue_message(self.client_a, status=WhatsAppMessage.STATUS_SENDING)
        sent = queue_message(self.client_a, status=WhatsAppMessage.STATUS_SENT, wamid='wamid.1')
        other = queue_message(self.client_b)
        job = queue_job(self.client_a)
        other_job = queue_job(self.client_b)
        services.deactivate_account(self.client_a, self.trainer_a)
        for message in (queued, sending):
            message.refresh_from_db()
            self.assertEqual((message.status, message.error_code), ('cancelled', 'user_inactive'))
        sent.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(sent.status, 'sent')
        self.assertEqual(other.status, 'queued')
        job.refresh_from_db()
        other_job.refresh_from_db()
        self.assertEqual((job.status, job.error_code), ('failed', 'client_inactive'))
        self.assertEqual(other_job.status, 'queued')
        # La clienta desactivada no recibe el correo de "plan aprobado"
        plan = make_plan(self.fresh(self.client_a), status=Plan.STATUS_APPROVED)
        with mock.patch('apps.whatsapp.services.notify_plan_approved'):
            self.assertFalse(plan_notifications.client_plan_approved(plan))

    def test_a_running_job_does_not_save_the_plan(self):
        job = queue_job(self.client_a, status=PlanGenerationJob.STATUS_RUNNING)
        services.deactivate_account(self.client_a, self.trainer_a)
        with self.assertRaises(plan_jobs.JobFailed) as ctx, transaction.atomic():
            plan_jobs._lock_running(job)
        self.assertEqual(ctx.exception.code, 'client_inactive')
        # Un trabajo en cola de una cuenta inactiva termina sin llamar a la IA ni avisar
        User.objects.filter(pk=self.client_b.pk).update(is_active=False)
        queued = queue_job(self.client_b)
        with mock.patch.object(plan_jobs.notifications, 'trainer_job_failed') as failed:
            self.assertIsNone(plan_jobs.run_job(queued.pk))
        failed.assert_not_called()
        queued.refresh_from_db()
        self.assertEqual((queued.status, queued.error_code), ('failed', 'client_inactive'))
        self.assertFalse(Plan.objects.filter(client=self.client_b).exists())

    def test_inactive_client_is_read_only(self):
        """Respuesta 4a: las vistas que escriben rechazan; las de lectura funcionan."""
        services.deactivate_account(self.client_a, self.trainer_a)
        self.login(self.trainer_a)
        detail = reverse('trainer_client_detail', args=[self.client_a.pk])
        response = self.client.get(detail)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.context['can_deactivate'], response.context['can_reactivate'],
                          response.context['can_erase']), (False, True, False))
        self.assertEqual(self.client.get(reverse('trainer_health_profile', args=[self.client_a.pk])).status_code, 200)
        measurement = BodyMeasurement.objects.get(user=self.client_a)
        writes = [
            ('get', reverse('trainer_client_edit', args=[self.client_a.pk]), {}),
            ('post', reverse('trainer_client_edit', args=[self.client_a.pk]),
             {'first_name': 'X', 'last_name': 'Y', 'email': 'x@example.com', 'username': 'client_a'}),
            ('post', reverse('trainer_assessment_create', args=[self.client_a.pk]),
             {'age': 30, 'sex': 'F', 'weight': 60, 'height': '1.65', 'goal': 'x'}),
            ('post', reverse('trainer_assessment_detail', args=[self.assessment_a.pk]), {'p0': 70, 'p1': 100, 'p2': 80}),
            ('post', reverse('trainer_measurement_add', args=[self.client_a.pk]), {'weight': '70'}),
            ('post', reverse('trainer_measurement_edit', args=[measurement.pk]), {'weight': '71'}),
            ('post', reverse('trainer_measurement_delete', args=[measurement.pk]), {}),
            ('post', reverse('trainer_membership_manage', args=[self.client_a.pk]), {'action': 'deactivate'}),
            ('post', reverse('trainer_manual_payment_add', args=[self.client_a.pk]),
             {'amount': '10000', 'method': 'cash', 'payment_date': '2026-10-01'}),
            ('post', reverse('trainer_routine_create'), {'name': 'Nueva', 'client': self.client_a.pk}),
            ('post', reverse('trainer_routine_delete', args=[self.routine_a.pk]), {}),
            ('get', reverse('trainer_health_profile_edit', args=[self.client_a.pk]), {}),
        ]
        for method, url, data in writes:
            with self.subTest(url=url, method=method):
                response = getattr(self.client, method)(url, data)
                self.assertRedirects(response, detail, fetch_redirect_response=False)
                self.assertIn('Esta cuenta está desactivada. Reactívala para hacer cambios.', flashes(response))
        # Las acciones HTMX del constructor responden el fragmento de error
        response = self.client.post(reverse('trainer_add_day', args=[self.routine_a.pk]), {'day_name': 'Otro'})
        self.assertEqual(response['X-Form-Error'], '1')
        self.assertContains(response, 'Esta cuenta está desactivada')
        # Nada cambió
        client = self.fresh(self.client_a)
        self.assertEqual(client.first_name, 'Client_a')
        self.assertEqual(InitialAssessment.objects.filter(user=client).count(), 1)
        self.assertEqual(BodyMeasurement.objects.filter(user=client).count(), 1)
        self.assertTrue(Membership.objects.get(user=client).is_active)
        self.assertEqual(ManualPayment.objects.filter(user=client).count(), 1)
        self.assertEqual(Routine.objects.filter(user=client, is_active=True).count(), 1)
        self.assertEqual(self.routine_a.days.count(), 1)
        # Y el servicio lo rechaza aunque se le llame directamente
        with self.assertRaises(ClientInactiveError):
            assessment_services.create_trainer_measurement(client, self.trainer_a, {})
        with self.assertRaises(plan_services.PlanClientInactive):
            plan_services.request_plan(client, 'exercise', self.trainer_a, uuid.uuid4())
        with self.assertRaises(health_services.ProfileNotAllowed):
            self._save_profile_as_trainer(client)
        # La lista para crear rutinas ya no la ofrece
        self.assertNotIn(client, list(self.client.get(reverse('trainer_routine_create')).context['clients']))

    def _save_profile_as_trainer(self, client):
        from apps.health.tests.helpers import cleaned_profile
        health_services.save_profile(client, cleaned_profile(), actor=self.trainer_a)

    def test_reactivate_restores_login_with_same_password(self):
        self.login(self.trainer_a)
        self.client.post(self.deactivate_url(self.client_a))
        response = self.client.post(self.reactivate_url(self.client_a))
        self.assertIn('La cuenta de Client_a Prueba está activa de nuevo.', flashes(response))
        client = self.fresh(self.client_a)
        self.assertTrue(client.is_active)
        self.assertIsNone(client.deactivated_at)
        self.assertTrue(Client().login(username='client_a', password=PASSWORD))
        self.assertEqual(list(AccountAuditLog.objects.order_by('pk').values_list('action', flat=True)),
                         ['deactivate', 'reactivate'])
        response = self.client.post(self.reactivate_url(self.client_a))
        self.assertIn('Esa cuenta ya estaba activa.', flashes(response))
        self.assertEqual(AccountAuditLog.objects.count(), 2)


class ListFilterTests(LifecycleMixin, ScenarioTestCase):
    def test_client_list_status_filter_and_counts(self):
        services.deactivate_account(self.client_b, self.boss)
        services.erase_account(self.client_free, self.boss, 'duplicate')
        self.login(self.boss)
        url = reverse('trainer_clients')
        expected = {'': {self.client_a}, 'active': {self.client_a}, 'inactive': {self.client_b},
                    'all': {self.client_a, self.client_b}, 'basura': {self.client_a}}
        for status, clients in expected.items():
            with self.subTest(status=status):
                response = self.client.get(url, {'status': status} if status else {})
                self.assertEqual(set(response.context['clients']), clients)
                self.assertEqual(response.context['status'], status if status in ('inactive', 'all') else 'active')
                self.assertEqual(response.context['counts'], {'active': 1, 'inactive': 1, 'all': 2})
        # El panel no cuenta a la lápida como «sin asignar»
        self.assertEqual(self.client.get(reverse('trainer_dashboard')).context['unassigned_clients'], 0)
        # Un entrenador solo ve sus clientas, también las desactivadas
        self.login(self.trainer_b)
        response = self.client.get(url, {'status': 'all'})
        self.assertEqual(set(response.context['clients']), {self.client_b})

    def test_staff_list_status_filter_counts_and_client_count(self):
        services.deactivate_account(self.trainer_b, self.boss, reassign_to=self.boss)
        self.login(self.boss)
        url = reverse('trainer_staff_list')
        response = self.client.get(url)
        self.assertEqual([t.pk for t in response.context['trainers']], [self.boss.pk, self.trainer_a.pk])
        self.assertEqual(response.context['counts'], {'active': 2, 'inactive': 1, 'all': 3})
        rows = {t.pk: t.client_count for t in response.context['trainers']}
        self.assertEqual(rows, {self.boss.pk: 1, self.trainer_a.pk: 1})
        response = self.client.get(url, {'status': 'inactive'})
        self.assertEqual([t.pk for t in response.context['trainers']], [self.trainer_b.pk])
        services.erase_account(self.trainer_b, self.boss, 'left')
        self.assertEqual(self.client.get(url, {'status': 'all'}).context['counts']['all'], 2)


# ─── 7-8. Entrenadores: reasignación ──────────────────────────────────────────

class TrainerDeactivateTests(LifecycleMixin, ScenarioTestCase):
    def setUp(self):
        self.login(self.boss)
        self.extra = make_user('client_a2', User.ROLE_MEMBER, assigned_trainer=self.trainer_a)
        User.objects.filter(pk=self.extra.pk).update(is_active=False)   # desactivada: también pasa
        self.in_review = make_plan(self.client_a, status=Plan.STATUS_IN_REVIEW, reviewed_by=self.trainer_a)
        self.approved = make_plan(self.client_a, status=Plan.STATUS_APPROVED, reviewed_by=self.trainer_a, version=2)

    def test_get_context(self):
        response = self.client.get(self.deactivate_url(self.trainer_a))
        ctx = response.context
        self.assertEqual(ctx['trainer_obj'], self.trainer_a)
        self.assertEqual((ctx['client_count'], ctx['in_review_count']), (2, 1))
        self.assertEqual([t.pk for t in ctx['targets']], [self.boss.pk, self.trainer_b.pk])   # dueña primero
        self.assertIn('no-store', response['Cache-Control'])

    def test_deactivate_reassigns_all_clients_and_releases_plans(self):
        response = self.client.post(self.deactivate_url(self.trainer_a),
                                    {'reassign_to': self.trainer_b.pk, 'reason': 'left'})
        self.assertRedirects(response, reverse('trainer_staff_list'), fetch_redirect_response=False)
        self.assertIn('Desactivaste a Trainer_a Prueba. 2 clientas pasaron a Trainer_b Prueba.', flashes(response))
        self.assertFalse(self.fresh(self.trainer_a).is_active)
        self.assertEqual(User.objects.filter(assigned_trainer=self.trainer_a).count(), 0)   # invariante
        self.assertEqual(set(User.objects.filter(assigned_trainer=self.trainer_b)),
                         {self.client_a, self.client_b, self.extra})
        self.in_review.refresh_from_db()
        self.assertEqual((self.in_review.status, self.in_review.reviewed_by), ('draft_ai', None))
        event = PlanEvent.objects.get(plan=self.in_review)
        self.assertEqual((event.action, event.actor), ('released', self.boss))
        self.approved.refresh_from_db()
        self.assertEqual((self.approved.status, self.approved.reviewed_by), ('approved', self.trainer_a))
        log = AccountAuditLog.objects.get()
        self.assertEqual((log.reassigned_to, log.reassigned_count, log.target_role), (self.trainer_b, 2, 'trainer'))
        # Desaparece de "Nosotros" y de la lista para asignar
        self.assertNotIn(self.trainer_a, list(self.client.get(reverse('about')).context['trainers']))
        assign = reverse('trainer_assign_client', args=[self.client_free.pk])
        self.assertNotIn(self.trainer_a, list(self.client.get(assign).context['trainers']))
        self.assertEqual(self.client.post(assign, {'trainer_id': self.trainer_a.pk}).status_code, 404)
        self.assertIsNone(self.fresh(self.client_free).assigned_trainer)

    def test_invalid_destinations_change_nothing(self):
        inactive_trainer = make_user('trainer_c', User.ROLE_TRAINER, is_staff=True)
        User.objects.filter(pk=inactive_trainer.pk).update(is_active=False)
        bad = {
            '': 'Elige a quién pasar sus clientas.',
            'abc': 'Elige a quién pasar sus clientas.',
            '1e3': 'Elige a quién pasar sus clientas.',
            str(self.trainer_a.pk): 'Ese entrenador ya no está disponible. Elige otro.',
            str(inactive_trainer.pk): 'Ese entrenador ya no está disponible. Elige otro.',
            str(self.client_b.pk): 'Ese entrenador ya no está disponible. Elige otro.',
            '999999': 'Ese entrenador ya no está disponible. Elige otro.',
        }
        for value, message in bad.items():
            with self.subTest(reassign_to=value):
                response = self.client.post(self.deactivate_url(self.trainer_a), {'reassign_to': value})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['errors'], {'reassign_to': message})
        self.assertTrue(self.fresh(self.trainer_a).is_active)
        self.assertEqual(User.objects.filter(assigned_trainer=self.trainer_a).count(), 2)
        self.assertEqual(Plan.objects.get(pk=self.in_review.pk).status, 'in_review')
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_destination_lost_inside_the_transaction_rolls_back(self):
        """Carrera: el destino se desactivó entre el formulario y la transacción."""
        User.objects.filter(pk=self.trainer_b.pk).update(is_active=False)
        with self.assertRaises(services.AccountFieldError) as ctx:
            services.deactivate_account(self.trainer_a, self.boss, reassign_to=self.trainer_b)
        self.assertEqual(ctx.exception.code, 'reassign_unavailable')
        self.assertTrue(self.fresh(self.trainer_a).is_active)
        self.assertEqual(User.objects.filter(assigned_trainer=self.trainer_a).count(), 2)
        self.assertEqual(Plan.objects.get(pk=self.in_review.pk).status, 'in_review')
        # Por la vista: error de campo y la lista de destinos se relee
        with mock.patch.object(services, '_lock_destination',
                               side_effect=services.AccountFieldError(
                                   'reassign_unavailable', 'reassign_to', services.REASSIGN_UNAVAILABLE_MESSAGE)):
            response = self.client.post(self.deactivate_url(self.trainer_a), {'reassign_to': self.boss.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['errors'], {'reassign_to': services.REASSIGN_UNAVAILABLE_MESSAGE})
        self.assertTrue(self.fresh(self.trainer_a).is_active)

    def test_failure_in_the_middle_rolls_back_and_shows_generic_message(self):
        with mock.patch.object(services, 'close_sessions', side_effect=RuntimeError('boom')), \
                self.assertLogs('apps.accounts.views', level='ERROR'):
            response = self.client.post(self.deactivate_url(self.trainer_a), {'reassign_to': self.trainer_b.pk})
        self.assertIn(services.GENERIC_ERROR, flashes(response))
        self.assertTrue(self.fresh(self.trainer_a).is_active)
        self.assertEqual(User.objects.filter(assigned_trainer=self.trainer_a).count(), 2)
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_trainer_without_clients_needs_no_destination(self):
        lonely = make_user('trainer_c', User.ROLE_TRAINER, is_staff=True)
        response = self.client.post(self.deactivate_url(lonely), {})
        self.assertIn('Desactivaste a Trainer_c Prueba.', flashes(response))
        self.assertFalse(self.fresh(lonely).is_active)

    def test_reactivated_trainer_does_not_get_clients_back(self):
        self.client.post(self.deactivate_url(self.trainer_a), {'reassign_to': self.boss.pk})
        response = self.client.post(self.reactivate_url(self.trainer_a))
        self.assertIn('Trainer_a Prueba está activo/a de nuevo. Sus clientas anteriores siguen con quien las '
                      'tiene ahora; reasígnalas si hace falta.', flashes(response))
        self.assertTrue(self.fresh(self.trainer_a).is_active)
        self.assertEqual(User.objects.filter(assigned_trainer=self.trainer_a).count(), 0)
        self.assertTrue(Client().login(username='trainer_a', password=PASSWORD))


# ─── 10-18. Eliminar definitivamente ──────────────────────────────────────────

class ForeignKeyInventoryTests(ScenarioTestCase):
    def test_every_foreign_key_to_user_is_classified(self):
        """Quien agregue una tabla con clave foránea a User tiene que decidir qué pasa al eliminar."""
        found = services.user_foreign_keys()
        self.assertEqual(found - set(services.ERASE_POLICY), set(), 'Clasifica la clave nueva en ERASE_POLICY')
        self.assertEqual(set(services.ERASE_POLICY) - found, set(), 'Quita de ERASE_POLICY la clave que ya no existe')

    def test_payments_are_protected_from_cascades(self):
        for model in (ManualPayment, Payment):
            with self.subTest(model=model.__name__):
                self.assertEqual(model._meta.get_field('user').remote_field.on_delete.__name__, 'PROTECT')
        with self.assertRaises(ProtectedError), transaction.atomic():
            User.objects.filter(pk=self.client_a.pk).delete()


@IN_MEMORY
@DB_SESSIONS
class EraseClientTests(LifecycleMixin, HealthScenarioTestCase):
    def populate(self, client):
        """Una fila en cada tabla que depende de la clienta."""
        self.grant(client)
        self.make_profile(client)
        health_services.log_access(self.trainer_a, client, HealthAccessLog.ACTION_VIEW, 1)
        BodyMeasurement.objects.create(user=client, trainer=self.trainer_a, weight=61)
        RoutineDayLog.objects.create(user=client, routine_day=self.day_a)
        plan = make_plan(client, kind='nutrition', status=Plan.STATUS_APPROVED)
        PlanEvent.objects.create(plan=plan, actor=self.trainer_a, action=PlanEvent.ACTION_APPROVED)
        nutrition = NutritionPlan.objects.create(
            plan=plan, kcal_target=1800, protein_g=100, carbs_g=200, fat_g=60, fiber_g=25, tmb=1400,
            get=1900, activity_factor='1.375', meals_per_day=3, formula='mifflin')
        slot = MealSlot.objects.create(nutrition_plan=nutrition, code='breakfast', name='Desayuno', order=0,
                                       kcal_target=500, protein_g=30, carbs_g=60, fat_g=15)
        option = MealOption.objects.create(slot=slot, position=1, name='Avena', kcal=500, protein_g=30,
                                           carbs_g=60, fat_g=15)
        MealChoice.objects.create(client=client, slot=slot, option=option, date=date.today())
        queue_job(client, status=PlanGenerationJob.STATUS_SUCCEEDED)
        queue_job(client)
        contact = WhatsAppContact.objects.create(user=client, phone_e164='+573001112233', status='active')
        queue_message(client)
        mp = ManualPayment.objects.get(user=client)
        mp.notes = 'Pagó María por Nequi 300 111 2233'
        mp.receipt.save('comprobante.jpg', ContentFile(b'jpeg'), save=True)
        Payment.objects.create(user=client, plan=self.plan, amount_cents=10000000, status='approved',
                               raw_webhook={'event': 'transaction.updated', 'data': {'transaction': {
                                   'id': 'tx-1', 'reference': 'R-1', 'status': 'APPROVED',
                                   'amount_in_cents': 10000000, 'currency': 'COP',
                                   'customer_email': 'client_a@example.com',
                                   'customer_data': {'full_name': 'Client_a Prueba', 'phone_number': '3001112233'},
                                   'payment_method': {'type': 'NEQUI', 'phone_number': '3001112233'},
                               }}, 'signature': {'checksum': 'x'}})
        client.profile_photo.save('client_a_foto.jpg', ContentFile(b'img'), save=True)
        LogEntry.objects.create(user_id=self.boss.pk, content_type_id=ContentType.objects.get_for_model(User).pk,
                                object_id=str(client.pk), object_repr=str(client), action_flag=CHANGE,
                                change_message='Cambió el correo de Client_a Prueba')
        LogEntry.objects.create(user_id=self.boss.pk,
                                content_type_id=ContentType.objects.get_for_model(ManualPayment).pk,
                                object_id=str(mp.pk), object_repr=str(mp), action_flag=CHANGE, change_message='[]')
        from axes.models import AccessAttempt, AccessLog
        AccessAttempt.objects.create(username='client_a', ip_address='10.0.0.1', user_agent='UA',
                                     failures_since_start=1, get_data='', post_data='', http_accept='',
                                     path_info='/accounts/login/')
        AccessLog.objects.create(username='client_a', ip_address='10.0.0.1', user_agent='UA',
                                 http_accept='', path_info='/accounts/login/')
        return contact, mp

    def test_get_shows_the_summary(self):
        self.populate(self.client_a)
        self.login(self.boss)
        response = self.client.get(self.erase_url(self.client_a))
        ctx = response.context
        self.assertEqual(ctx['display_name'], 'Client_a Prueba')
        self.assertEqual(ctx['summary'], {'health_versions': 1, 'assessments': 1, 'measurements': 2,
                                          'routines': 1, 'plans': 1, 'manual_payments': 1, 'receipts': 1,
                                          'wompi_payments': 1})
        self.assertEqual(ctx['membership_valid_until'], Membership.objects.get(user=self.client_a).end_date)
        self.assertEqual((ctx['pending_wompi'], ctx['needs_ack']), (False, True))
        self.assertEqual(ctx['reasons'], choices.REASON_CHOICES)
        self.assertIn('no-store', response['Cache-Control'])

    def test_erase_client_end_to_end(self):
        contact, mp = self.populate(self.client_a)
        photo_name = self.fresh(self.client_a).profile_photo.name
        receipt_name = mp.receipt.name
        self.assertTrue(default_storage.exists(photo_name))
        pk = self.client_a.pk
        self.login(self.boss)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.erase_url(self.client_a),
                                        self.erase_post(self.client_a, confirm_name='  client_a   PRUEBA '))
        self.assertRedirects(response, reverse('trainer_clients'), fetch_redirect_response=False)
        self.assertIn('Eliminaste definitivamente la cuenta de Client_a Prueba. Sus pagos se conservan sin '
                      'datos personales.', flashes(response))

        # Lápida (7.2)
        user = User.objects.get(pk=pk)
        self.assertEqual(user.username, f'cuenta-eliminada-{pk}')
        self.assertEqual((user.first_name, user.last_name, user.email, user.phone, user.bio, user.gender,
                          user.training_goal), ('', '', '', '', '', '', ''))
        self.assertIsNone(user.interested_plan)
        self.assertIsNone(user.assigned_trainer)
        self.assertFalse(user.profile_photo)
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_active or user.is_staff or user.is_superuser)
        self.assertIsNone(user.last_login)
        self.assertIsNotNone(user.erased_at)
        self.assertIsNotNone(user.deactivated_at)
        self.assertEqual(user.role, 'member')
        self.assertEqual((user.display_name, user.is_erased), ('Cuenta eliminada', True))
        self.assertFalse(default_storage.exists(photo_name))   # la foto pública se borró

        # Se borró (7.1 A)
        for key, policy in services.ERASE_POLICY.items():
            if policy != services.DELETE:
                continue
            app_label, model_name, field_name = key.split('.')
            from django.apps import apps
            model = apps.get_model(app_label, model_name)
            with self.subTest(table=key):
                self.assertFalse(model.objects.filter(**{field_name: pk}).exists())
        self.assertFalse(WhatsAppContact.objects.filter(pk=contact.pk).exists())

        # Se conserva sin datos personales
        mp.refresh_from_db()
        self.assertEqual((mp.user_id, mp.amount, mp.notes, mp.trainer), (pk, 50000, '', self.trainer_a))
        self.assertEqual(mp.receipt.name, receipt_name)               # respuesta 1a
        self.assertTrue(default_storage.exists(receipt_name))
        self.assertTrue(mp.receipt_retained)
        self.assertNotIn('Client_a', str(mp))
        payment = Payment.objects.get(user_id=pk)
        tx = payment.raw_webhook['data']['transaction']
        self.assertLessEqual(set(tx), set(services.WEBHOOK_TRANSACTION_WHITELIST))
        self.assertEqual(tx['reference'], 'R-1')
        dump = json.dumps(payment.raw_webhook)
        for secret in ('client_a@example.com', 'Client_a', '3001112233', 'signature'):
            self.assertNotIn(secret, dump)
        consents = ConsentRecord.objects.filter(user_id=pk)
        self.assertTrue(consents.exists())
        self.assertFalse(consents.filter(revoked_at__isnull=True).exists())
        self.assertEqual(set(HealthAccessLog.objects.filter(client_id=pk).values_list('action', flat=True)),
                         {'view', 'delete'})
        self.assertEqual(HealthAccessLog.objects.get(client_id=pk, action='delete').actor, self.boss)

        # 7.3: axes y admin sin el nombre
        from axes.models import AccessAttempt, AccessLog
        self.assertFalse(AccessAttempt.objects.filter(username='client_a').exists())
        self.assertFalse(AccessLog.objects.filter(username='client_a').exists())
        for entry in LogEntry.objects.all():
            self.assertNotIn('Client_a', entry.object_repr + entry.change_message)

        # Auditoría
        log = AccountAuditLog.objects.get()
        self.assertEqual((log.action, log.reason, log.actor, log.target_id, log.target_role),
                         ('erase', 'holder_request', self.boss, pk, 'member'))

        # Desaparece de todas las listas y rutas; el usuario original queda libre
        self.assertNotIn(pk, [c.pk for c in self.client.get(reverse('trainer_clients'), {'status': 'all'})
                              .context['clients']])
        self.assertEqual(self.client.get(reverse('trainer_client_detail', args=[pk])).status_code, 404)
        self.assertEqual(self.client.get(self.erase_url(user)).status_code, 404)
        self.assertFalse(Client().login(username='client_a', password=PASSWORD))
        again = make_user('client_a', User.ROLE_MEMBER)
        self.assertNotEqual(again.pk, pk)

    def test_superuser_sees_erased_payments_and_receipt_but_trainer_does_not(self):
        _contact, mp = self.populate(self.client_a)
        services.erase_account(self.client_a, self.boss, 'holder_request')
        self.login(self.boss)
        payments = list(self.client.get(reverse('trainer_manual_payment_list')).context['payments'])
        erased_rows = [p for p in payments if p.user.is_erased]
        self.assertEqual([p.pk for p in erased_rows], [mp.pk])
        self.assertEqual(erased_rows[0].user.display_name, 'Cuenta eliminada')
        response = self.client.get(reverse('payment_receipt', args=[mp.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.login(self.trainer_a)
        self.assertNotIn(mp.pk, [p.pk for p in self.client.get(reverse('trainer_manual_payment_list'))
                                 .context['payments']])
        self.assertEqual(self.client.get(reverse('payment_receipt', args=[mp.pk])).status_code, 404)

    def test_wrong_confirmation_changes_nothing(self):
        self.login(self.boss)
        maria = make_user('maria', User.ROLE_MEMBER, assigned_trainer=self.trainer_a)
        User.objects.filter(pk=maria.pk).update(first_name='María', last_name='José')
        maria.refresh_from_db()
        url = self.erase_url(maria)
        bad = {'confirm_name': ['Maria Jose', '', 'María', 'x' * 301],
               'reason': ['', 'inventado']}
        for name in bad['confirm_name']:
            with self.subTest(confirm_name=name):
                response = self.client.post(url, self.erase_post(maria, confirm_name=name))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['errors'],
                                 {'confirm_name': 'El nombre no coincide. Escríbelo tal como aparece arriba.'})
        for reason in bad['reason']:
            with self.subTest(reason=reason):
                response = self.client.post(url, self.erase_post(maria, reason=reason))
                self.assertEqual(response.context['errors'], {'reason': 'Elige un motivo.'})
        # C-04 pinta cada error junto a su campo: no se repite como mensaje arriba
        self.assertContains(response, 'Elige un motivo.')
        self.assertFalse(any('Elige un motivo.' in m for m in flashes(response)))
        self.assertIsNone(self.fresh(maria).erased_at)
        self.assertFalse(AccountAuditLog.objects.exists())
        response = self.client.post(url, self.erase_post(maria, confirm_name=' maría  JOSÉ'))
        self.assertRedirects(response, reverse('trainer_clients'), fetch_redirect_response=False)
        self.assertIsNotNone(self.fresh(maria).erased_at)

    def test_ack_payments_only_when_needed(self):
        self.login(self.boss)
        # client_a tiene membresía vigente: hace falta la casilla
        response = self.client.post(self.erase_url(self.client_a), self.erase_post(self.client_a, ack_payments=None))
        self.assertEqual(response.context['errors'],
                         {'ack_payments': 'Confirma que revisaste la membresía y los pagos en curso.'})
        self.assertIsNone(self.fresh(self.client_a).erased_at)
        # Sin membresía vigente ni pago en curso no hace falta
        lonely = make_user('lonely', User.ROLE_MEMBER)
        self.assertFalse(services.needs_payment_ack(lonely))
        Payment.objects.create(user=lonely, plan=self.plan, amount_cents=100, status=Payment.STATUS_PENDING)
        self.assertTrue(services.needs_payment_ack(lonely))
        Payment.objects.filter(user=lonely).update(status=Payment.STATUS_DECLINED)
        response = self.client.post(self.erase_url(lonely), self.erase_post(lonely, ack_payments=None))
        self.assertRedirects(response, reverse('trainer_clients'), fetch_redirect_response=False)

    def test_failure_rolls_back_rows_and_files(self):
        self.populate(self.client_a)
        photo_name = self.fresh(self.client_a).profile_photo.name
        self.login(self.boss)
        with mock.patch.object(services, '_make_tombstone', side_effect=RuntimeError('boom')), \
                self.assertLogs('apps.accounts.views', level='ERROR'), \
                self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.client.post(self.erase_url(self.client_a), self.erase_post(self.client_a))
        self.assertEqual(callbacks, [])
        self.assertRedirects(response, reverse('trainer_client_detail', args=[self.client_a.pk]),
                             fetch_redirect_response=False)
        self.assertIn(services.GENERIC_ERROR, flashes(response))
        client = self.fresh(self.client_a)
        self.assertEqual((client.username, client.email, client.is_active), ('client_a', 'client_a@example.com', True))
        self.assertTrue(default_storage.exists(photo_name))
        self.assertTrue(HealthProfile.objects.filter(user=client).exists())
        self.assertTrue(WhatsAppContact.objects.filter(user=client).exists())
        self.assertEqual(ManualPayment.objects.get(user=client).notes, 'Pagó María por Nequi 300 111 2233')
        self.assertFalse(AccountAuditLog.objects.exists())

    def test_file_delete_failure_is_logged_without_names(self):
        self.populate(self.client_a)
        with mock.patch.object(default_storage, 'delete', side_effect=OSError('r2 down')), \
                self.assertLogs('apps.accounts.services', level='INFO') as logs, \
                self.captureOnCommitCallbacks(execute=True):
            services.erase_account(self.client_a, self.boss, 'holder_request')
        text = '\n'.join(logs.output)
        self.assertIn(f'account erase file_delete_failed kind=photo user_id={self.client_a.pk}', text)
        self.assertIn(f'account erased user_id={self.client_a.pk} role=member', text)
        self.assertNotIn('Client_a', text)
        self.assertNotIn('client_a_foto', text)

    def test_wompi_webhook_after_erase_does_not_activate(self):
        payment = Payment.objects.create(user=self.client_free, plan=self.plan, amount_cents=10000000)
        Membership.objects.filter(user=self.client_free).delete()
        services.erase_account(self.client_free, self.boss, 'holder_request')
        payload = {'event': 'transaction.updated', 'data': {'transaction': {
            'id': 'tx-9', 'reference': payment.reference, 'status': 'APPROVED', 'amount_in_cents': 10000000,
            'currency': 'COP', 'payment_method_type': 'NEQUI', 'customer_email': 'free@example.com',
            'customer_data': {'full_name': 'Client_free Prueba'}}}, 'timestamp': 1}
        with self.assertLogs('apps.payments.services', level='WARNING') as logs:
            self.assertEqual(apply_transaction_event(payload), 'erased_account')
        self.assertIn('cuenta eliminada', '\n'.join(logs.output))
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.STATUS_APPROVED)
        self.assertNotIn('free@example.com', json.dumps(payment.raw_webhook))
        self.assertLessEqual(set(payment.raw_webhook['data']['transaction']),
                             set(services.WEBHOOK_TRANSACTION_WHITELIST))
        self.assertFalse(Membership.objects.filter(user=payment.user).exists())

    def test_regular_trainer_cannot_erase_by_service(self):
        with self.assertRaises(services.AccountNotAllowed):
            services.erase_account(self.client_a, self.trainer_a, 'holder_request')
        self.assertIsNone(self.fresh(self.client_a).erased_at)


@IN_MEMORY
class EraseTrainerTests(LifecycleMixin, ScenarioTestCase):
    def test_erase_trainer_keeps_history_as_erased_account(self):
        TrainerCredential.objects.create(user=self.trainer_a, registration_label='RETHUS 123')
        approved = make_plan(self.client_a, status=Plan.STATUS_APPROVED, version=1)
        Plan.objects.filter(pk=approved.pk).update(approved_by=self.trainer_a, reviewer_credential='RETHUS 123')
        in_review = make_plan(self.client_a, status=Plan.STATUS_IN_REVIEW, reviewed_by=self.trainer_a, version=2)
        LogEntry.objects.create(user_id=self.trainer_a.pk, content_type_id=ContentType.objects.get_for_model(User).pk,
                                object_id=str(self.client_a.pk), object_repr='x', action_flag=CHANGE,
                                change_message='[]')
        self.login(self.boss)
        response = self.client.get(self.erase_url(self.trainer_a))
        self.assertEqual(response.context['display_name'], 'Trainer_a Prueba')
        self.assertEqual(response.context['client_count'], 1)
        self.assertFalse(response.context['validated_foods_hint'])
        response = self.client.post(self.erase_url(self.trainer_a),
                                    self.erase_post(self.trainer_a, reassign_to=self.boss.pk, reason='left'))
        self.assertRedirects(response, reverse('trainer_staff_list'), fetch_redirect_response=False)
        self.assertIn('Eliminaste definitivamente la cuenta de Trainer_a Prueba. 1 clientas pasaron a Boss Prueba.',
                      flashes(response))
        trainer = self.fresh(self.trainer_a)
        self.assertTrue(trainer.is_erased)
        self.assertEqual(trainer.role, 'trainer')
        self.assertEqual(self.fresh(self.client_a).assigned_trainer, self.boss)
        self.assertFalse(TrainerCredential.objects.filter(user=trainer).exists())
        # Su historial queda como «Cuenta eliminada»
        self.assertEqual(Routine.objects.get(pk=self.routine_a.pk).trainer.display_name, 'Cuenta eliminada')
        self.assertEqual(InitialAssessment.objects.get(pk=self.assessment_a.pk).trainer, trainer)
        self.assertEqual(ManualPayment.objects.get(user=self.client_a).trainer, trainer)
        approved.refresh_from_db()
        self.assertEqual((approved.approved_by, approved.reviewer_credential), (trainer, 'RETHUS 123'))
        in_review.refresh_from_db()
        self.assertEqual((in_review.status, in_review.reviewed_by), ('draft_ai', None))
        self.assertTrue(LogEntry.objects.filter(user=trainer).exists())
        log = AccountAuditLog.objects.get()
        self.assertEqual((log.action, log.reassigned_to, log.reassigned_count), ('erase', self.boss, 1))
        # Ya no aparece en el panel ni en la web
        self.assertNotIn(trainer.pk, [t.pk for t in self.client.get(reverse('trainer_staff_list'),
                                                                     {'status': 'all'}).context['trainers']])
        self.assertNotIn(trainer, list(self.client.get(reverse('about')).context['trainers']))
        self.assertEqual(self.client.get(reverse('trainer_edit_trainer', args=[trainer.pk])).status_code, 404)

    def test_erase_trainer_requires_destination_when_it_has_clients(self):
        self.login(self.boss)
        response = self.client.post(self.erase_url(self.trainer_a), self.erase_post(self.trainer_a))
        self.assertEqual(response.context['errors'], {'reassign_to': 'Elige a quién pasar sus clientas.'})
        self.assertIsNone(self.fresh(self.trainer_a).erased_at)


# ─── 18. Admin ────────────────────────────────────────────────────────────────

class AdminTests(LifecycleMixin, ScenarioTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def request_for(self, user):
        request = self.factory.get('/admin/')
        request.user = user
        return request

    admin_login_backend = 'django.contrib.auth.backends.ModelBackend'

    def admin_login(self, user):
        self.client.force_login(user, backend=self.admin_login_backend)

    @staticmethod
    def admin_delete_url(user, query=''):
        return reverse('admin:accounts_user_delete', args=[user.pk]) + query

    def assert_untouched(self, *users):
        """Ninguna cuenta se borró ni quedó como lápida, y no hay auditoría de eliminación."""
        for user in users:
            row = User.objects.get(pk=user.pk)
            self.assertIsNone(row.erased_at)
            self.assertTrue(row.is_active)
        self.assertFalse(AccountAuditLog.objects.filter(action=choices.ACTION_ERASE).exists())

    def test_user_admin_flags_are_read_only_and_delete_selected_is_gone(self):
        model_admin = admin.site._registry[User]
        self.assertIsInstance(model_admin, CustomUserAdmin)
        request = self.request_for(self.boss)
        actions = model_admin.get_actions(request)
        self.assertNotIn('delete_selected', actions)
        self.assertIn('erase_via_panel', actions)
        readonly = model_admin.get_readonly_fields(request, self.client_a)
        for name in ('is_active', 'is_superuser', 'deactivated_at', 'erased_at'):
            self.assertIn(name, readonly)
        # La acción masiva de Django ya no existe: el POST no borra nada.
        self.admin_login(self.boss)
        self.client.post(reverse('admin:accounts_user_changelist'),
                         {'action': 'delete_selected', '_selected_action': [self.client_a.pk]})
        self.client.post(reverse('admin:accounts_user_changelist'),
                         {'action': 'delete_selected', 'post': 'yes', '_selected_action': [self.client_a.pk]})
        self.assertTrue(User.objects.filter(pk=self.client_a.pk).exists())
        self.assert_untouched(self.client_a)

    def test_delete_permission_only_for_owner_and_erasable_targets(self):
        model_admin = admin.site._registry[User]
        boss_request = self.request_for(self.boss)
        self.assertTrue(model_admin.has_delete_permission(boss_request))
        self.assertTrue(model_admin.has_delete_permission(boss_request, self.client_a))
        self.assertTrue(model_admin.has_delete_permission(boss_request, self.client_free))
        self.assertTrue(model_admin.has_delete_permission(boss_request, self.trainer_a))
        # Nunca sobre la propia cuenta ni sobre otra superusuaria
        other_boss = make_user('boss2', User.ROLE_TRAINER, is_staff=True, is_superuser=True)
        self.assertFalse(model_admin.has_delete_permission(boss_request, self.boss))
        self.assertFalse(model_admin.has_delete_permission(boss_request, other_boss))
        # Nunca sobre una lápida
        services.erase_account(self.client_b, self.boss, choices.REASON_HOLDER_REQUEST)
        self.assertFalse(model_admin.has_delete_permission(boss_request, self.fresh(self.client_b)))
        # Un entrenador con acceso al admin (is_staff) no puede, ni sobre sus clientas
        trainer_request = self.request_for(self.trainer_a)
        self.assertFalse(model_admin.has_delete_permission(trainer_request))
        self.assertFalse(model_admin.has_delete_permission(trainer_request, self.client_a))
        # Una clienta, aunque fuera superusuaria de Django, tampoco (no es entrenadora)
        member_boss = make_user('member_boss', User.ROLE_MEMBER, is_staff=True, is_superuser=True)
        self.assertFalse(model_admin.has_delete_permission(self.request_for(member_boss), self.client_a))
        # Una superusuaria desactivada tampoco
        other_boss.is_active = False
        other_boss.save(update_fields=['is_active'])
        self.assertFalse(model_admin.has_delete_permission(self.request_for(other_boss), self.client_a))

    def test_change_page_shows_delete_link_only_where_it_applies(self):
        self.admin_login(self.boss)
        for target in (self.client_a, self.trainer_a):
            response = self.client.get(reverse('admin:accounts_user_change', args=[target.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, self.admin_delete_url(target))
        response = self.client.get(reverse('admin:accounts_user_change', args=[self.boss.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.admin_delete_url(self.boss))

    def test_admin_delete_redirects_client_to_the_safe_panel_flow_without_deleting(self):
        self.admin_login(self.boss)
        for method in (self.client.get, self.client.post):
            response = method(self.admin_delete_url(self.client_a))
            self.assertRedirects(response, reverse('trainer_client_delete', args=[self.client_a.pk]),
                                 fetch_redirect_response=False)
        self.assert_untouched(self.client_a)
        # La pantalla del panel abre y es la que pide motivo y confirmación
        response = self.client.get(self.admin_delete_url(self.client_a), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.resolver_match.url_name, 'trainer_client_delete')
        self.assertIn('reasons', response.context)
        self.assert_untouched(self.client_a)

    def test_admin_delete_redirects_trainer_to_the_safe_panel_flow_without_deleting(self):
        self.admin_login(self.boss)
        for method in (self.client.get, self.client.post):
            response = method(self.admin_delete_url(self.trainer_a))
            self.assertRedirects(response, reverse('trainer_staff_delete', args=[self.trainer_a.pk]),
                                 fetch_redirect_response=False)
        self.assert_untouched(self.trainer_a, self.client_a)
        self.assertEqual(self.fresh(self.client_a).assigned_trainer_id, self.trainer_a.pk)
        response = self.client.get(self.admin_delete_url(self.trainer_a), follow=True)
        self.assertEqual(response.resolver_match.url_name, 'trainer_staff_delete')
        self.assertEqual(response.status_code, 200)

    def test_erasing_after_the_admin_redirect_goes_through_the_service(self):
        """De punta a punta: admin -> panel -> `erase_account` (lápida, auditoría), no `delete()`."""
        self.admin_login(self.boss)
        response = self.client.get(self.admin_delete_url(self.client_a), follow=True)
        self.assertEqual(response.resolver_match.url_name, 'trainer_client_delete')
        response = self.client.post(response.request['PATH_INFO'], self.erase_post(self.client_a))
        self.assertRedirects(response, reverse('trainer_clients'), fetch_redirect_response=False)
        row = self.fresh(self.client_a)   # la fila sigue: es una lápida, no un borrado
        self.assertIsNotNone(row.erased_at)
        self.assertTrue(ManualPayment.objects.filter(user=row).exists())
        log = AccountAuditLog.objects.get(action=choices.ACTION_ERASE)
        self.assertEqual((log.actor, log.target), (self.boss, row))

    def test_admin_delete_of_protected_targets_shows_error_and_returns_to_change_page(self):
        other_boss = make_user('boss2', User.ROLE_TRAINER, is_staff=True, is_superuser=True)
        services.erase_account(self.client_b, self.boss, choices.REASON_HOLDER_REQUEST)
        audit_before = AccountAuditLog.objects.count()
        self.admin_login(self.boss)
        cases = ((self.boss, 'cuenta de superusuaria'), (other_boss, 'cuenta de superusuaria'),
                 (self.fresh(self.client_b), 'ya fue eliminada'))
        for target, expected_text in cases:
            for method in (self.client.get, self.client.post):
                response = method(self.admin_delete_url(target))
                self.assertRedirects(response, reverse('admin:accounts_user_change', args=[target.pk]),
                                     fetch_redirect_response=False)
                self.assertIn(expected_text, ' '.join(flashes(response)))
        self.assertEqual(AccountAuditLog.objects.count(), audit_before)
        self.assertTrue(User.objects.filter(pk=self.boss.pk, is_active=True).exists())
        self.assertTrue(User.objects.filter(pk=other_boss.pk, is_active=True, erased_at__isnull=True).exists())

    def test_admin_delete_of_unknown_account_is_404(self):
        self.admin_login(self.boss)
        self.assertEqual(self.client.get(reverse('admin:accounts_user_delete', args=[999999])).status_code, 404)
        self.assertEqual(self.client.get('/admin/accounts/user/no-es-un-id/delete/').status_code, 404)

    def test_admin_delete_is_forbidden_for_non_owners_and_popups(self):
        # Entrenador con acceso al admin (is_staff), aunque la clienta sea suya
        self.admin_login(self.trainer_a)
        for method in (self.client.get, self.client.post):
            self.assertEqual(method(self.admin_delete_url(self.client_a)).status_code, 403)
        response = self.client.post(reverse('admin:accounts_user_changelist'),
                                    {'action': 'erase_via_panel', '_selected_action': [self.client_a.pk]})
        self.assertEqual(response.status_code, 403)
        # Sin sesión: el admin pide iniciar sesión
        self.client.logout()
        response = self.client.get(self.admin_delete_url(self.client_a))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])
        # Una clienta (no staff) tampoco
        self.admin_login(self.client_a)
        self.assertEqual(self.client.get(self.admin_delete_url(self.client_a)).status_code, 302)
        # La ventana emergente de los campos relacionados no sigue el flujo
        self.admin_login(self.boss)
        response = self.client.get(self.admin_delete_url(self.client_a, '?_popup=1&_to_field=id'))
        self.assertEqual(response.status_code, 403)
        self.assert_untouched(self.client_a, self.trainer_a)

    def test_erase_action_redirects_with_exactly_one_selected_account(self):
        self.admin_login(self.boss)
        changelist = reverse('admin:accounts_user_changelist')
        response = self.client.post(changelist, {'action': 'erase_via_panel',
                                                 '_selected_action': [self.client_a.pk]})
        self.assertRedirects(response, reverse('trainer_client_delete', args=[self.client_a.pk]),
                             fetch_redirect_response=False)
        response = self.client.post(changelist, {'action': 'erase_via_panel',
                                                 '_selected_action': [self.trainer_a.pk]})
        self.assertRedirects(response, reverse('trainer_staff_delete', args=[self.trainer_a.pk]),
                             fetch_redirect_response=False)
        self.assert_untouched(self.client_a, self.trainer_a)

    def test_erase_action_does_nothing_with_zero_or_several_accounts(self):
        self.admin_login(self.boss)
        changelist = reverse('admin:accounts_user_changelist')
        response = self.client.post(changelist, {'action': 'erase_via_panel',
                                                 '_selected_action': [self.client_a.pk, self.client_b.pk]})
        self.assertRedirects(response, changelist, fetch_redirect_response=False)
        self.assertIn('Elige una sola cuenta', ' '.join(flashes(response)))
        # «Seleccionar las N cuentas» (select_across): tampoco
        response = self.client.post(changelist, {'action': 'erase_via_panel', 'select_across': '1', 'index': '0',
                                                 '_selected_action': [self.client_a.pk]})
        self.assertRedirects(response, changelist, fetch_redirect_response=False)
        self.assertIn('Elige una sola cuenta', ' '.join(flashes(response)))
        # Ninguna seleccionada: Django ni llama a la acción y avisa
        response = self.client.post(changelist, {'action': 'erase_via_panel', '_selected_action': []})
        self.assertNotIn('/accounts/trainer/', response.get('Location', ''))
        # La acción llamada directo con un queryset vacío tampoco hace nada
        model_admin = admin.site._registry[User]
        request = self.request_for(self.boss)
        with mock.patch.object(model_admin, 'message_user') as message_user:
            self.assertIsNone(model_admin.erase_via_panel(request, User.objects.none()))
        self.assertEqual(message_user.call_count, 1)
        self.assert_untouched(self.client_a, self.client_b)

    def test_erase_action_with_a_protected_account_shows_error_and_does_not_redirect(self):
        self.admin_login(self.boss)
        changelist = reverse('admin:accounts_user_changelist')
        response = self.client.post(changelist, {'action': 'erase_via_panel', '_selected_action': [self.boss.pk]})
        self.assertRedirects(response, changelist, fetch_redirect_response=False)
        self.assertIn('cuenta de superusuaria', ' '.join(flashes(response)))
        self.assert_untouched(self.boss)

    def test_admin_has_no_other_path_that_deletes_accounts(self):
        model_admin = admin.site._registry[User]
        request = self.request_for(self.boss)
        with self.assertRaises(PermissionDenied):
            model_admin.delete_model(request, self.client_a)
        with self.assertRaises(PermissionDenied):
            model_admin.delete_queryset(request, User.objects.filter(pk=self.client_a.pk))
        self.assertTrue(User.objects.filter(pk=self.client_a.pk).exists())
        # Ninguna acción del admin de usuarios toca la base: solo `erase_via_panel`
        self.assertEqual(set(model_admin.get_actions(request)), {'erase_via_panel'})

    def test_other_admins_do_not_offer_to_delete_accounts(self):
        """Con `has_delete_permission=True` para la dueña, ningún campo de otro admin que apunte a
        `User` debe ofrecer el botón de borrar la cuenta relacionada (ventana emergente)."""
        from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
        request = self.request_for(self.boss)
        offenders = []
        for model, model_admin in admin.site._registry.items():
            if model is User or not model_admin.has_view_permission(request):
                continue
            form = model_admin.get_form(request)
            for name, form_field in form.base_fields.items():
                widget = form_field.widget
                queryset = getattr(form_field, 'queryset', None)
                if (isinstance(widget, RelatedFieldWidgetWrapper) and queryset is not None
                        and queryset.model is User and widget.can_delete_related):
                    offenders.append(f'{model._meta.label}.{name}')
        self.assertEqual(offenders, [])

    def test_audit_admin_is_read_only_and_superuser_only(self):
        services.deactivate_account(self.client_a, self.trainer_a)
        model_admin = admin.site._registry[AccountAuditLog]
        self.assertIsInstance(model_admin, AccountAuditLogAdmin)
        log = AccountAuditLog.objects.get()
        boss_request = self.request_for(self.boss)
        self.assertTrue(model_admin.has_view_permission(boss_request, log))
        self.assertFalse(model_admin.has_add_permission(boss_request))
        self.assertFalse(model_admin.has_change_permission(boss_request, log))
        self.assertFalse(model_admin.has_delete_permission(boss_request, log))
        self.assertEqual(model_admin.get_actions(boss_request), {})
        trainer_request = self.request_for(self.trainer_a)   # is_staff, no superusuario
        self.assertFalse(model_admin.has_view_permission(trainer_request, log))
        self.assertFalse(model_admin.has_module_permission(trainer_request))
        self.client.force_login(self.boss, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('admin:accounts_accountauditlog_changelist')).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:accounts_accountauditlog_add')).status_code, 403)
        self.client.force_login(self.trainer_a, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(reverse('admin:accounts_accountauditlog_changelist')).status_code, 403)

    def test_audit_has_no_personal_data_fields(self):
        names = {f.name for f in AccountAuditLog._meta.get_fields() if getattr(f, 'concrete', False)}
        self.assertEqual(names, {'id', 'actor', 'target', 'target_role', 'action', 'reason', 'reassigned_to',
                                 'reassigned_count', 'created_at'})


# ─── 7.4. Archivos huérfanos ──────────────────────────────────────────────────

@IN_MEMORY
class PurgeOrphanFilesTests(ScenarioTestCase):
    def test_counts_by_default_and_deletes_only_orphans(self):
        user = User.objects.get(pk=self.trainer_a.pk)
        user.profile_photo.save('en_uso.jpg', ContentFile(b'1'), save=True)
        used = user.profile_photo.name
        default_storage.save('profiles/huerfana.jpg', ContentFile(b'2'))
        default_storage.save('receipts/huerfano.pdf', ContentFile(b'3'))
        from io import StringIO
        out = StringIO()
        call_command('purge_orphan_files', stdout=out)
        self.assertIn('Fotos de perfil huérfanas: 1', out.getvalue())
        self.assertIn('Comprobantes huérfanos: 1', out.getvalue())
        self.assertNotIn('huerfana', out.getvalue())   # nunca imprime nombres
        self.assertTrue(default_storage.exists('profiles/huerfana.jpg'))
        call_command('purge_orphan_files', '--delete', stdout=StringIO())
        self.assertFalse(default_storage.exists('profiles/huerfana.jpg'))
        self.assertFalse(default_storage.exists('receipts/huerfano.pdf'))
        self.assertTrue(default_storage.exists(used))
