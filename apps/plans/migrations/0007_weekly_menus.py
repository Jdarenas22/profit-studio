from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('plans', '0006_gemini_daily_usage')]
    operations = [
        migrations.AddField(model_name='nutritionplan', name='duration_days',
                            field=models.PositiveSmallIntegerField(default=1, verbose_name='Días de menú distintos')),
        migrations.AddField(model_name='mealslot', name='day_index',
                            field=models.PositiveSmallIntegerField(default=1, verbose_name='Día del menú')),
        migrations.AlterModelOptions(name='mealslot', options={'ordering': ['day_index', 'order']}),
        migrations.RemoveConstraint(model_name='mealslot', name='plans_slot_unique_code'),
        migrations.AddConstraint(model_name='mealslot', constraint=models.UniqueConstraint(
            fields=('nutrition_plan', 'day_index', 'code'), name='plans_slot_unique_day_code')),
        migrations.AddConstraint(model_name='mealslot', constraint=models.CheckConstraint(
            condition=models.Q(day_index__gte=1, day_index__lte=7), name='plans_slot_day_range')),
        migrations.AddConstraint(model_name='nutritionplan', constraint=models.CheckConstraint(
            condition=models.Q(duration_days__in=(1, 7)), name='plans_nutrition_duration')),
    ]
