"""Flujo completo de la fase B sin red: pedido -> trabajo -> borrador -> revisión -> aprobación.

Cubre (contrato, sección 11): bandera roja sin llamar a la IA, validación con un reintento,
reintentos por errores transitorios, idempotencia, cupos, recuperación de trabajos huérfanos,
privacidad de lo que se envía y de los logs, y que la clienta nunca vea un borrador.
"""
import json
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.plans import jobs, services
from apps.plans.models import KIND_EXERCISE, Plan, PlanEvent, PlanGenerationJob
from apps.routines.models import Routine, RoutineDayLog

from .helpers import PlanScenarioTestCase


class RequestAndGenerateTests(PlanScenarioTestCase):
    def test_success_creates_inactive_routine_and_draft(self):
        self.ready(self.client_a)
        job, fake = self.request(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        plan = job.plan
        self.assertEqual((plan.status, plan.kind, plan.version), (Plan.STATUS_DRAFT_AI, KIND_EXERCISE, 1))
        self.assertTrue(plan.is_simulated)
        self.assertFalse(plan.routine.is_active)
        self.assertEqual(plan.routine.days.count(), 3)               # training_days_per_week de la ficha
        self.assertEqual(plan.profile_version, plan.health_profile.version)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual((job.tokens_in, job.tokens_out), (1000, 500))
        self.assertTrue(PlanEvent.objects.filter(plan=plan, action=PlanEvent.ACTION_CREATED).exists())
        # Aviso al entrenador asignado, sin datos de salud
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.trainer_a.email])
        self.assertNotIn(self.client_a.first_name, mail.outbox[0].body)

    def test_payload_sent_to_ai_is_minimal(self):
        self.ready(self.client_a, injuries=['knee'], other_condition_text='Algo privado')
        _job, fake = self.request(self.client_a)
        payload = fake.calls[0]['payload']
        sent = json.dumps(payload, ensure_ascii=False)
        for secret in (self.client_a.username, self.client_a.email, self.client_a.first_name,
                       self.client_a.last_name, self.trainer_a.username, 'Algo privado', 'knee'):
            self.assertNotIn(secret, sent)
        self.assertNotIn('birth_date', sent)
        self.assertEqual(set(payload), {'task', 'schema_version', 'job_ref', 'client', 'training', 'limits',
                                        'allowed_exercises', 'trainer_notes', 'previous_errors'})
        self.assertNotEqual(payload['job_ref'], str(self.client_a.pk))
        self.assertEqual(set(payload['client']), {'age', 'sex', 'goal', 'experience', 'activity', 'conservative'})
        self.assertTrue(payload['client']['conservative'])           # lesión = advertencia Y04
        allowed = {item['id'] for item in payload['allowed_exercises']}
        self.assertNotIn(self.exercise.pk, allowed)                  # sin etiquetar + lesión = fuera

    def test_logs_have_no_health_data(self):
        self.ready(self.client_a, other_condition_text='Algo privado')
        with self.assertLogs('apps.plans', level='INFO') as logs:
            self.request(self.client_a)
        text = '\n'.join(logs.output)
        self.assertIn('status=succeeded', text)
        for secret in ('Algo privado', self.client_a.username, self.client_a.email):
            self.assertNotIn(secret, text)

    def test_red_flag_blocks_without_calling_ai(self):
        self.ready(self.client_a, pregnancy_status='pregnant')
        job, fake = self.request(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_BLOCKED)
        self.assertIn('R02_PREGNANCY', job.flags)
        self.assertEqual(fake.calls, [])
        self.assertFalse(Plan.objects.exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertNotIn('embarazo', mail.outbox[0].body.lower())

    def test_repeated_blocked_request_notifies_once_a_day(self):
        self.ready(self.client_a, pregnancy_status='pregnant')
        first, _ = self.request(self.client_a)
        again, _ = self.request(self.client_a)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(PlanGenerationJob.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)
        PlanGenerationJob.objects.update(created_at=timezone.now() - timedelta(days=2))
        later, _ = self.request(self.client_a)
        self.assertNotEqual(later.pk, first.pk)

    def test_few_compatible_exercises_blocks(self):
        self.ready(self.client_a, training_place='home', equipment=['none'])   # todo el catálogo usa máquinas
        job, fake = self.request(self.client_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_BLOCKED)
        self.assertEqual(job.flags, ['D06_FEW_EXERCISES'])
        self.assertEqual(fake.calls, [])

    def test_missing_requirements_create_nothing(self):
        self.grant(self.client_a)            # sin consentimiento de IA ni ficha
        with self.assertRaises(services.PlanRequestNotReady) as ctx:
            self.request(self.client_a)
        self.assertIn('D01_CONSENT_AI', ctx.exception.eligibility.missing)
        self.assertIn('D02_PROFILE', ctx.exception.eligibility.missing)
        self.assertFalse(PlanGenerationJob.objects.exists())

    def test_legal_placeholders_block_outside_debug(self):
        self.ready(self.client_a)
        self.publish_new_version('ai_processing', body='Responsable: [RAZÓN SOCIAL]')
        with self.assertRaises(services.PlanRequestNotReady) as ctx:
            self.request(self.client_a)
        self.assertIn('D00_NOT_AVAILABLE', ctx.exception.eligibility.missing)

    def test_same_token_is_idempotent_and_one_active_job(self):
        self.ready(self.client_a)
        token = services.new_request_token()
        with self.settings(AI_PLANS_RUN_INLINE=False), mock.patch.object(jobs, 'start_job') as start, \
                self.captureOnCommitCallbacks(execute=True):
            first, created = services.request_plan(self.client_a, KIND_EXERCISE, self.client_a, token)
            again, created_again = services.request_plan(self.client_a, KIND_EXERCISE, self.client_a, token)
            other, created_other = services.request_plan(
                self.client_a, KIND_EXERCISE, self.client_a, services.new_request_token())
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertFalse(created_other)
        self.assertEqual({first.pk, again.pk, other.pk}, {first.pk})
        start.assert_called_once_with(first.pk)

    def test_bad_token_and_foreign_actor(self):
        self.ready(self.client_a)
        with self.assertRaises(services.PlanError):
            services.request_plan(self.client_a, KIND_EXERCISE, self.client_a, 'no-es-uuid')
        with self.assertRaises(services.PlanNotAllowed):
            services.request_plan(self.client_a, KIND_EXERCISE, self.trainer_b, services.new_request_token())
        with self.assertRaises(services.PlanNotAllowed):
            services.request_plan(self.client_a, KIND_EXERCISE, self.client_b, services.new_request_token())
        with self.assertRaises(services.PlanNotAllowed):
            services.request_plan(self.client_a, 'nutrition', self.client_a, services.new_request_token())

    @override_settings(AI_PLANS_CLIENT_WEEKLY_LIMIT=1)
    def test_client_weekly_quota_counts_failures_not_blocks(self):
        self.ready(self.client_a)
        job, _ = self.request(self.client_a, scenario='permanent')
        self.assertEqual(job.status, PlanGenerationJob.STATUS_FAILED)
        with self.assertRaises(services.PlanRequestNotReady) as ctx:
            self.request(self.client_a)
        self.assertEqual(ctx.exception.eligibility.missing, ('D05_QUOTA',))
        # El entrenador tiene su propio cupo
        job, _ = self.request(self.client_a, actor=self.trainer_a)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED)

    @override_settings(AI_PLANS_TRAINER_DAILY_LIMIT=1)
    def test_trainer_daily_quota(self):
        self.ready(self.client_a)
        self.request(self.client_a, actor=self.trainer_a)
        with self.assertRaises(services.QuotaExceeded):
            self.request(self.client_a, actor=self.trainer_a)


