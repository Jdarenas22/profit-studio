"""Pruebas del ciclo de vida del plan (`plan_lifecycle`).

Corren desde la raíz del proyecto:
    PYTHONPATH=docs/actividad-patrones/anexos/experimento .venv/Scripts/python.exe manage.py test exp_b
"""
from datetime import date, timedelta
from unittest import mock

from django.core import mail
from django.db.models import Max
from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from apps.plans import notifications, services
from apps.plans.models import KIND_EXERCISE, KIND_NUTRITION, Plan, PlanEvent
from apps.plans.services import PlanNotAllowed, PlanStateError
from apps.plans.tests.helpers import PlanScenarioTestCase
from apps.routines.models import Routine

from . import plan_lifecycle as lifecycle
from .plan_lifecycle import approve_plan, archive_plan, regenerate_plan, take_for_review

DRAFT, REVIEW = Plan.STATUS_DRAFT_AI, Plan.STATUS_IN_REVIEW
APPROVED, SUPERSEDED, ARCHIVED = Plan.STATUS_APPROVED, Plan.STATUS_SUPERSEDED, Plan.STATUS_ARCHIVED
ALL_STATUSES = [DRAFT, REVIEW, APPROVED, SUPERSEDED, ARCHIVED]

MSG_CANNOT_ARCHIVE = 'Este plan ya no se puede archivar.'
MSG_CANNOT_APPROVE = 'Solo se puede aprobar un borrador o un plan en revisión.'
MSG_CHANGED = 'Este plan cambió mientras lo revisabas. Recarga la página.'
MSG_CANNOT_REGENERATE = 'Solo se regenera un borrador o un plan en revisión.'


class LifecycleTestCase(PlanScenarioTestCase):
    """Escenario: `client_a` es de `trainer_a`; `client_b`, de `trainer_b`; `boss` ve a todas."""

    def make_plan(self, client=None, status=DRAFT, kind=KIND_EXERCISE, routine=None, **extra):
        client = client or self.client_a
        version = (Plan.objects.filter(client=client, kind=kind).aggregate(top=Max('version'))['top'] or 0) + 1
        if routine is None and kind == KIND_EXERCISE:
            routine = Routine.objects.create(
                name=f'Rutina v{version}', user=client, trainer=self.trainer_a, is_active=(status == APPROVED))
        return Plan.objects.create(client=client, kind=kind, version=version, status=status, routine=routine, **extra)

    @staticmethod
    def events(plan):
        return list(plan.events.order_by('pk').values_list('action', 'actor_id', 'note'))

    @staticmethod
    def reload(plan):
        return Plan.objects.select_related('routine').get(pk=plan.pk)


# ─── Registro de estados ──────────────────────────────────────────────────────

class StateRegistryTests(SimpleTestCase):
    def test_cada_estado_del_modelo_tiene_su_clase(self):
        for value, _label in Plan.STATUS_CHOICES:
            self.assertEqual(lifecycle.state_for(value).status, value)

    def test_estado_desconocido_es_un_error_de_negocio(self):
        with self.assertRaises(PlanStateError):
            lifecycle.state_for('otro')

    def test_el_contexto_delega_en_el_estado_actual(self):
        plan = Plan(status=APPROVED)
        self.assertIsInstance(lifecycle.PlanLifecycle(plan).state, lifecycle.ApprovedState)
        plan.status = ARCHIVED
        self.assertIsInstance(lifecycle.PlanLifecycle(plan).state, lifecycle.ArchivedState)


# ─── Acceso ───────────────────────────────────────────────────────────────────

class AccessTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(lifecycle.logger, 'warning')   # sin ruido en la consola
        patcher.start()
        self.addCleanup(patcher.stop)

    def operations(self, plan, actor):
        token = services.new_request_token()
        return {
            'take': lambda: take_for_review(plan, actor),
            'archive': lambda: archive_plan(plan, actor),
            'approve': lambda: approve_plan(plan, actor),
            'regenerate': lambda: regenerate_plan(plan, actor, 'notas', token),
        }

    def test_la_clienta_no_puede_operar(self):
        plan = self.make_plan()
        for name, call in self.operations(plan, self.client_a).items():
            with self.subTest(name), self.assertRaisesMessage(PlanNotAllowed, 'No tienes acceso a este plan.'):
                call()

    def test_un_entrenador_ajeno_no_puede_operar(self):
        plan = self.make_plan()
        for name, call in self.operations(plan, self.trainer_b).items():
            with self.subTest(name), self.assertRaisesMessage(PlanNotAllowed, 'No tienes acceso a este plan.'):
                call()
        plan = self.reload(plan)
        self.assertEqual(plan.status, DRAFT)
        self.assertEqual(self.events(plan), [])

    def test_el_acceso_se_comprueba_antes_que_el_estado(self):
        plan = self.make_plan(status=ARCHIVED)
        with self.assertRaises(PlanNotAllowed):
            approve_plan(plan, self.trainer_b)

    def test_el_entrenador_asignado_y_la_superusuaria_si_pueden(self):
        self.assertTrue(take_for_review(self.make_plan(), self.trainer_a))
        self.assertTrue(take_for_review(self.make_plan(self.client_free), self.boss))

    def test_la_superusuaria_ve_a_la_clienta_sin_asignar_pero_el_entrenador_no(self):
        plan = self.make_plan(self.client_free)
        with self.assertRaises(PlanNotAllowed):
            take_for_review(plan, self.trainer_a)


# ─── take_for_review ──────────────────────────────────────────────────────────

class TakeForReviewTests(LifecycleTestCase):
    def test_pasa_de_borrador_a_en_revision(self):
        plan = self.make_plan()
        self.assertIs(take_for_review(plan, self.trainer_a), True)
        plan = self.reload(plan)
        self.assertEqual(plan.status, REVIEW)
        self.assertEqual(plan.reviewed_by, self.trainer_a)
        self.assertEqual(self.events(plan), [(PlanEvent.ACTION_TAKEN, self.trainer_a.pk, '')])

    def test_actualiza_el_objeto_en_memoria(self):
        plan = self.make_plan()
        take_for_review(plan, self.trainer_a)
        self.assertEqual(plan.status, REVIEW)
        self.assertEqual(plan.reviewed_by, self.trainer_a)

    def test_desde_otro_estado_no_hace_nada(self):
        for status in (REVIEW, APPROVED, SUPERSEDED, ARCHIVED):
            with self.subTest(status):
                plan = self.make_plan(self.client_a, status=status)
                self.assertIs(take_for_review(plan, self.trainer_a), False)
                plan = self.reload(plan)
                self.assertEqual(plan.status, status)
                self.assertIsNone(plan.reviewed_by)
                self.assertEqual(self.events(plan), [])

    def test_un_objeto_desactualizado_devuelve_false(self):
        plan = self.make_plan()
        Plan.objects.filter(pk=plan.pk).update(status=REVIEW, reviewed_by=self.boss)
        self.assertIs(take_for_review(plan, self.trainer_a), False)
        plan = self.reload(plan)
        self.assertEqual(plan.reviewed_by, self.boss)
        self.assertEqual(self.events(plan), [])

    def test_no_se_puede_tomar_dos_veces(self):
        plan = self.make_plan()
        self.assertTrue(take_for_review(plan, self.trainer_a))
        self.assertFalse(take_for_review(plan, self.boss))
        self.assertEqual(self.reload(plan).reviewed_by, self.trainer_a)
        self.assertEqual(len(self.events(plan)), 1)


# ─── archive_plan ─────────────────────────────────────────────────────────────

