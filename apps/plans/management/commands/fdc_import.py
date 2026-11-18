import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.plans.sources.fdc import FDCClient, FDCError
from apps.plans.sources.fdc_import import import_food, STATES
from apps.plans.sources.fdc_normalize import normalize_food


class Command(BaseCommand):
    help = 'Vista previa USDA; --apply guarda el alimento INACTIVO para revisión. No modifica planes previos.'

    def add_arguments(self, parser):
        parser.add_argument('--fdc-id', type=int, required=True)
        target = parser.add_mutually_exclusive_group(required=True)
        target.add_argument('--food-id', type=int)
        target.add_argument('--name')
        parser.add_argument('--group')
        parser.add_argument('--state', choices=STATES, required=True)
        parser.add_argument('--confirm-description', default='')
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        try:
            payload = FDCClient(settings.FDC_API_KEY, settings.FDC_TIMEOUT_SECONDS).food(options['fdc_id'])
            _values, snapshot, digest = normalize_food(payload, options['fdc_id'])
            food, _snapshot, changed = import_food(
                payload, food_id=options['food_id'], name=options['name'], group=options['group'],
                state=options['state'], confirm_description=options['confirm_description'], apply=options['apply'],
            )
        except FDCError as exc:
            raise CommandError(str(exc)) from None
        except ValidationError:
            raise CommandError('fdc_local_validation_failed') from None
        self.stdout.write(json.dumps({
            'operation': 'applied' if changed else 'unchanged' if options['apply'] else 'preview_only',
            'local_food_id': food.pk if food else None, 'local_name': food.name if food else options['name'],
            'preparation_state': options['state'], 'source_hash': digest, 'source': snapshot,
        }, ensure_ascii=False, indent=2))
        if changed:
            self.stdout.write('Guardado INACTIVO. Revisa identidad, restricciones y nutrientes en el administrador.')

