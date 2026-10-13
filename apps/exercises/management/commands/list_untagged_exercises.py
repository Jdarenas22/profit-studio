"""Lista los ejercicios activos que aún no tienen etiquetas para los planes automáticos.

Prerrequisito de la fase B (docs/contracts/plan-ia.md, sección 5): sin intensidad, equipo y zonas
de riesgo, el motor no puede garantizar las restricciones y trata el ejercicio con prudencia (no
lo propone a clientas con lesiones o advertencias, ni a quien entrena en casa).

Uso:
    python manage.py list_untagged_exercises
"""
from django.core.management.base import BaseCommand

from apps.exercises.models import Exercise


class Command(BaseCommand):
    help = 'Lista los ejercicios activos sin etiquetar (sin intensidad) para los planes con IA.'

    def handle(self, *args, **options):
        active = Exercise.objects.filter(is_active=True)
        untagged = active.filter(intensity='').select_related('category').order_by('name')
        total, missing = active.count(), untagged.count()
        if not missing:
            self.stdout.write(self.style.SUCCESS(f'Los {total} ejercicios activos están etiquetados.'))
            return
        self.stdout.write(f'{missing} de {total} ejercicios activos sin etiquetar:')
        for exercise in untagged:
            category = exercise.category.name if exercise.category else 'sin categoría'
            self.stdout.write(f'  - [{exercise.pk}] {exercise.name} ({category})')
        self.stdout.write('Etiquétalos en el panel: Ejercicios > Editar > "Etiquetas para planes automáticos".')