class ArchiveTests(LifecycleTestCase):
    def test_archiva_desde_borrador_revision_y_aprobado(self):
        for status in (DRAFT, REVIEW, APPROVED):
            with self.subTest(status):
                plan = self.make_plan(status=status)
                before = timezone.now()
                result = archive_plan(plan, self.trainer_a, 'Ya no aplica')
                self.assertIs(result, plan)
                plan = self.reload(plan)
                self.assertEqual(plan.status, ARCHIVED)
                self.assertGreaterEqual(plan.archived_at, before)
                self.assertFalse(plan.routine.is_active)
                self.assertEqual(self.events(plan), [(PlanEvent.ACTION_ARCHIVED, self.trainer_a.pk, 'Ya no aplica')])

    def test_desactiva_la_rutina_de_un_plan_aprobado(self):
        plan = self.make_plan(status=APPROVED)
        self.assertTrue(plan.routine.is_active)
        archive_plan(plan, self.boss)
        self.assertFalse(Routine.objects.get(pk=plan.routine_id).is_active)

    def test_motivo_vacio_usa_el_texto_por_defecto(self):
        for reason in ('', '   ', None):
            with self.subTest(reason):
                plan = self.make_plan()
                archive_plan(plan, self.trainer_a, reason)
                self.assertEqual(self.events(plan)[0][2], 'Archivado por el entrenador.')

    def test_motivo_se_recorta_a_300_caracteres(self):
        plan = self.make_plan()
        archive_plan(plan, self.trainer_a, 'm' * 400)
        self.assertEqual(self.events(plan)[0][2], 'm' * 300)

    def test_plan_sin_rutina_se_archiva(self):
        plan = self.make_plan(kind=KIND_NUTRITION)
        self.assertIsNone(plan.routine_id)
        archive_plan(plan, self.trainer_a)
        self.assertEqual(self.reload(plan).status, ARCHIVED)

    def test_desde_reemplazado_o_archivado_falla_sin_cambios(self):
        for status in (SUPERSEDED, ARCHIVED):
            with self.subTest(status):
                plan = self.make_plan(status=status)
                with self.assertRaisesMessage(PlanStateError, MSG_CANNOT_ARCHIVE):
                    archive_plan(plan, self.trainer_a, 'x')
                plan = self.reload(plan)
                self.assertEqual(plan.status, status)
                self.assertIsNone(plan.archived_at)
                self.assertEqual(self.events(plan), [])

    def test_un_objeto_desactualizado_no_pisa_el_estado_real(self):
        plan = self.make_plan()
        Plan.objects.filter(pk=plan.pk).update(status=SUPERSEDED)
        with self.assertRaisesMessage(PlanStateError, MSG_CANNOT_ARCHIVE):
            archive_plan(plan, self.trainer_a)
        self.assertEqual(self.reload(plan).status, SUPERSEDED)

    def test_no_se_puede_archivar_dos_veces(self):
        plan = self.make_plan()
        archive_plan(plan, self.trainer_a)
        with self.assertRaisesMessage(PlanStateError, MSG_CANNOT_ARCHIVE):
            archive_plan(plan, self.trainer_a)
        self.assertEqual(len(self.events(plan)), 1)


# ─── approve_plan ─────────────────────────────────────────────────────────────