class AIFailureTests(PlanScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.ready(self.client_a)

    def test_validation_failure_retries_once_with_codes(self):
        job, fake = self.request(self.client_a, scenario=['unknown_id', 'ok'])
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(fake.calls[1]['payload']['previous_errors'], ['V02_IDS'])

    def test_two_validation_failures_fail_and_save_nothing(self):
        for scenario, code in (('forbidden_text', 'validation_failed:V08_TEXT'),
                               ('wrong_count', 'validation_failed:V07_EXERCISE'),
                               ('invalid_json', 'validation_failed:V01_SCHEMA'),
                               ('max_tokens', 'ai_finish_max_tokens')):
            with self.subTest(scenario=scenario):
                PlanGenerationJob.objects.all().delete()
                job, fake = self.request(self.client_a, scenario=scenario)
                self.assertEqual(job.status, PlanGenerationJob.STATUS_FAILED)
                self.assertEqual(job.error_code, code)
                self.assertEqual(len(fake.calls), 2)
                self.assertFalse(Plan.objects.exists())
                self.assertFalse(Routine.objects.filter(is_active=False).exists())

    def test_transient_errors_are_retried(self):
        job, fake = self.request(self.client_a, scenario=['rate_limited', 'timeout', 'ok'])
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED)
        self.assertEqual(len(fake.calls), 3)

    def test_transient_errors_give_up_after_three(self):
        job, fake = self.request(self.client_a, scenario='timeout')
        self.assertEqual((job.status, job.error_code), (PlanGenerationJob.STATUS_FAILED, 'ai_timeout'))
        self.assertEqual(len(fake.calls), 3)

    def test_permanent_and_safety_errors_are_not_retried(self):
        for scenario, code in (('permanent', 'ai_http_400'), ('safety_block', 'ai_blocked_safety')):
            with self.subTest(scenario=scenario):
                PlanGenerationJob.objects.all().delete()
                job, fake = self.request(self.client_a, scenario=scenario)
                self.assertEqual((job.status, job.error_code), (PlanGenerationJob.STATUS_FAILED, code))
                self.assertEqual(len(fake.calls), 1)

    def test_ai_mode_off_during_run_fails_cleanly(self):
        token = services.new_request_token()
        with self.settings(AI_PLANS_RUN_INLINE=False), mock.patch.object(jobs, 'start_job'), \
                self.captureOnCommitCallbacks(execute=True):
            job, _ = services.request_plan(self.client_a, KIND_EXERCISE, self.client_a, token)
        with self.settings(AI_PLANS_MODE='off'):
            jobs.run_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, PlanGenerationJob.STATUS_FAILED)
        self.assertEqual(job.error_code, 'not_ready:D00_NOT_AVAILABLE')


