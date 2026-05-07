from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_user_interested_plan'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='gender',
            field=models.CharField(
                blank=True,
                choices=[('F', 'Femenino'), ('M', 'Masculino'), ('O', 'Prefiero no decir')],
                default='',
                max_length=1,
                verbose_name='Género',
            ),
        ),
    ]
