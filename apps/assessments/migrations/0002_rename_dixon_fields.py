from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assessments', '0001_initial'),
    ]

    operations = [
        # Renombrar p1→p0, p2→p1, p3→p2 (orden importa: no hay colisión)
        migrations.RenameField('dixontest', 'p1', 'p0'),
        migrations.RenameField('dixontest', 'p2', 'p1'),
        migrations.RenameField('dixontest', 'p3', 'p2'),
        # Actualizar verbose_names
        migrations.AlterField(
            model_name='dixontest',
            name='p0',
            field=models.PositiveIntegerField(verbose_name='Pulso inicial en reposo (P0)'),
        ),
        migrations.AlterField(
            model_name='dixontest',
            name='p1',
            field=models.PositiveIntegerField(verbose_name='Pulso post-actividad física (P1)'),
        ),
        migrations.AlterField(
            model_name='dixontest',
            name='p2',
            field=models.PositiveIntegerField(verbose_name='Pulso 1 min después (P2)'),
        ),
        # Ampliar max_digits del índice para permitir valores negativos con decimales
        migrations.AlterField(
            model_name='dixontest',
            name='index_value',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=6,
                null=True, verbose_name='IRD',
            ),
        ),
    ]
