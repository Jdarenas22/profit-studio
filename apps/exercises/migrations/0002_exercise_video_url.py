from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('exercises', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='exercise',
            name='video_url',
            field=models.URLField(
                blank=True,
                default='',
                help_text='Pega el enlace de YouTube. Ej: https://youtu.be/abc123',
                verbose_name='URL de video (YouTube)',
            ),
        ),
    ]
