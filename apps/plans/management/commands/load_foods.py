"""Carga o actualiza el catálogo inicial de alimentos (`apps/plans/foods_data.py`).

Nunca pisa un alimento ya validado (`validated_by` lleno): si un profesional corrigió o validó las
cifras, esas cifras mandan. Las cifras iniciales son aproximadas y NO están validadas.

    python manage.py load_foods
"""
from django.core.management.base import BaseCommand

from apps.plans.foods_data import food_rows
from apps.plans.models import Food


class Command(BaseCommand):
    help = 'Carga el catálogo inicial de alimentos (cifras aproximadas, sin validar).'

    def handle(self, *args, **options):
        created = updated = kept = 0
        for row in food_rows():
            name = row.pop('name')
            food = Food.objects.filter(name=name).first()
            if food is None:
                Food.objects.create(name=name, **row)
                created += 1
            elif food.validated_by:
                kept += 1
            else:
                for field, value in row.items():
                    setattr(food, field, value)
                food.save()
                updated += 1
        unvalidated = Food.objects.filter(is_active=True, validated_by='').count()
        self.stdout.write(self.style.SUCCESS(
            f'Alimentos: {created} nuevos, {updated} actualizados, {kept} ya validados (sin tocar).'))
        if unvalidated:
            self.stdout.write(self.style.WARNING(
                f'{unvalidated} alimentos activos siguen SIN VALIDAR: fuera de DEBUG no se usan hasta que un '
                'nutricionista los valide (admin > Alimentos > "Marcar como validados").'))
