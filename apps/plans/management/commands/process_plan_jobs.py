"""Recupera y ejecuta los trabajos de generación pendientes (huérfanos tras un reinicio o redeploy).

Normalmente no hace falta: la recuperación también corre al consultar un estado y al crear un
trabajo. Sirve para programarlo con un cron de Railway si algún día se quiere.

    python manage.py process_plan_jobs
"""
from django.core.management.base import BaseCommand

from apps.plans import jobs


class Command(BaseCommand):
    help = 'Recupera trabajos de planes con IA huérfanos y los ejecuta (en este mismo proceso).'

    def handle(self, *args, **options):
        relaunch = jobs.recover_stale_jobs(start=False)
        for job_id in relaunch:
            jobs.run_job(job_id)
        self.stdout.write(f'Trabajos procesados: {len(relaunch)}.')
