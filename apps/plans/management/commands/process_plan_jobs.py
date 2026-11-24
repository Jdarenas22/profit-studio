"""Recupera y ejecuta los trabajos de generación de planes con IA.

Sin opciones (como siempre): recupera los trabajos huérfanos (tras un reinicio o redeploy) y los `queued`
olvidados hace más de 1 minuto, los corre en este mismo proceso y termina. Normalmente no hace falta: la
recuperación también corre al consultar un estado y al crear un trabajo. Sirve para un cron de Railway.

    python manage.py process_plan_jobs

Con `--loop`: servicio TRABAJADOR. Corre sin terminar, toma los trabajos `queued` por orden de creación,
recupera huérfanos cada minuto y duerme `--interval` segundos cuando no hay nada. Se detiene limpio con
SIGTERM o SIGINT (termina el trabajo en curso y sale; una segunda señal lo corta). Pensado para
`AI_PLANS_WORKER=True`; consulta la base cada pocos segundos, así que mantiene despierto a Postgres
(docs/DEPLOY.md, sección 12).

    python manage.py process_plan_jobs --loop [--interval 5] [--max-jobs 20]
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.plans import jobs, worker


class Command(BaseCommand):
    help = ('Recupera trabajos de planes con IA huérfanos y los ejecuta en este proceso. '
            'Con --loop queda corriendo como servicio trabajador.')

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true', default=False,
                            help='Servicio trabajador: no termina hasta recibir SIGTERM o SIGINT.')
        parser.add_argument('--interval', type=int, default=None,
                            help=f'Segundos de espera cuando no hay trabajo (solo con --loop; por defecto '
                                 f'{worker.DEFAULT_INTERVAL}).')
        parser.add_argument('--max-jobs', type=int, default=None, dest='max_jobs',
                            help='Termina después de procesar este número de trabajos (solo con --loop).')

    def handle(self, *args, **options):
        if not options['loop']:
            if options['interval'] is not None or options['max_jobs'] is not None:
                raise CommandError('--interval y --max-jobs solo se usan junto con --loop.')
            relaunch = jobs.recover_stale_jobs(start=False)
            for job_id in relaunch:
                jobs.run_job(job_id)
            self.stdout.write(f'Trabajos procesados: {len(relaunch)}.')
            return

        interval = worker.DEFAULT_INTERVAL if options['interval'] is None else options['interval']
        max_jobs = options['max_jobs']
        if interval < 1:
            raise CommandError('--interval debe ser de 1 segundo o más.')
        if max_jobs is not None and max_jobs < 1:
            raise CommandError('--max-jobs debe ser 1 o más.')

        stop = worker.StopRequest()
        self.stdout.write(f'Trabajador de planes con IA en marcha (base: {connection.vendor}; espera {interval} s '
                          'sin trabajo). Para detenerlo: SIGTERM o Ctrl+C.')
        try:
            with worker.stop_on_signals(stop):
                processed = worker.run_loop(interval=interval, max_jobs=max_jobs, stop=stop)
        except KeyboardInterrupt:
            # Segunda señal: el trabajo en curso queda `running` y se recupera solo cuando vence su latido.
            raise CommandError('Parada forzada: el trabajo en curso se recuperará solo.', returncode=130) from None
        finally:
            connection.close()
        self.stdout.write(f'Trabajador detenido ({stop.reason or "max-jobs"}). Trabajos procesados: {processed}.')
