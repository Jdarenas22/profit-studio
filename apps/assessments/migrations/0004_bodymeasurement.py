from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('assessments', '0003_alter_dixontest_options'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BodyMeasurement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField(auto_now_add=True)),
                ('weight', models.DecimalField(decimal_places=1, max_digits=5, verbose_name='Peso (kg)')),
                ('height', models.DecimalField(blank=True, decimal_places=2, max_digits=4, null=True, verbose_name='Estatura (m)')),
                ('waist_cm', models.DecimalField(blank=True, decimal_places=1, max_digits=5, null=True, verbose_name='Cintura (cm)')),
                ('imc', models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True, verbose_name='IMC')),
                ('imc_classification', models.CharField(blank=True, max_length=50)),
                ('notes', models.TextField(blank=True, verbose_name='Observaciones')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='measurements', to=settings.AUTH_USER_MODEL, verbose_name='Cliente')),
                ('trainer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='recorded_measurements', to=settings.AUTH_USER_MODEL, verbose_name='Registrado por')),
            ],
            options={
                'verbose_name': 'Medición corporal',
                'verbose_name_plural': 'Mediciones corporales',
                'ordering': ['-date', '-pk'],
            },
        ),
    ]