class ApproveTests(LifecycleTestCase):
    def test_aprueba_un_borrador(self):
        plan = self.make_plan()
        before = timezone.now()
        with mock.patch('apps.health.dates.today', return_value=date(2026, 3, 1)):
            result = approve_plan(plan, self.trainer_a, note_for_client='Buen trabajo')
        self.assertIs(result, plan)
        plan = self.reload(plan)
        self.assertEqual(plan.status, APPROVED)
        self.assertEqual(plan.approved_by, self.trainer_a)
        self.assertGreaterEqual(plan.approved_at, before)
        self.assertEqual(plan.reviewed_by, self.trainer_a)
        self.assertEqual(plan.note_for_client, 'Buen trabajo')
        self.assertEqual(plan.valid_until, date(2026, 3, 1) + timedelta(days=28))
        self.assertTrue(plan.routine.is_active)
        self.assertIsNone(plan.warnings_ack_by)
        self.assertIsNone(plan.warnings_ack_at)
        self.assertEqual(self.events(plan), [(PlanEvent.ACTION_APPROVED, self.trainer_a.pk, '')])

    def test_aprueba_un_plan_en_revision_y_conserva_quien_lo_reviso(self):
        plan = self.make_plan()
        take_for_review(plan, self.trainer_a)
        approve_plan(plan, self.boss)
        plan = self.reload(plan)
        self.assertEqual(plan.status, APPROVED)
        self.assertEqual(plan.reviewed_by, self.trainer_a)
        self.assertEqual(plan.approved_by, self.boss)
        self.assertEqual(
            [event[0] for event in self.events(plan)], [PlanEvent.ACTION_TAKEN, PlanEvent.ACTION_APPROVED])

    def test_la_nota_para_la_clienta_se_recorta_a_500(self):
        plan = self.make_plan()
        approve_plan(plan, self.trainer_a, note_for_client='n' * 700)
        self.assertEqual(self.reload(plan).note_for_client, 'n' * 500)

    def test_la_vigencia_usa_hoy_de_health_dates(self):
        plan = self.make_plan()
        approve_plan(plan, self.trainer_a)
        from apps.health import dates
        self.assertEqual(self.reload(plan).valid_until, dates.today() + timedelta(days=28))

    def test_desde_aprobado_reemplazado_o_archivado_falla(self):
        for status in (APPROVED, SUPERSEDED, ARCHIVED):
            with self.subTest(status):
                plan = self.make_plan(self.client_b if status == APPROVED else self.client_a, status=status)
                with self.assertRaisesMessage(PlanStateError, MSG_CANNOT_APPROVE):
                    approve_plan(plan, self.boss)
                self.assertEqual(self.reload(plan).status, status)
                self.assertEqual(self.events(plan), [])

    def test_reemplaza_al_plan_aprobado_anterior(self):
        old = self.make_plan(status=APPROVED)
        new = self.make_plan()
        self.assertEqual(new.version, 2)
        approve_plan(new, self.trainer_a)
        old, new = self.reload(old), self.reload(new)
        self.assertEqual(old.status, SUPERSEDED)
        self.assertFalse(old.routine.is_active)
        self.assertEqual(self.events(old), [(PlanEvent.ACTION_SUPERSEDED, self.trainer_a.pk, 'Reemplazado por la versión 2.')])
        self.assertEqual(new.status, APPROVED)
        self.assertTrue(new.routine.is_active)
        self.assertEqual(Plan.objects.filter(client=self.client_a, kind=KIND_EXERCISE, status=APPROVED).count(), 1)

    def test_no_toca_aprobados_de_otra_clienta_ni_de_otro_tipo(self):
        other_client = self.make_plan(self.client_b, status=APPROVED)
        other_kind = self.make_plan(self.client_a, status=APPROVED, kind=KIND_NUTRITION)
        new = self.make_plan(self.client_a)
        approve_plan(new, self.boss)
        self.assertEqual(self.reload(other_client).status, APPROVED)
        self.assertEqual(self.reload(other_kind).status, APPROVED)
        self.assertEqual(self.events(other_client), [])
        self.assertEqual(self.events(other_kind), [])

    def test_modo_de_prueba_no_se_aprueba(self):
        plan = self.make_plan(is_simulated=True)
        with self.assertRaisesMessage(
                PlanStateError, 'Este plan se generó en MODO DE PRUEBA: no se puede aprobar. Genera uno real.'):
            approve_plan(plan, self.trainer_a)
        self.assertEqual(self.reload(plan).status, DRAFT)

    @override_settings(DEBUG=True)
    def test_modo_de_prueba_se_aprueba_con_debug(self):
        plan = self.make_plan(is_simulated=True)
        approve_plan(plan, self.trainer_a)
        self.assertEqual(self.reload(plan).status, APPROVED)

    def test_advertencias_exigen_confirmacion(self):
        plan = self.make_plan(flags=['Y01'])
        with self.assertRaisesMessage(PlanStateError, 'Marca "Revisé las advertencias" para aprobar.'):
            approve_plan(plan, self.trainer_a)
        plan = self.reload(plan)
        self.assertEqual(plan.status, DRAFT)
        self.assertIsNone(plan.warnings_ack_by)

    def test_con_advertencias_confirmadas_guarda_quien_las_reviso(self):
        plan = self.make_plan(flags=['Y01'])
        approve_plan(plan, self.trainer_a, warnings_ack=True)
        plan = self.reload(plan)
        self.assertEqual(plan.status, APPROVED)
        self.assertEqual(plan.warnings_ack_by, self.trainer_a)
        self.assertEqual(plan.warnings_ack_at, plan.approved_at)

    def test_sin_advertencias_no_se_guarda_la_confirmacion(self):
        plan = self.make_plan()
        approve_plan(plan, self.trainer_a, warnings_ack=True)
        self.assertIsNone(self.reload(plan).warnings_ack_by)

    def test_primero_se_revisa_el_modo_de_prueba_y_luego_las_advertencias(self):
        plan = self.make_plan(is_simulated=True, flags=['Y01'])
        with self.assertRaisesMessage(PlanStateError, 'MODO DE PRUEBA'):
            approve_plan(plan, self.trainer_a)

    def test_un_rechazo_no_reemplaza_al_plan_aprobado(self):
        old = self.make_plan(status=APPROVED)
        new = self.make_plan(flags=['Y01'])
        with self.assertRaises(PlanStateError):
            approve_plan(new, self.trainer_a)
        self.assertEqual(self.reload(old).status, APPROVED)
        self.assertTrue(self.reload(old).routine.is_active)
        self.assertEqual(self.events(old), [])

    def test_stale_ack_se_acepta_y_se_ignora(self):
        plan = self.make_plan()
        approve_plan(plan, self.trainer_a, stale_ack=True)
        self.assertEqual(self.reload(plan).status, APPROVED)

    def test_si_el_plan_cambio_mientras_se_revisaba_falla_sin_efectos(self):
        old = self.make_plan(status=APPROVED)
        new = self.make_plan()
        Plan.objects.filter(pk=new.pk).update(status=ARCHIVED)   # otra persona lo archivó
        with self.assertRaisesMessage(PlanStateError, MSG_CHANGED):
            approve_plan(new, self.trainer_a)
        self.assertEqual(self.reload(new).status, ARCHIVED)
        self.assertEqual(self.reload(old).status, APPROVED)
        self.assertEqual(self.events(new), [])

    def test_si_otra_persona_lo_tomo_para_revision_igual_se_puede_aprobar(self):
        plan = self.make_plan()
        Plan.objects.filter(pk=plan.pk).update(status=REVIEW, reviewed_by=self.boss)
        approve_plan(plan, self.trainer_a)
        plan = self.reload(plan)
        self.assertEqual(plan.status, APPROVED)
        self.assertEqual(plan.reviewed_by, self.boss)

    def test_plan_de_alimentacion_sin_rutina_se_aprueba(self):
        plan = self.make_plan(kind=KIND_NUTRITION)
        approve_plan(plan, self.trainer_a)
        self.assertEqual(self.reload(plan).status, APPROVED)


