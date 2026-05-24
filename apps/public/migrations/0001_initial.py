from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='Testimonial',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, verbose_name='Nombre')),
                ('role', models.CharField(
                    blank=True, max_length=150,
                    verbose_name='Logro / Descripción',
                    help_text='Ej: "Perdió 10 kg en 3 meses" o "Cliente desde 2023"',
                )),
                ('text', models.TextField(verbose_name='Testimonio')),
                ('photo', models.ImageField(blank=True, null=True, upload_to='testimonials/', verbose_name='Foto (opcional)')),
                ('rating', models.PositiveSmallIntegerField(default=5, verbose_name='Estrellas (1–5)')),
                ('is_active', models.BooleanField(default=True, verbose_name='Mostrar en la web')),
                ('order', models.PositiveIntegerField(default=0, verbose_name='Orden de aparición')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Testimonio',
                'verbose_name_plural': 'Testimonios',
                'ordering': ['order', '-created_at'],
            },
        ),
    ]
