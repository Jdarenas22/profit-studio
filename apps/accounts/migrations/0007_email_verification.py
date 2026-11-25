"""Verificación del correo (docs/contracts/verificacion-correo.md).

1. Agrega `User.email_verified_at` (nulo = todavía no verificó).
2. Marca como verificadas TODAS las cuentas que ya existen (con su fecha de alta): nadie queda
   bloqueado ni pendiente por el cambio. Es un solo UPDATE, sin recorrer filas ni tocar contraseñas,
   y se puede repetir sin efecto (solo toca las filas con la fecha vacía).
"""
from django.db import migrations, models
from django.db.models import F


def mark_existing_accounts_verified(apps, schema_editor):
    User = apps.get_model('accounts', 'User')
    User.objects.filter(email_verified_at__isnull=True).update(email_verified_at=F('date_joined'))


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_transfer_admin_action'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='email_verified_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Correo verificado el'),
        ),
        # Al revertir, la columna se elimina sola: no hay nada que deshacer en los datos.
        migrations.RunPython(mark_existing_accounts_verified, migrations.RunPython.noop),
    ]