class RecoveryTests(PlanScenarioTestCase):
    def make_job(self, **fields):
        self.ready(self.client_a)
        defaults = dict(kind=KIND_EXERCISE, client=self.client_a, requested_by=self.client_a,
                        requested_by_role='member', request_token=services.new_request_token(),
                        status=PlanGenerationJob.STATUS_RUNNING, attempts=1,
                        heartbeat_at=timezone.now() - timedelta(minutes=5))
        defaults.update(fields)
        return PlanGenerationJob.objects.create(**defaults)

    def test_stale_running_job_is_requeued_and_run(self):
        job = self.make_job()
        with self.fake_ai():
            relaunched = jobs.recover_stale_jobs()
        job.refresh_from_db()
        self.assertEqual(relaunched, [job.pk])
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED)
        self.assertEqual(job.attempts, 2)

    def test_stale_job_without_attempts_left_fails(self):
        job = self.make_job(attempts=3, max_attempts=3)
        jobs.recover_stale_jobs()
        job.refresh_from_db()
        self.assertEqual((job.status, job.error_code), (PlanGenerationJob.STATUS_FAILED, 'worker_lost'))

    def test_fresh_running_job_is_left_alone_and_claim_is_exclusive(self):
        job = self.make_job(heartbeat_at=timezone.now())
        self.assertEqual(jobs.recover_stale_jobs(start=False), [])
        self.assertFalse(jobs.claim(job.pk))     # ya está corriendo: nadie más lo toma

    def test_forgotten_queued_job_is_relaunched(self):
        job = self.make_job(status=PlanGenerationJob.STATUS_QUEUED, attempts=0, heartbeat_at=None)
        PlanGenerationJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(minutes=5))
        self.assertEqual(jobs.recover_stale_jobs(start=False), [job.pk])


