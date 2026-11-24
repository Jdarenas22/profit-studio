"""Cuenta los registros de cada modelo. Sirve para comparar una base con su copia de seguridad restaurada.

Uso:
    python manage.py count_records            # una línea por modelo: app.Modelo y cantidad
    python manage.py count_records --json     # lo mismo en JSON (para comparar con `diff`)

Es de SOLO LECTURA (únicamente `COUNT`) y NO imprime datos personales: solo el nombre del modelo y un
número. Tampoco imprime el host, el usuario ni la clave de la base: solo el motor (postgresql, sqlite).
La salida está ordenada, así que dos corridas sobre bases iguales dan texto idéntico:

    python manage.py count_records > antes.txt   (en una base)
    python manage.py count_records > despues.txt (en la restaurada)    ->   diff antes.txt despues.txt

Termina con código 1 si algún modelo no se pudo contar (por ejemplo, falta una migración).
Procedimiento completo: docs/BACKUPS.md.
"""
import json

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection
from django.db.migrations.recorder import MigrationRecorder


def model_label(model):
    return f'{model._meta.app_label}.{model._meta.object_name}'


def count_models():
    """({'app.Modelo': cantidad}, {'app.Modelo': clase del error}). Solo `COUNT(*)` por modelo."""
    counts, failures = {}, {}
    for model in sorted(apps.get_models(include_auto_created=True), key=model_label):
        label = model_label(model)
        try:
            counts[label] = model._base_manager.count()
        except DatabaseError as exc:     # nunca el mensaje: puede traer datos de la conexión
            failures[label] = type(exc).__name__
    return counts, failures


class Command(BaseCommand):
    help = ('Cuenta los registros de cada modelo (solo cantidades, sin datos personales) para verificar una '
            'restauración. Solo lectura.')

    def add_arguments(self, parser):
        parser.add_argument('--json', action='store_true', default=False, dest='as_json',
                            help='Imprime el resultado como JSON.')

    def handle(self, *args, **options):
        counts, failures = count_models()
        try:
            migrations = MigrationRecorder(connection).migration_qs.count()
        except DatabaseError as exc:
            migrations = None
            failures['django_migrations'] = type(exc).__name__
        total = sum(counts.values())
        if options['as_json']:
            self.stdout.write(json.dumps({
                'engine': connection.vendor, 'models': counts, 'total_rows': total,
                'applied_migrations': migrations, 'failed': failures,
            }, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            self.stdout.write(f'Motor: {connection.vendor}')
            width = max([len(label) for label in counts] + [len('Migraciones aplicadas')])
            for label, number in counts.items():
                self.stdout.write(f'{label:<{width}}  {number}')
            self.stdout.write(f'{"Migraciones aplicadas":<{width}}  {"?" if migrations is None else migrations}')
            self.stdout.write(f'{"Total de filas":<{width}}  {total}')
            for label, error in failures.items():
                self.stdout.write(self.style.ERROR(f'{label}: no se pudo contar ({error})'))
        if failures:
            raise CommandError(f'{len(failures)} modelo(s) no se pudieron contar.', returncode=1)
