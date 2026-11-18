"""Archivos huérfanos: fotos de perfil (`profiles/`) y comprobantes (`receipts/`) que ninguna fila
referencia (docs/contracts/cuentas-eliminar.md, sección 7.4).

Quedan cuando falla el borrado de una foto al eliminar una cuenta (R2 caído), cuando se cambia la
foto de perfil y cuando el borrado en cascada anterior se llevaba los pagos manuales.

Por defecto SOLO cuenta (no borra nada). Con `--delete` los borra. Nunca imprime nombres de
archivo: el de una foto puede llevar el nombre de la persona.

    python manage.py purge_orphan_files            # cuenta
    python manage.py purge_orphan_files --delete   # borra
"""
import logging
import posixpath

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand

from apps.payments.models import ManualPayment

logger = logging.getLogger(__name__)


def walk(storage, prefix):
    """Todos los archivos bajo `prefix` (recursivo). Una carpeta que no existe = ninguno."""
    try:
        dirs, files = storage.listdir(prefix)
    except (FileNotFoundError, NotADirectoryError):
        return []
    found = [posixpath.join(prefix, name) for name in files]
    for sub in dirs:
        found.extend(walk(storage, posixpath.join(prefix, sub)))
    return found


def _referenced(queryset, field_name):
    return set(queryset.exclude(**{field_name: ''}).exclude(**{f'{field_name}__isnull': True})
               .values_list(field_name, flat=True))


def orphans():
    """[(tipo, almacenamiento, nombre)] de los archivos sin fila que los use."""
    photos = _referenced(get_user_model().objects.all(), 'profile_photo')
    receipts = _referenced(ManualPayment.objects.all(), 'receipt')
    # El mismo almacenamiento que usa el campo (con R2, el bucket PRIVADO de comprobantes)
    receipts_storage = ManualPayment._meta.get_field('receipt').storage
    result = [('photo', default_storage, name) for name in walk(default_storage, 'profiles')
              if name not in photos]
    result += [('receipt', receipts_storage, name) for name in walk(receipts_storage, 'receipts')
               if name not in receipts]
    return result


class Command(BaseCommand):
    help = 'Cuenta (o borra con --delete) las fotos y comprobantes que ninguna fila referencia.'

    def add_arguments(self, parser):
        parser.add_argument('--delete', action='store_true', default=False,
                            help='Borrar los archivos huérfanos (sin esto, solo se cuentan).')

    def handle(self, *args, **options):
        found = orphans()
        counts = {'photo': 0, 'receipt': 0}
        for kind, _storage, _name in found:
            counts[kind] += 1
        self.stdout.write(f'Fotos de perfil huérfanas: {counts["photo"]}')
        self.stdout.write(f'Comprobantes huérfanos: {counts["receipt"]}')
        if not options['delete']:
            if found:
                self.stdout.write('Nada se borró. Usa --delete para borrarlos.')
            return
        deleted = failed = 0
        for kind, storage, name in found:
            try:
                storage.delete(name)
            except Exception:
                failed += 1
                logger.warning('purge_orphan_files delete_failed kind=%s', kind)
            else:
                deleted += 1
        self.stdout.write(self.style.SUCCESS(f'Borrados: {deleted}. Fallaron: {failed}.'))
