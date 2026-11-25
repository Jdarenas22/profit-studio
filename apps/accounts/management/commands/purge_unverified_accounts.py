"""Cuenta o borra las cuentas registradas que nunca confirmaron su correo.

    python manage.py purge_unverified_accounts                # solo cuenta (no borra nada)
    python manage.py purge_unverified_accounts --days 14      # con otra antigüedad mínima
    python manage.py purge_unverified_accounts --delete       # borra de verdad

Qué se considera una cuenta sin confirmar (docs/contracts/verificacion-correo.md, sección 9): una clienta
que se registró sola (`role='member'`), sigue INACTIVA, sin `email_verified_at`, nunca fue desactivada ni
eliminada, no es del equipo y se creó hace más de `--days` días (7 por defecto; el enlace del correo
vence a las 24 horas, así que esas cuentas ya no se pueden activar).

El borrado es REAL (no deja lápida) porque la cuenta nunca estuvo activa: no tiene pagos, membresía,
fichas, consentimientos ni nada. Aun así, como defensa, NO se borra una cuenta si CUALQUIER fila de la base
la referencia (pagos, auditoría, entradas del admin, lo que sea): esa se cuenta como «con datos» y se deja
para revisarla a mano. También se borran los intentos de inicio de sesión (django-axes) con su usuario.

Es seguro repetirlo. No imprime nombres, correos ni usuarios: solo cantidades. En Railway se puede
programar como un servicio con Cron Schedule (por ejemplo, una vez por semana); no se programa solo.
"""
from datetime import timedelta

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts import services
from apps.accounts.models import User, pending_registration_q

DEFAULT_DAYS = 7


def _user_reference_fields():
    """`(modelo, nombre_del_campo)` de cada clave foránea (o uno a uno) concreta hacia `User`, incluidas
    las ocultas (`related_name='+'`)."""
    found = []
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if (getattr(field, 'concrete', False) and field.is_relation
                    and (field.many_to_one or field.one_to_one) and field.related_model is User):
                found.append((model, field.name))
    return found


def has_related_rows(user, fields=None):
    """¿Alguna fila de la base apunta a esta cuenta? (si sí, nunca se borra)."""
    for model, name in (fields if fields is not None else _user_reference_fields()):
        if model._base_manager.filter(**{name: user.pk}).exists():
            return True
    return False


def unverified_queryset(days, now=None):
    cutoff = (now or timezone.now()) - timedelta(days=days)
    return User.objects.filter(
        pending_registration_q(), role=User.ROLE_MEMBER, is_staff=False, is_superuser=False,
        date_joined__lt=cutoff,
    )


class Command(BaseCommand):
    help = ('Cuenta (o, con --delete, borra) las cuentas registradas que nunca confirmaron su correo y '
            'no tienen ningún dato.')

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=DEFAULT_DAYS,
                            help=f'Antigüedad mínima en días (por defecto {DEFAULT_DAYS}; mínimo 1).')
        parser.add_argument('--delete', action='store_true', default=False,
                            help='Borra de verdad. Sin esta opción solo cuenta.')

    def handle(self, *args, **options):
        days = options['days']
        if days < 1:
            raise CommandError('--days debe ser 1 o más (el enlace del correo vence a las 24 horas).')
        now = timezone.now()
        fields = _user_reference_fields()
        pks = list(unverified_queryset(days, now).order_by('pk').values_list('pk', flat=True))
        deletable = [pk for pk in pks
                     if not has_related_rows(User(pk=pk), fields)]
        with_data = len(pks) - len(deletable)

        self.stdout.write(f'Cuentas sin confirmar hace más de {days} días: {len(pks)}.')
        self.stdout.write(f'  Sin ningún dato (se pueden borrar): {len(deletable)}.')
        self.stdout.write(f'  Con alguna relación en la base (no se tocan, revisar a mano): {with_data}.')

        if not options['delete']:
            self.stdout.write('No se borró nada. Para borrar las que no tienen datos, agrega --delete.')
            return

        deleted = skipped = 0
        for pk in deletable:
            with transaction.atomic():
                locked = unverified_queryset(days, now).select_for_update().filter(pk=pk).first()
                # Se vuelve a comprobar con la fila bloqueada: pudo confirmar su correo o recibir datos
                if locked is None or has_related_rows(locked, fields):
                    skipped += 1
                    continue
                username = locked.username
                locked.delete()
                services._purge_axes(username)
            deleted += 1
        self.stdout.write(self.style.SUCCESS(f'Borradas: {deleted}. Omitidas por cambiar mientras tanto: {skipped}.'))
