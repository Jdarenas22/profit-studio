"""Pruebas del ciclo de vida del plan (`exp_a.plan_lifecycle`).

Corren sobre el escenario de planes (`PlanScenarioTestCase`): la IA siempre es un doble, sin red.
Los planes de las pruebas unitarias se crean directamente en la base (sin pasar por la IA) para
controlar su estado; las pruebas de integración usan `request_plan` de verdad.
"""
import uuid
from datetime import timedelta
from unittest import mock

from django.test import override_settings

from apps.accounts.models import User
from apps.health import dates
from apps.plans import services
from apps.plans.models import KIND_EXERCISE, KIND_NUTRITION, Plan, PlanEvent
from apps.plans.tests.helpers import PlanScenarioTestCase
from apps.routines.models import Routine

from exp_a import plan_lifecycle as lifecycle
from exp_a.plan_lifecycle import PlanNotAllowed, PlanStateError

NOTIFY = 'apps.plans.notifications.client_plan_approved'


class LifecycleTestCase(PlanScenarioTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # No depende de cómo el escenario reparta las clientas: la asignación se fija aquí.
        User.objects.filter(pk=cls.client_a.pk).update(assigned_trainer=cls.trainer_a)
        cls.client_a.refresh_from_db()
        cls.outsider = User.objects.create_user(
            username='sin_acceso', password='clave-de-prueba-123', role=User.ROLE_TRAINER,
        )

    def make_plan(self, client=None, *, status=Plan.STATUS_DRAFT_AI, kind=KIND_EXERCISE, version=1,
                  with_routine=True, flags=None, simulated=False, reviewed_by=None):
        client = client or self.client_a
        routine = None
        if with_routine:
            routine = Routine.objects.create(
                name=f'Rutina v{version}', user=client, trainer=self.trainer_a,
                is_active=(status == Plan.STATUS_APPROVED),
            )
        return Plan.objects.create(
            client=client, kind=kind, version=version, status=status, routine=routine,
            flags=flags or [], is_simulated=simulated, reviewed_by=reviewed_by,
        )

    def events(self, plan, action=None):
        qs = PlanEvent.objects.filter(plan=plan)
        return qs.filter(action=action) if action else qs

    def routine_active(self, plan):
        return Routine.objects.get(pk=plan.routine_id).is_active


class AuthorizationTests(LifecycleTestCase):
    def calls(self, plan, actor):
        token = uuid.uuid4()
        return {
            'take_for_review': lambda: lifecycle.take_for_review(plan, actor),
            'archive_plan': lambda: lifecycle.archive_plan(plan, actor, 'x'),
            'approve_plan': lambda: lifecycle.approve_plan(plan, actor),
            'regenerate_plan': lambda: lifecycle.regenerate_plan(plan, actor, 'x', token),
        }

    def test_member_is_denied_in_every_operation(self):
        plan = self.make_plan()
        for name, call in self.calls(plan, self.client_a).items():
            with self.subTest(operation=name), self.assertRaisesMessage(PlanNotAllowed, 'No tienes acceso a este plan.'):
                call()
        plan.refresh_from_db()
        self.assertEqual(plan.status, Plan.STATUS_DRAFT_AI)
        self.assertEqual(PlanEvent.objects.count(), 0)

    def test_trainer_without_access_is_denied_in_every_operation(self):
        plan = self.make_plan()
        for name, call in self.calls(plan, self.outsider).items():
            with self.subTest(operation=name), self.assertRaises(PlanNotAllowed):
                call()
        plan.refresh_from_db()
        self.assertEqual(plan.status, Plan.STATUS_DRAFT_AI)
        self.assertIsNone(plan.reviewed_by_id)
        self.assertEqual(PlanEvent.objects.count(), 0)

    def test_anonymous_actor_is_denied(self):
        from django.contrib.auth.models import AnonymousUser
        with self.assertRaises(PlanNotAllowed):
            lifecycle.take_for_review(self.make_plan(), AnonymousUser())

    def test_denied_regenerate_never_requests_a_job(self):
        plan = self.make_plan()
        with mock.patch.object(services, 'request_plan') as request_plan:
            with self.assertRaises(PlanNotAllowed):
                lifecycle.regenerate_plan(plan, self.outsider, 'x', uuid.uuid4())
        request_plan.assert_not_called()

    def test_assigned_trainer_and_superuser_are_allowed(self):
        for actor in (self.trainer_a, self.boss):
            with self.subTest(actor=actor.username):
                plan = self.make_plan(version=Plan.objects.count() + 1)
                self.assertTrue(lifecycle.take_for_review(plan, actor))


class TakeForReviewTests(LifecycleTestCase):
    def test_draft_goes_to_review(self):
        plan = self.make_plan()
        self.assertIs(lifecycle.take_for_review(plan, self.trainer_a), True)
        self.assertEqual(plan.status, Plan.STATUS_IN_REVIEW)
        self.assertEqual(plan.reviewed_by, self.trainer_a)
        stored = Plan.objects.get(pk=plan.pk)
        self.assertEqual(stored.status, Plan.STATUS_IN_REVIEW)
        self.assertEqual(stored.reviewed_by, self.trainer_a)
        event = self.events(plan, PlanEvent.ACTION_TAKEN).get()
        self.assertEqual(event.actor, self.trainer_a)

    def test_other_states_do_nothing(self):
        for status in (Plan.STATUS_IN_REVIEW, Plan.STATUS_APPROVED, Plan.STATUS_SUPERSEDED, Plan.STATUS_ARCHIVED):
            with self.subTest(status=status):
                plan = self.make_plan(status=status, version=Plan.objects.count() + 1)
                self.assertIs(lifecycle.take_for_review(plan, self.trainer_a), False)
                stored = Plan.objects.get(pk=plan.pk)
                self.assertEqual(stored.status, status)
                self.assertIsNone(stored.reviewed_by_id)
                self.assertFalse(self.events(plan).exists())

    def test_stale_object_does_not_override_a_newer_state(self):
        plan = self.make_plan()
        Plan.objects.filter(pk=plan.pk).update(status=Plan.STATUS_IN_REVIEW, reviewed_by=self.boss)
        self.assertIs(lifecycle.take_for_review(plan, self.trainer_a), False)
        self.assertEqual(Plan.objects.get(pk=plan.pk).reviewed_by, self.boss)
        self.assertFalse(self.events(plan).exists())

    def test_second_call_returns_false(self):
        plan = self.make_plan()
        self.assertTrue(lifecycle.take_for_review(plan, self.trainer_a))
        self.assertFalse(lifecycle.take_for_review(plan, self.boss))
        self.assertEqual(self.events(plan, PlanEvent.ACTION_TAKEN).count(), 1)
        self.assertEqual(Plan.objects.get(pk=plan.pk).reviewed_by, self.trainer_a)


class ArchiveTests(LifecycleTestCase):
    def test_archivable_states(self):
        for status in lifecycle.ARCHIVABLE_STATUSES:
            with self.subTest(status=status):
                plan = self.make_plan(status=status, version=Plan.objects.count() + 1)
                result = lifecycle.archive_plan(plan, self.trainer_a, 'Ya no aplica')
                self.assertIs(result, plan)
                stored = Plan.objects.get(pk=plan.pk)
                self.assertEqual(stored.status, Plan.STATUS_ARCHIVED)
                self.assertIsNotNone(stored.archived_at)
                self.assertFalse(self.routine_active(stored))
                event = self.events(plan, PlanEvent.ACTION_ARCHIVED).get()
                self.assertEqual(event.note, 'Ya no aplica')
                self.assertEqual(event.actor, self.trainer_a)

    def test_non_archivable_states_raise(self):
        for status in (Plan.STATUS_SUPERSEDED, Plan.STATUS_ARCHIVED):
            with self.subTest(status=status):
                plan = self.make_plan(status=status, version=Plan.objects.count() + 1)
                with self.assertRaisesMessage(PlanStateError, 'Este plan ya no se puede archivar.'):
                    lifecycle.archive_plan(plan, self.trainer_a, 'x')
                self.assertEqual(Plan.objects.get(pk=plan.pk).status, status)
                self.assertFalse(self.events(plan).exists())

    def test_reason_is_clipped_to_300_characters(self):
        plan = self.make_plan()
        lifecycle.archive_plan(plan, self.trainer_a, 'm' * 400)
        self.assertEqual(self.events(plan, PlanEvent.ACTION_ARCHIVED).get().note, 'm' * 300)

    def test_empty_reason_uses_default(self):
        for index, reason in enumerate(('', '   ', None)):
            with self.subTest(reason=reason):
                plan = self.make_plan(version=index + 1)
                lifecycle.archive_plan(plan, self.trainer_a, reason)
                self.assertEqual(
                    self.events(plan, PlanEvent.ACTION_ARCHIVED).get().note, 'Archivado por el entrenador.')

    def test_default_reason_when_omitted(self):
        plan = self.make_plan()
        lifecycle.archive_plan(plan, self.trainer_a)
        self.assertEqual(self.events(plan, PlanEvent.ACTION_ARCHIVED).get().note, 'Archivado por el entrenador.')

    def test_plan_without_routine(self):
        plan = self.make_plan(with_routine=False)
        lifecycle.archive_plan(plan, self.trainer_a)
        self.assertEqual(plan.status, Plan.STATUS_ARCHIVED)

    def test_stale_object_cannot_archive_a_superseded_plan(self):
        plan = self.make_plan()
        Plan.objects.filter(pk=plan.pk).update(status=Plan.STATUS_SUPERSEDED)
        with self.assertRaises(PlanStateError):
            lifecycle.archive_plan(plan, self.trainer_a)
        self.assertEqual(Plan.objects.get(pk=plan.pk).status, Plan.STATUS_SUPERSEDED)

    def test_archiving_only_touches_its_own_routine(self):
        mine = self.make_plan(status=Plan.STATUS_APPROVED)
        other = self.make_plan(status=Plan.STATUS_APPROVED, kind=KIND_NUTRITION)
        lifecycle.archive_plan(mine, self.trainer_a)
        self.assertFalse(self.routine_active(mine))
        self.assertTrue(self.routine_active(other))


class ApproveTests(LifecycleTestCase):
    def approve(self, plan, actor=None, **kwargs):
        with self.captureOnCommitCallbacks(execute=True), mock.patch(NOTIFY):
            return lifecycle.approve_plan(plan, actor or self.trainer_a, **kwargs)

    def test_approving_a_draft(self):
        plan = self.make_plan()
        before = dates.today()
        result = self.approve(plan, note_for_client='Buen trabajo')
        self.assertIs(result, plan)
        stored = Plan.objects.get(pk=plan.pk)
        self.assertEqual(stored.status, Plan.STATUS_APPROVED)
        self.assertEqual(stored.approved_by, self.trainer_a)
        self.assertEqual(stored.reviewed_by, self.trainer_a)
        self.assertIsNotNone(stored.approved_at)
        self.assertEqual(stored.note_for_client, 'Buen trabajo')
        self.assertIn(stored.valid_until, (before + timedelta(days=28), dates.today() + timedelta(days=28)))
        self.assertIsNone(stored.warnings_ack_by)
        self.assertIsNone(stored.warnings_ack_at)
        self.assertTrue(self.routine_active(stored))
        event = self.events(plan, PlanEvent.ACTION_APPROVED).get()
        self.assertEqual(event.actor, self.trainer_a)

    def test_approving_a_plan_in_review_keeps_the_reviewer(self):
        plan = self.make_plan(status=Plan.STATUS_IN_REVIEW, reviewed_by=self.trainer_a)
        self.approve(plan, actor=self.boss)
        stored = Plan.objects.get(pk=plan.pk)
        self.assertEqual(stored.reviewed_by, self.trainer_a)
        self.assertEqual(stored.approved_by, self.boss)

    def test_note_is_clipped_to_500_characters(self):
        plan = self.make_plan()
        self.approve(plan, note_for_client='n' * 700)
        self.assertEqual(Plan.objects.get(pk=plan.pk).note_for_client, 'n' * 500)

    def test_valid_until_uses_health_dates_today(self):
        import datetime
        plan = self.make_plan()
        with mock.patch.object(dates, 'today', return_value=datetime.date(2030, 1, 1)):
            self.approve(plan)
        self.assertEqual(Plan.objects.get(pk=plan.pk).valid_until, datetime.date(2030, 1, 29))

    def test_previous_approved_plan_is_superseded(self):
        old = self.make_plan(status=Plan.STATUS_APPROVED, version=1)
        new = self.make_plan(version=2)
        self.approve(new)
        old.refresh_from_db()
        self.assertEqual(old.status, Plan.STATUS_SUPERSEDED)
        self.assertFalse(self.routine_active(old))
        event = self.events(old, PlanEvent.ACTION_SUPERSEDED).get()
        self.assertEqual(event.note, 'Reemplazado por la versión 2.')
        self.assertEqual(event.actor, self.trainer_a)
        self.assertEqual(new.status, Plan.STATUS_APPROVED)
        self.assertTrue(self.routine_active(new))
        self.assertEqual(Plan.objects.filter(client=self.client_a, kind=KIND_EXERCISE, status='approved').count(), 1)

    def test_other_kind_and_other_client_are_not_superseded(self):
        other_kind = self.make_plan(status=Plan.STATUS_APPROVED, kind=KIND_NUTRITION)
        other_client = self.make_plan(self.client_b, status=Plan.STATUS_APPROVED)
        self.approve(self.make_plan(version=2))
        for plan in (other_kind, other_client):
            plan.refresh_from_db()
            self.assertEqual(plan.status, Plan.STATUS_APPROVED)
            self.assertTrue(self.routine_active(plan))
            self.assertFalse(self.events(plan, PlanEvent.ACTION_SUPERSEDED).exists())

    def test_plan_without_routine(self):
        plan = self.make_plan(with_routine=False)
        self.approve(plan)
        self.assertEqual(plan.status, Plan.STATUS_APPROVED)

    def test_not_pending_states_raise(self):
        for status in (Plan.STATUS_APPROVED, Plan.STATUS_SUPERSEDED, Plan.STATUS_ARCHIVED):
            with self.subTest(status=status):
                plan = self.make_plan(status=status, version=Plan.objects.count() + 1)
                with self.assertRaisesMessage(
                        PlanStateError, 'Solo se puede aprobar un borrador o un plan en revisión.'):
                    self.approve(plan)
                self.assertEqual(Plan.objects.get(pk=plan.pk).status, status)

    def test_simulated_plan_is_rejected_outside_debug(self):
        plan = self.make_plan(simulated=True)
        with override_settings(DEBUG=False):
            with self.assertRaisesMessage(PlanStateError, 'MODO DE PRUEBA'):
                self.approve(plan)
        self.assertEqual(Plan.objects.get(pk=plan.pk).status, Plan.STATUS_DRAFT_AI)
        self.assertFalse(self.routine_active(plan))

    def test_simulated_plan_can_be_approved_in_debug(self):
        plan = self.make_plan(simulated=True)
        with override_settings(DEBUG=True):
            self.approve(plan)
        self.assertEqual(Plan.objects.get(pk=plan.pk).status, Plan.STATUS_APPROVED)

    def test_flags_require_acknowledgement(self):
        plan = self.make_plan(flags=['Y01'])
        with self.assertRaisesMessage(PlanStateError, 'Marca "Revisé las advertencias" para aprobar.'):
            self.approve(plan)
        self.assertEqual(Plan.objects.get(pk=plan.pk).status, Plan.STATUS_DRAFT_AI)

    def test_acknowledged_flags_are_recorded(self):
        plan = self.make_plan(flags=['Y01'])
        self.approve(plan, warnings_ack=True)
        stored = Plan.objects.get(pk=plan.pk)
        self.assertEqual(stored.status, Plan.STATUS_APPROVED)
        self.assertEqual(stored.warnings_ack_by, self.trainer_a)
        self.assertIsNotNone(stored.warnings_ack_at)

    def test_ack_without_flags_records_nothing(self):
        plan = self.make_plan()
        self.approve(plan, warnings_ack=True)
        self.assertIsNone(Plan.objects.get(pk=plan.pk).warnings_ack_by)

    def test_stale_ack_is_accepted_and_ignored(self):
        plan = self.make_plan()
        self.approve(plan, stale_ack=True)
        self.assertEqual(plan.status, Plan.STATUS_APPROVED)

    def test_state_changed_while_reviewing(self):
        old = self.make_plan(status=Plan.STATUS_APPROVED, version=1)
        plan = self.make_plan(version=2)
        Plan.objects.filter(pk=plan.pk).update(status=Plan.STATUS_ARCHIVED)   # otra persona lo archivó
        with self.assertRaisesMessage(
                PlanStateError, 'Este plan cambió mientras lo revisabas. Recarga la página.'):
            self.approve(plan)
        self.assertEqual(Plan.objects.get(pk=plan.pk).status, Plan.STATUS_ARCHIVED)
        old.refresh_from_db()
        self.assertEqual(old.status, Plan.STATUS_APPROVED)
        self.assertTrue(self.routine_active(old))

    def test_client_is_notified_once_after_commit(self):
        plan = self.make_plan()
        with mock.patch(NOTIFY) as notify:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                lifecycle.approve_plan(plan, self.trainer_a)
                notify.assert_not_called()        # todavía no se confirmó la transacción
            self.assertEqual(len(callbacks), 1)
            notify.assert_not_called()
            callbacks[0]()                        # la confirmación de la transacción
        notify.assert_called_once_with(plan)

    def test_no_notification_when_approval_fails(self):
        plan = self.make_plan(simulated=True)
        with mock.patch(NOTIFY) as notify, self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(PlanStateError):
                lifecycle.approve_plan(plan, self.trainer_a)
        notify.assert_not_called()

    def test_notification_failure_does_not_undo_the_approval(self):
        plan = self.make_plan()
        with mock.patch(NOTIFY, side_effect=RuntimeError('correo caído')):
            with self.assertLogs(lifecycle.logger, level='ERROR'), self.captureOnCommitCallbacks(execute=True):
                lifecycle.approve_plan(plan, self.trainer_a)
        self.assertEqual(Plan.objects.get(pk=plan.pk).status, Plan.STATUS_APPROVED)

    def test_everything_is_rolled_back_if_a_step_fails(self):
        old = self.make_plan(status=Plan.STATUS_APPROVED, version=1)
        new = self.make_plan(version=2)
        real_record = lifecycle._record_event

        def failing_record(plan, actor, action, note=''):
            if action == PlanEvent.ACTION_APPROVED:
                raise RuntimeError('falla simulada')
            return real_record(plan, actor, action, note)

        with mock.patch.object(lifecycle, '_record_event', side_effect=failing_record):
            with self.assertRaises(RuntimeError):
                self.approve(new)
        old.refresh_from_db()
        new.refresh_from_db()
        self.assertEqual(old.status, Plan.STATUS_APPROVED)
        self.assertTrue(self.routine_active(old))
        self.assertEqual(new.status, Plan.STATUS_DRAFT_AI)
        self.assertFalse(self.routine_active(new))
        self.assertFalse(self.events(old).exists())

    def test_logs_the_state_change_without_free_text(self):
        plan = self.make_plan()
        with self.assertLogs(lifecycle.logger, level='INFO') as captured:
            self.approve(plan, note_for_client='texto privado de la nota')
        output = '\n'.join(captured.output)
        self.assertIn(f'Plan {plan.pk}', output)
        self.assertIn('approved', output)
        self.assertNotIn('texto privado', output)


class RegenerateTests(LifecycleTestCase):
    def test_requests_a_job_and_records_the_event(self):
        plan = self.make_plan()
        token = uuid.uuid4()
        with mock.patch.object(services, 'request_plan', return_value=('job', True)) as request_plan:
            result = lifecycle.regenerate_plan(plan, self.trainer_a, 'Más trabajo de piernas', token)
        self.assertEqual(result, ('job', True))
        request_plan.assert_called_once_with(
            plan.client, plan.kind, self.trainer_a, token,
            trainer_notes='Más trabajo de piernas', parent_plan=plan,
        )
        event = self.events(plan, PlanEvent.ACTION_REGENERATE).get()
        self.assertEqual(event.note, 'Más trabajo de piernas')
        self.assertEqual(event.actor, self.trainer_a)

    def test_in_review_is_allowed_and_notes_are_clipped(self):
        plan = self.make_plan(status=Plan.STATUS_IN_REVIEW)
        with mock.patch.object(services, 'request_plan', return_value=('job', True)):
            lifecycle.regenerate_plan(plan, self.trainer_a, 'z' * 800, uuid.uuid4())
        self.assertEqual(self.events(plan, PlanEvent.ACTION_REGENERATE).get().note, 'z' * 500)

    def test_repeated_request_does_not_record_a_second_event(self):
        plan = self.make_plan()
        with mock.patch.object(services, 'request_plan', return_value=('job', False)):
            result = lifecycle.regenerate_plan(plan, self.trainer_a, 'nota', uuid.uuid4())
        self.assertEqual(result, ('job', False))
        self.assertFalse(self.events(plan).exists())

    def test_non_pending_states_raise_without_requesting(self):
        for status in (Plan.STATUS_APPROVED, Plan.STATUS_SUPERSEDED, Plan.STATUS_ARCHIVED):
            with self.subTest(status=status):
                plan = self.make_plan(status=status, version=Plan.objects.count() + 1)
                with mock.patch.object(services, 'request_plan') as request_plan:
                    with self.assertRaisesMessage(PlanStateError, 'Solo se regenera un borrador o un plan en revisión.'):
                        lifecycle.regenerate_plan(plan, self.trainer_a, 'x', uuid.uuid4())
                request_plan.assert_not_called()

    def test_stale_object_uses_the_stored_state(self):
        plan = self.make_plan()
        Plan.objects.filter(pk=plan.pk).update(status=Plan.STATUS_APPROVED)
        with mock.patch.object(services, 'request_plan') as request_plan:
            with self.assertRaises(PlanStateError):
                lifecycle.regenerate_plan(plan, self.trainer_a, 'x', uuid.uuid4())
        request_plan.assert_not_called()

    def test_event_is_rolled_back_with_the_job_when_the_event_fails(self):
        plan = self.make_plan()
        with mock.patch.object(services, 'request_plan', return_value=('job', True)):
            with mock.patch.object(lifecycle, '_record_event', side_effect=RuntimeError('falla simulada')):
                with self.assertRaises(RuntimeError):
                    lifecycle.regenerate_plan(plan, self.trainer_a, 'x', uuid.uuid4())
        self.assertFalse(self.events(plan).exists())


class IntegrationTests(LifecycleTestCase):
    """Con `request_plan` y la rutina generadas de verdad (IA simulada, sin red)."""

    def draft_for(self, client):
        self.ready(client)
        job, _fake = self.request(client)
        self.assertEqual(job.status, job.STATUS_SUCCEEDED, job.error_code)
        return job.plan

    def test_generated_draft_goes_through_review_and_approval(self):
        plan = self.draft_for(self.client_a)
        self.assertEqual(plan.status, Plan.STATUS_DRAFT_AI)
        self.assertFalse(plan.routine.is_active)
        self.assertTrue(lifecycle.take_for_review(plan, self.trainer_a))
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)   # como si viniera de la IA real
        plan.refresh_from_db()
        with mock.patch(NOTIFY) as notify, self.captureOnCommitCallbacks(execute=True):
            lifecycle.approve_plan(plan, self.trainer_a, note_for_client='Vamos con todo', warnings_ack=True)
        plan.refresh_from_db()
        self.assertEqual(plan.status, Plan.STATUS_APPROVED)
        self.assertTrue(Routine.objects.get(pk=plan.routine_id).is_active)
        notify.assert_called_once()
        actions = list(self.events(plan).order_by('pk').values_list('action', flat=True))
        self.assertEqual(actions[-2:], [PlanEvent.ACTION_TAKEN, PlanEvent.ACTION_APPROVED])

    def test_real_regeneration_creates_a_child_job_once(self):
        plan = self.draft_for(self.client_a)
        token = services.new_request_token()
        # Sin ejecutar los callbacks de confirmación: el trabajo queda en cola y no cambia el plan.
        job, created = lifecycle.regenerate_plan(plan, self.trainer_a, 'Más piernas', token)
        self.assertTrue(created)
        self.assertEqual(job.parent_plan, plan)
        self.assertEqual(job.trainer_notes, 'Más piernas')
        again, created_again = lifecycle.regenerate_plan(plan, self.trainer_a, 'Más piernas', token)
        self.assertFalse(created_again)
        self.assertEqual(again.pk, job.pk)
        self.assertEqual(self.events(plan, PlanEvent.ACTION_REGENERATE).count(), 1)
