from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('memberships', '0001_initial'),
        ('payments', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ManualPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.PositiveIntegerField(verbose_name='Monto (COP)')),
                ('method', models.CharField(
                    choices=[
                        ('cash', 'Efectivo'), ('transfer', 'Transferencia'),
                        ('nequi', 'Nequi'), ('daviplata', 'Daviplata'), ('other', 'Otro'),
                    ],
                    default='cash', max_length=20, verbose_name='Método de pago',
                )),
                ('payment_date', models.DateField(verbose_name='Fecha de pago')),
                ('receipt', models.ImageField(blank=True, null=True, upload_to='receipts/', verbose_name='Comprobante (foto)')),
                ('notes', models.TextField(blank=True, verbose_name='Notas')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='manual_payments', to=settings.AUTH_USER_MODEL, verbose_name='Cliente')),
                ('trainer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='registered_payments', to=settings.AUTH_USER_MODEL, verbose_name='Registrado por')),
                ('plan', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='manual_payments', to='memberships.membershipplan', verbose_name='Plan')),
            ],
            options={
                'verbose_name': 'Pago manual',
                'verbose_name_plural': 'Pagos manuales',
                'ordering': ['-payment_date', '-created_at'],
            },
        ),
    ]