class ReviewAndApprovalTests(PlanScenarioTestCase):
    def draft(self, client=None, **profile):
        client = client or self.client_a
        if not services.health.current_profile(client):
            self.ready(client, **profile)
        job, _ = self.request(client)
        self.assertEqual(job.status, PlanGenerationJob.STATUS_SUCCEEDED, job.error_code)
        return job.plan

    def test_client_never_sees_a_draft(self):
        plan = self.draft()
        self.login(self.client_a)
        response = self.client.get(reverse('member_routine'))
        self.assertNotIn(plan.routine, list(response.context['routines']))
        response = self.client.get(reverse('dashboard'))
        self.assertNotContains(response, plan.routine.name)
        # marcar un día del borrador como hecho: 404 (antes no se filtraba is_active)
        day = plan.routine.days.first()
        response = self.client.post(reverse('mark_day_complete', args=[day.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(RoutineDayLog.objects.filter(routine_day=day).exists())

    @override_settings(DEBUG=True)   # los planes simulados solo se aprueban con DEBUG
    def test_approve_activates_and_supersedes(self):
        first = self.draft()
        with self.captureOnCommitCallbacks(execute=True):
            services.approve_plan(first, self.trainer_a, note_for_client='¡Vamos!')
        first.refresh_from_db()
        self.assertEqual(first.status, Plan.STATUS_APPROVED)
        self.assertTrue(first.routine.is_active)
        self.assertIsNotNone(first.valid_until)
        self.assertEqual(first.note_for_client, '¡Vamos!')
        self.assertEqual(mail.outbox[-1].to, [self.client_a.email])
        second = self.draft()
        self.assertEqual(second.version, 2)
        services.approve_plan(second, self.trainer_a)
        first.refresh_from_db()
        first.routine.refresh_from_db()
        self.assertEqual(first.status, Plan.STATUS_SUPERSEDED)
        self.assertFalse(first.routine.is_active)
        self.assertEqual(Plan.objects.filter(status=Plan.STATUS_APPROVED).count(), 1)

    def test_simulated_plan_cannot_be_approved_outside_debug(self):
        plan = self.draft()
        with self.assertRaises(services.PlanStateError):
            services.approve_plan(plan, self.trainer_a)

    @override_settings(DEBUG=True)
    def test_warnings_and_stale_profile_need_acknowledgement(self):
        plan = self.draft(injuries=['knee'])
        self.assertIn('Y04_JOINT', plan.flags)
        with self.assertRaises(services.PlanStateError):
            services.approve_plan(plan, self.trainer_a)
        self.make_profile(self.client_a, injuries=['knee'], sleep_hours='8')   # la ficha cambia
        self.assertTrue(services.is_stale(plan))
        with self.assertRaises(services.PlanStateError):
            services.approve_plan(plan, self.trainer_a, warnings_ack=True)
        services.approve_plan(plan, self.trainer_a, warnings_ack=True, stale_ack=True)
        plan.refresh_from_db()
        self.assertEqual(plan.warnings_ack_by, self.trainer_a)

    @override_settings(DEBUG=True)
    def test_empty_routine_cannot_be_approved(self):
        plan = self.draft()
        plan.routine.days.all().delete()
        with self.assertRaises(services.PlanStateError):
            services.approve_plan(plan, self.trainer_a)

    def test_other_trainer_cannot_touch_the_plan(self):
        plan = self.draft()
        for action in (lambda: services.approve_plan(plan, self.trainer_b),
                       lambda: services.archive_plan(plan, self.trainer_b),
                       lambda: services.take_for_review(plan, self.trainer_b)):
            with self.assertRaises(services.PlanNotAllowed):
                action()
        with self.assertRaises(Plan.DoesNotExist):
            services.get_plan_for_trainer(self.trainer_b, plan.pk)
        self.assertEqual(services.get_plan_for_trainer(self.boss, plan.pk), plan)

    def test_take_archive_and_regenerate(self):
        plan = self.draft()
        self.assertTrue(services.take_for_review(plan, self.trainer_a))
        self.assertEqual(plan.status, Plan.STATUS_IN_REVIEW)
        with self.fake_ai() as fake, self.captureOnCommitCallbacks(execute=True):
            job, created = services.regenerate_plan(plan, self.trainer_a, 'Más trabajo de glúteo',
                                                    services.new_request_token())
        job.refresh_from_db()
        plan.refresh_from_db()
        self.assertTrue(created)
        self.assertEqual(fake.calls[0]['payload']['trainer_notes'], 'Más trabajo de glúteo')
        self.assertEqual(plan.status, Plan.STATUS_ARCHIVED)
        self.assertEqual(job.plan.parent, plan)
        self.assertEqual(job.plan.status, Plan.STATUS_DRAFT_AI)
        services.archive_plan(job.plan, self.trainer_a, 'No sirve')
        job.plan.refresh_from_db()
        self.assertEqual(job.plan.status, Plan.STATUS_ARCHIVED)
        with self.assertRaises(services.PlanStateError):
            services.archive_plan(job.plan, self.trainer_a)

    def test_routine_conflicts_flag_unsafe_manual_additions(self):
        plan = self.draft(injuries=['knee'])
        risky = self.tagged[0]
        risky.risk_tags = ['knee']
        risky.save()
        day = plan.routine.days.first()
        day.exercises.create(exercise=risky, sets=3, reps='10', rest_seconds=60, order=99)
        self.assertIn(risky.name, services.routine_conflicts(plan))
