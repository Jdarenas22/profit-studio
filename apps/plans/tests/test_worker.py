"""Modo trabajador de los planes con IA (`AI_PLANS_WORKER`) y el bucle `process_plan_jobs --loop`.

Sin red ni Gemini (siempre `FakeAIClient`). Los hilos nunca se lanzan de verdad: `jobs.threading` se
reemplaza por un doble. El bucle corre con `close_old_connections` y la conexión de `worker` simulados,
porque dentro de una prueba (transacción abierta) cerrar la conexión real la rompería.

La señal de parada es REAL: `signal.raise_signal` entrega SIGTERM/SIGINT al manejador instalado por
`worker.stop_on_signals`, igual que Railway al redesplegar.
"""
import signal
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from django.urls import reverse
from django.utils import timezone

from apps.plans import jobs, services, worker
from apps.plans.management.commands import process_plan_jobs as command_module
from apps.plans.models import KIND_EXERCISE, Plan, PlanGenerationJob

from .helpers import PlanScenarioTestCase

QUEUED = PlanGenerationJob.STATUS_QUEUED
RUNNING = PlanGenerationJob.STATUS_RUNNING
SUCCEEDED = PlanGenerationJob.STATUS_SUCCEEDED
RUNAWAY_SLEEPS = 500
RUNAWAY_POLLS = 300


class RunawayLoop(BaseException):
    """El bucle no se detuvo. Es BaseException a propósito: el `except Exception` del bucle no la traga,
    así que una parada rota hace FALLAR la prueba en vez de colgar la suite."""