class ApproveNotificationTests(LifecycleTestCase):
    def test_avisa_a_la_clienta_solo_al_confirmar_la_transaccion(self):
        plan = self.make_plan()
        with mock.patch.object(notifications, 'client_plan_approved') as notify:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                approve_plan(plan, self.trainer_a)
            notify.assert_not_called()
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            notify.assert_called_once_with(plan)

    def test_el_correo_sale_de_verdad(self):
        plan = self.make_plan()
        with self.captureOnCommitCallbacks(execute=True):
            approve_plan(plan, self.trainer_a)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.client_a.email])

    def test_no_avisa_si_la_aprobacion_falla(self):
        plan = self.make_plan(flags=['Y01'])
        with mock.patch.object(notifications, 'client_plan_approved') as notify:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                with self.assertRaises(PlanStateError):
                    approve_plan(plan, self.trainer_a)
        self.assertEqual(callbacks, [])
        notify.assert_not_called()

    def test_un_fallo_del_aviso_no_rompe_la_aprobacion(self):
        plan = self.make_plan()
        with mock.patch.object(notifications, 'client_plan_approved', side_effect=RuntimeError('boom')):
            with self.assertLogs(lifecycle.logger.name, 'WARNING') as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    approve_plan(plan, self.trainer_a)
        self.assertEqual(self.reload(plan).status, APPROVED)
        self.assertNotIn('boom', ''.join(logs.output))


# ─── regenerate_plan ──────────────────────────────────────────────────────────

