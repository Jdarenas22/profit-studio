import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.plans.sources.fdc import FDCClient, FDCError


class Command(BaseCommand):
    help = 'Busca hasta 10 alimentos USDA. Solo consulta, no cambia el catálogo.'

    def add_arguments(self, parser):
        parser.add_argument('--query', required=True)

    def handle(self, *args, **options):
        try:
            rows = FDCClient(settings.FDC_API_KEY, settings.FDC_TIMEOUT_SECONDS).search(options['query'])
        except FDCError as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(json.dumps(rows, ensure_ascii=False, indent=2))

