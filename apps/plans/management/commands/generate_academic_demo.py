from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.plans import services
from apps.plans.academic import MEMBERS, TRAINER, demo_environment


class Command(BaseCommand):
    help = 'Genera un borrador por el flujo normal, solo para una cuenta ficticia de la demo.'

    def add_arguments(self, parser):
        parser.add_argument('--username', choices=MEMBERS, required=True)
        parser.add_argument('--kind', choices=('exercise', 'nutrition'), default='nutrition')

    def handle(self, **options):
        if not demo_environment():
            raise CommandError('Solo disponible con config.settings.demo.')
        try:
            client = User.objects.get(username=options['username'])
            trainer = User.objects.get(username=TRAINER)
        except User.DoesNotExist:
            raise CommandError('Ejecuta primero seed_academic_demo.') from None
        job, _ = services.request_plan(client, options['kind'], trainer, services.new_request_token())
        job.refresh_from_db()
        self.stdout.write(f'Modo: {settings.AI_PLANS_MODE}; trabajo {job.pk}; estado: {job.status}; código: {job.error_code or "ninguno"}')
        if job.status != job.STATUS_SUCCEEDED:
            raise CommandError('No se creó un borrador válido. Revisar estado y cuota; no hay cambio automático de proveedor.')
        self.stdout.write(self.style.SUCCESS(f'Borrador {job.plan_id} listo para revisión en /plans/trainer/{job.plan_id}/.'))