class WorkerTestCase(PlanScenarioTestCase):
    def setUp(self):
        super().setUp()
        self.slept = []
        self.ran = []
        self.polls = 0
        for name in ('close_old_connections', 'connection'):
            patcher = mock.patch.object(worker, name)
            setattr(self, f'worker_{name}', patcher.start())
            self.addCleanup(patcher.stop)
        real_next_queued = jobs.run_next_queued

        def guarded_next_queued():
            self.polls += 1
            if self.polls > RUNAWAY_POLLS:
                raise RunawayLoop('el bucle sigue tomando trabajos sin parar')
            return real_next_queued()
        # Las vueltas del bucle pasan por aquí; y el comando nunca duerme de verdad (todo "sleep" pasa por self.sleep)
        for target, name, replacement in ((jobs, 'run_next_queued', guarded_next_queued), (worker, '_sleep', self.sleep)):
            patcher = mock.patch.object(target, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_job(self, client=None, **fields):
        client = client or self.client_a
        defaults = dict(kind=KIND_EXERCISE, client=client, requested_by=client, requested_by_role='member',
                        request_token=services.new_request_token(), status=QUEUED)
        defaults.update(fields)
        return PlanGenerationJob.objects.create(**defaults)

    def three_jobs_out_of_order(self):
        """Tres trabajos en cola (de tres clientas: hay un solo trabajo en curso por clienta y tipo); el
        orden de creación NO coincide con el orden de los ids."""
        now = timezone.now()
        middle = self.make_job(self.client_b, created_at=now - timedelta(minutes=8))
        oldest = self.make_job(self.client_free, created_at=now - timedelta(minutes=9))
        newest = self.make_job(self.client_a, created_at=now - timedelta(minutes=7))
        return oldest, middle, newest

    def fake_run_claimed(self, job_id):
        """Doble de `jobs._run_claimed`: anota el id y termina el trabajo (sin tocar la IA)."""
        self.ran.append(job_id)
        PlanGenerationJob.objects.filter(pk=job_id).update(status=SUCCEEDED, finished_at=timezone.now())

    def patch_run_claimed(self):
        patcher = mock.patch.object(jobs, '_run_claimed', side_effect=self.fake_run_claimed)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sleep(self, seconds):
        self.slept.append(seconds)
        if len(self.slept) > RUNAWAY_SLEEPS:     # un bucle que no se detiene FALLA la prueba en vez de colgarla
            raise RunawayLoop('el bucle no se detuvo')


# ─── Quién lanza hilos: AI_PLANS_WORKER = False (como siempre) o True ─────────────────────────

class StartJobModeTests(WorkerTestCase):
    def test_default_mode_launches_a_thread_in_the_web_process(self):
        with self.settings(AI_PLANS_RUN_INLINE=False, AI_PLANS_WORKER=False), \
                mock.patch.object(jobs, 'threading') as threading:
            jobs.start_job(41)
        threading.Thread.assert_called_once_with(target=jobs._thread_main, args=(41,), name='plan-job-41',
                                                 daemon=True)
        threading.Thread.return_value.start.assert_called_once_with()

    def test_worker_mode_launches_no_thread_and_leaves_the_job_queued(self):
        job = self.make_job()
        with self.settings(AI_PLANS_RUN_INLINE=False, AI_PLANS_WORKER=True), \
                mock.patch.object(jobs, 'threading') as threading, mock.patch.object(jobs, 'run_job') as run_job:
            jobs.start_job(job.pk)
        threading.Thread.assert_not_called()
        run_job.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, QUEUED)

    def test_inline_has_priority_over_both_modes(self):
        for worker_mode in (False, True):
            with self.subTest(worker=worker_mode), \
                    self.settings(AI_PLANS_RUN_INLINE=True, AI_PLANS_WORKER=worker_mode), \
                    mock.patch.object(jobs, 'threading') as threading, mock.patch.object(jobs, 'run_job') as run_job:
                jobs.start_job(7)
            run_job.assert_called_once_with(7)
            threading.Thread.assert_not_called()

    def test_requesting_a_plan_in_worker_mode_queues_it_and_the_loop_runs_it(self):
        self.ready(self.client_a)
        with self.settings(AI_PLANS_RUN_INLINE=False, AI_PLANS_WORKER=True), \
                mock.patch.object(jobs, 'threading') as threading, self.captureOnCommitCallbacks(execute=True):
            job, created = services.request_plan(self.client_a, KIND_EXERCISE, self.client_a,
                                                 services.new_request_token())
        self.assertTrue(created)
        threading.Thread.assert_not_called()
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempts), (QUEUED, 0))
        with self.fake_ai() as fake:
            processed = worker.run_loop(max_jobs=1, sleep=self.sleep)
        job.refresh_from_db()
        self.assertEqual(processed, 1)
        self.assertEqual(job.status, SUCCEEDED, job.error_code)
        self.assertEqual(job.plan.status, Plan.STATUS_DRAFT_AI)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(len(mail.outbox), 1)                       # el aviso al entrenador sale igual
        self.assertEqual(self.slept, [])                            # había trabajo: no durmió

    def old_orphans(self):
        """Un `running` sin latido y un `queued` olvidado hace 5 minutos."""
        stale = self.make_job(status=RUNNING, attempts=1, heartbeat_at=timezone.now() - timedelta(minutes=5))
        forgotten = self.make_job(created_at=timezone.now() - timedelta(minutes=5), client=self.client_b)
        return stale, forgotten

    def test_web_recovery_requeues_orphans_but_launches_no_thread_in_worker_mode(self):
        stale, forgotten = self.old_orphans()
        with self.settings(AI_PLANS_RUN_INLINE=False, AI_PLANS_WORKER=True), \
                mock.patch.object(jobs, 'threading') as threading:
            relaunch = jobs.recover_stale_jobs()
        self.assertEqual(sorted(relaunch), sorted([stale.pk, forgotten.pk]))
        threading.Thread.assert_not_called()
        stale.refresh_from_db()
        self.assertEqual(stale.status, QUEUED)            # sigue devolviendo huérfanos a la cola para el trabajador

    def test_web_recovery_launches_threads_when_worker_mode_is_off(self):
        self.old_orphans()
        with self.settings(AI_PLANS_RUN_INLINE=False, AI_PLANS_WORKER=False), \
                mock.patch.object(jobs, 'threading') as threading:
            jobs.recover_stale_jobs()
        self.assertEqual(threading.Thread.call_count, 2)

    def test_status_page_recovery_launches_no_thread_in_worker_mode(self):
        self.ready(self.client_a)
        job = self.make_job(created_at=timezone.now() - timedelta(minutes=5))
        self.login(self.client_a)
        for worker_mode, launched in ((True, 0), (False, 1)):
            PlanGenerationJob.objects.filter(pk=job.pk).update(status=QUEUED)
            with self.subTest(worker=worker_mode), \
                    self.settings(AI_PLANS_RUN_INLINE=False, AI_PLANS_WORKER=worker_mode), \
                    mock.patch.object(jobs, 'threading') as threading:
                response = self.client.get(reverse('member_plan_request_status', args=[job.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(threading.Thread.call_count, launched)


# ─── El bucle ─────────────────────────────────────────────────────────────────────────────────

class RunNextQueuedTests(WorkerTestCase):
    def test_takes_the_oldest_queued_job_by_creation_time_not_by_id(self):
        self.patch_run_claimed()
        oldest, middle, newest = self.three_jobs_out_of_order()
        self.assertGreater(oldest.pk, middle.pk)           # el más antiguo tiene el id MÁS alto
        self.assertEqual([jobs.run_next_queued() for _ in range(4)], [oldest.pk, middle.pk, newest.pk, None])
        self.assertEqual(self.ran, [oldest.pk, middle.pk, newest.pk])

    def test_ignores_jobs_that_are_not_queued(self):
        self.patch_run_claimed()
        self.make_job(status=RUNNING, heartbeat_at=timezone.now())
        self.make_job(status=SUCCEEDED)
        self.assertIsNone(jobs.run_next_queued())
        self.assertEqual(self.ran, [])

    def test_a_claim_lost_to_another_process_moves_on_to_the_next_job(self):
        self.patch_run_claimed()
        first, second, third = self.three_jobs_out_of_order()
        real_claim = jobs.claim

        def competitor_wins_the_first_one(job_id):
            if job_id == first.pk:            # otro proceso lo reclama justo antes (UPDATE atómico)
                PlanGenerationJob.objects.filter(pk=job_id).update(status=RUNNING, heartbeat_at=timezone.now())
            return real_claim(job_id)

        with mock.patch.object(jobs, 'claim', side_effect=competitor_wins_the_first_one):
            ran = jobs.run_next_queued()
        self.assertEqual(ran, second.pk)
        self.assertEqual(self.ran, [second.pk])             # este proceso NUNCA corre el que ganó el otro
        first.refresh_from_db()
        self.assertEqual(first.status, RUNNING)

    def test_returns_none_when_every_candidate_was_taken(self):
        self.patch_run_claimed()
        self.make_job()
        with mock.patch.object(jobs, 'claim', return_value=False):
            self.assertIsNone(jobs.run_next_queued())
        self.assertEqual(self.ran, [])

    def test_two_loops_taking_turns_run_each_job_exactly_once(self):
        self.patch_run_claimed()
        created = self.three_jobs_out_of_order()
        counts = [worker.run_loop(max_jobs=1, sleep=self.sleep), worker.run_loop(max_jobs=2, sleep=self.sleep)]
        self.assertEqual(counts, [1, 2])
        self.assertEqual(sorted(self.ran), sorted(job.pk for job in created))
        self.assertEqual(len(self.ran), len(set(self.ran)))


class RunLoopTests(WorkerTestCase):
    def test_processes_in_creation_order_and_stops_at_max_jobs(self):
        self.patch_run_claimed()
        oldest, middle, newest = self.three_jobs_out_of_order()
        processed = worker.run_loop(max_jobs=2, interval=5, sleep=self.sleep)
        self.assertEqual(processed, 2)
        self.assertEqual(self.ran, [oldest.pk, middle.pk])
        newest.refresh_from_db()
        self.assertEqual(newest.status, QUEUED)             # el tercero queda para otra vuelta
        self.assertEqual(self.slept, [])                    # con trabajo no se duerme
        self.assertEqual(self.worker_close_old_connections.call_count, 2)    # una vez por vuelta
        self.worker_connection.close.assert_called_once_with()               # y al terminar

    def test_without_max_jobs_it_drains_the_queue_and_then_sleeps_the_interval_in_slices(self):
        self.patch_run_claimed()
        self.three_jobs_out_of_order()
        stop = worker.StopRequest()

        def sleep(seconds):
            self.sleep(seconds)
            if len(self.slept) == 4:
                stop.request('test')
        processed = worker.run_loop(interval=3, stop=stop, sleep=sleep)
        self.assertEqual(processed, 3)
        self.assertEqual(self.slept, [1.0] * 4)                     # espera 3 s en tramos de 1 s y vuelve a preguntar
        self.assertEqual(self.worker_close_old_connections.call_count, 5)    # 3 con trabajo + 2 vueltas vacías

    def test_stop_request_wait_is_split_in_slices_and_returns_as_soon_as_it_is_asked_to_stop(self):
        stop = worker.StopRequest()
        stop.wait(2.5, self.sleep)
        self.assertEqual(self.slept, [1.0, 1.0, 0.5])
        self.slept.clear()
        stop.request('X')
        stop.wait(30, self.sleep)
        self.assertEqual(self.slept, [])
        stop.request('Y')                                   # la primera razón se conserva
        self.assertEqual(stop.reason, 'X')

    def test_recovery_runs_at_start_and_then_every_minute(self):
        class Clock:
            now = 0.0
        stop = worker.StopRequest()

        def sleep(seconds):
            self.sleep(seconds)
            Clock.now += seconds
            if Clock.now >= 70:
                stop.request('test')
        with mock.patch.object(jobs, 'recover_stale_jobs', return_value=[]) as recover:
            worker.run_loop(interval=30, stop=stop, sleep=sleep, clock=lambda: Clock.now)
        self.assertEqual(recover.call_count, 2)             # t=0 y t=60
        recover.assert_called_with(start=False)             # nunca lanza hilos desde el trabajador

    def test_orphans_are_recovered_and_run_by_the_loop(self):
        self.ready(self.client_a)
        stale = self.make_job(status=RUNNING, attempts=1, heartbeat_at=timezone.now() - timedelta(minutes=5))
        with self.settings(AI_PLANS_RUN_INLINE=False, AI_PLANS_WORKER=True), self.fake_ai() as fake:
            processed = worker.run_loop(max_jobs=1, sleep=self.sleep)
        stale.refresh_from_db()
        self.assertEqual(processed, 1)
        self.assertEqual((stale.status, stale.attempts), (SUCCEEDED, 2), stale.error_code)
        self.assertEqual(len(fake.calls), 1)

    def test_orphan_without_attempts_left_fails_instead_of_looping_forever(self):
        stale = self.make_job(status=RUNNING, attempts=3, max_attempts=3,
                              heartbeat_at=timezone.now() - timedelta(minutes=5))
        stop = worker.StopRequest()
        worker.run_loop(stop=stop, sleep=lambda seconds: (self.sleep(seconds), stop.request('test')))
        stale.refresh_from_db()
        self.assertEqual((stale.status, stale.error_code), (PlanGenerationJob.STATUS_FAILED, 'worker_lost'))

    def test_unexpected_errors_do_not_kill_the_loop_and_never_log_the_message(self):
        stop = worker.StopRequest()
        slept_at_each_call = []

        def flaky():
            slept_at_each_call.append(len(self.slept))
            if len(slept_at_each_call) == 1:
                raise OperationalError('could not connect to server host-secreto.internal password=clave-bd')
            if len(slept_at_each_call) == 2:
                raise RuntimeError('SECRETO-DE-LA-FICHA')
            return None

        def sleep(seconds):
            self.sleep(seconds)
            if len(self.slept) >= 40:
                stop.request('test')
        with mock.patch.object(jobs, 'run_next_queued', side_effect=flaky), \
                self.assertLogs('apps.plans', level='ERROR') as logs:
            worker.run_loop(interval=5, stop=stop, sleep=sleep)
        text = '\n'.join(logs.output)
        self.assertIn('OperationalError', text)
        self.assertIn('RuntimeError', text)
        for secret in ('host-secreto', 'clave-bd', 'SECRETO-DE-LA-FICHA'):
            self.assertNotIn(secret, text)
        # Espera creciente tras cada error seguido (10 s y luego 20 s), la normal (5 s) cuando se recupera
        self.assertEqual(slept_at_each_call, [0, 10, 30, 35])
        self.assertGreaterEqual(self.worker_connection.close.call_count, 3)      # 2 errores + al terminar

    def test_logs_only_ids_counts_and_codes(self):
        self.ready(self.client_a, other_condition_text='Algo privado')
        job = self.make_job()
        with self.assertLogs('apps.plans', level='INFO') as logs, self.fake_ai():
            worker.run_loop(max_jobs=1, sleep=self.sleep)
        text = '\n'.join(logs.output)
        self.assertIn(f'plan worker ran job_id={job.pk}', text)
        self.assertIn('plan worker stopped processed=1 reason=max_jobs', text)
        self.assertIn('status=succeeded', text)
        for secret in ('Algo privado', self.client_a.username, self.client_a.email):
            self.assertNotIn(secret, text)


class SignalTests(WorkerTestCase):
    def test_sigterm_and_sigint_stop_an_idle_loop_cleanly(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(signal=signum.name):
                self.slept.clear()
                stop = worker.StopRequest()
                before = signal.getsignal(signum)

                def sleep(seconds):
                    self.sleep(seconds)
                    if len(self.slept) == 2:
                        signal.raise_signal(signum)         # Railway envía SIGTERM al redesplegar
                with worker.stop_on_signals(stop):
                    processed = worker.run_loop(interval=30, stop=stop, sleep=sleep)
                self.assertEqual(processed, 0)
                self.assertEqual(stop.reason, signum.name)
                self.assertEqual(self.slept, [1.0, 1.0])     # notó la señal en el tramo siguiente, no a los 30 s
                self.assertIs(signal.getsignal(signum), before)    # los manejadores se restauran al salir

    def test_a_signal_during_a_job_lets_it_finish_and_does_not_claim_the_next_one(self):
        first, second, third = self.three_jobs_out_of_order()
        stop = worker.StopRequest()

        def run_and_get_terminated(job_id):
            self.fake_run_claimed(job_id)
            if len(self.ran) == 1:
                signal.raise_signal(signal.SIGTERM)          # llega con el trabajo en curso
        with mock.patch.object(jobs, '_run_claimed', side_effect=run_and_get_terminated), \
                worker.stop_on_signals(stop):
            processed = worker.run_loop(interval=5, stop=stop, sleep=self.sleep)
        self.assertEqual(processed, 1)
        self.assertEqual(self.ran, [first.pk])
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, second.status), (SUCCEEDED, QUEUED))     # el actual terminó; el siguiente, no
        self.assertEqual(self.slept, [])

    def test_a_second_signal_forces_the_stop(self):
        stop = worker.StopRequest()
        with worker.stop_on_signals(stop):
            signal.raise_signal(signal.SIGTERM)
            self.assertTrue(stop.requested)
            with self.assertRaises(KeyboardInterrupt):
                signal.raise_signal(signal.SIGTERM)

    def test_the_handler_only_writes_the_flag(self):
        stop = worker.StopRequest()
        with worker.stop_on_signals(stop), self.assertNoLogs('apps.plans'):
            signal.raise_signal(signal.SIGTERM)
        self.assertEqual((stop.requested, stop.reason), (True, 'SIGTERM'))


# ─── El comando ───────────────────────────────────────────────────────────────────────────────

class CommandTests(WorkerTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(command_module, 'connection')
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_command(self, *args):
        out = StringIO()
        call_command('process_plan_jobs', *args, stdout=out)
        return out.getvalue()

    def test_without_loop_it_behaves_as_before(self):
        self.ready(self.client_a)
        forgotten = self.make_job(created_at=timezone.now() - timedelta(minutes=5))
        fresh = self.make_job(client=self.client_b)                 # menos de 1 minuto: no es un olvidado
        with self.fake_ai():
            out = self.run_command()
        forgotten.refresh_from_db()
        fresh.refresh_from_db()
        self.assertIn('Trabajos procesados: 1.', out)
        self.assertEqual((forgotten.status, fresh.status), (SUCCEEDED, QUEUED))

    def test_loop_runs_a_job_and_stops_at_max_jobs(self):
        self.ready(self.client_a)
        job = self.make_job()
        with self.fake_ai():
            out = self.run_command('--loop', '--max-jobs', '1', '--interval', '1')
        job.refresh_from_db()
        self.assertEqual(job.status, SUCCEEDED, job.error_code)
        self.assertIn('Trabajador de planes con IA en marcha', out)
        self.assertIn('Trabajos procesados: 1.', out)

    def test_loop_stops_cleanly_with_a_real_signal(self):
        def sleep_then_terminate(seconds):
            self.sleep(seconds)
            signal.raise_signal(signal.SIGTERM)
        with mock.patch.object(worker, '_sleep', sleep_then_terminate):
            out = self.run_command('--loop', '--interval', '30')
        self.assertIn('Trabajador detenido (SIGTERM). Trabajos procesados: 0.', out)

    def test_a_forced_stop_exits_with_130(self):
        with mock.patch.object(worker, 'run_loop', side_effect=KeyboardInterrupt):
            with self.assertRaises(CommandError) as ctx:
                self.run_command('--loop')
        self.assertEqual(ctx.exception.returncode, 130)

    def test_invalid_options_are_rejected_before_doing_anything(self):
        for args, text in ((('--interval', '3'), 'solo se usan junto con --loop'),
                           (('--max-jobs', '2'), 'solo se usan junto con --loop'),
                           (('--loop', '--interval', '0'), '1 segundo o más'),
                           (('--loop', '--max-jobs', '0'), '1 o más')):
            with self.subTest(args=args), mock.patch.object(worker, 'run_loop') as run_loop:
                with self.assertRaisesMessage(CommandError, text):
                    self.run_command(*args)
                run_loop.assert_not_called()
