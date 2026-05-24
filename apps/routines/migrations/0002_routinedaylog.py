from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('routines', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RoutineDayLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('completed_at', models.DateField(auto_now_add=True)),
                ('notes', models.CharField(blank=True, max_length=200, verbose_name='Nota rápida')),
                ('routine_day', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='logs', to='routines.routineday', verbose_name='Día de rutina')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='day_logs', to=settings.AUTH_USER_MODEL, verbose_name='Cliente')),
            ],
            options={
                'verbose_name': 'Registro de entrenamiento',
                'verbose_name_plural': 'Registros de entrenamiento',
                'ordering': ['-completed_at'],
            },
        ),
    ]