class RegenerateTests(LifecycleTestCase):
    def test_pide_el_trabajo_y_registra_el_evento(self):
        for status in (DRAFT, REVIEW):
            with self.subTest(status):
                plan = self.make_plan(status=status)
                token = services.new_request_token()
                with mock.patch.object(services, 'request_plan', return_value=('job', True)) as request:
                    result = regenerate_plan(plan, self.trainer_a, 'Más énfasis en piernas', token)
                self.assertEqual(result, ('job', True))
                request.assert_called_once_with(
                    self.client_a, KIND_EXERCISE, self.trainer_a, token,
                    trainer_notes='Más énfasis en piernas', parent_plan=plan)
                self.assertEqual(
                    self.events(plan),
                    [(PlanEvent.ACTION_REGENERATE, self.trainer_a.pk, 'Más énfasis en piernas')])
                self.assertEqual(self.reload(plan).status, status)

    def test_si_el_pedido_ya_existia_no_registra_evento(self):
        plan = self.make_plan()
        with mock.patch.object(services, 'request_plan', return_value=('job', False)):
            result = regenerate_plan(plan, self.trainer_a, 'x', services.new_request_token())
        self.assertEqual(result, ('job', False))
        self.assertEqual(self.events(plan), [])

    def test_las_notas_del_evento_se_recortan_a_500(self):
        plan = self.make_plan()
        with mock.patch.object(services, 'request_plan', return_value=('job', True)):
            regenerate_plan(plan, self.trainer_a, 'n' * 800, services.new_request_token())
        self.assertEqual(self.events(plan)[0][2], 'n' * 500)

    def test_desde_aprobado_reemplazado_o_archivado_falla(self):
        for status in (APPROVED, SUPERSEDED, ARCHIVED):
            with self.subTest(status):
                plan = self.make_plan(status=status)
                with mock.patch.object(services, 'request_plan') as request:
                    with self.assertRaisesMessage(PlanStateError, MSG_CANNOT_REGENERATE):
                        regenerate_plan(plan, self.trainer_a, 'x', services.new_request_token())
                request.assert_not_called()
                self.assertEqual(self.events(plan), [])

    def test_un_error_al_pedir_el_trabajo_no_deja_evento(self):
        plan = self.make_plan()
        with mock.patch.object(services, 'request_plan', side_effect=services.QuotaExceeded('cupo')):
            with self.assertRaises(services.QuotaExceeded):
                regenerate_plan(plan, self.trainer_a, 'x', services.new_request_token())
        self.assertEqual(self.events(plan), [])

    def test_con_el_servicio_real_crea_un_trabajo_ligado_al_plan(self):
        self.ready(self.client_a)
        job, _fake = self.request(self.client_a, self.trainer_a)
        self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
        plan = job.plan
        token = services.new_request_token()
        with self.fake_ai() as _fake, self.captureOnCommitCallbacks(execute=True):
            new_job, created = regenerate_plan(plan, self.trainer_a, 'Sin saltos', token)
        self.assertTrue(created)
        self.assertEqual(new_job.parent_plan, plan)
        self.assertEqual(new_job.trainer_notes, 'Sin saltos')
        self.assertIn(PlanEvent.ACTION_REGENERATE, [event[0] for event in self.events(plan)])
        # El mismo token no crea otro trabajo ni otro evento
        with self.fake_ai(), self.captureOnCommitCallbacks(execute=True):
            again, created_again = regenerate_plan(plan, self.trainer_a, 'Sin saltos', token)
        self.assertFalse(created_again)
        self.assertEqual(again.pk, new_job.pk)
        regenerations = [event for event in self.events(plan) if event[0] == PlanEvent.ACTION_REGENERATE]
        self.assertEqual(len(regenerations), 1)


# ─── Registro (logger) ────────────────────────────────────────────────────────

class LoggingTests(LifecycleTestCase):
    def test_registra_los_cambios_de_estado_sin_datos_personales(self):
        plan = self.make_plan()
        with self.assertLogs(lifecycle.logger.name, 'INFO') as logs:
            take_for_review(plan, self.trainer_a)
            approve_plan(plan, self.trainer_a)
        text = '\n'.join(logs.output)
        self.assertIn(f'Plan {plan.pk}: draft_ai -> in_review', text)
        self.assertIn(f'Plan {plan.pk}: in_review -> approved', text)
        self.assertNotIn(self.client_a.email, text)
        self.assertNotIn(self.client_a.username, text)

    def test_registra_el_acceso_denegado(self):
        plan = self.make_plan()
        with self.assertLogs(lifecycle.logger.name, 'WARNING'):
            with self.assertRaises(PlanNotAllowed):
                take_for_review(plan, self.trainer_b)
