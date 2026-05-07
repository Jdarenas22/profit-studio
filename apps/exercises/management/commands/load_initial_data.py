from django.core.management.base import BaseCommand
from apps.exercises.models import ExerciseCategory
from apps.memberships.models import MembershipPlan


CATEGORIES = [
    ('Calentamiento', 0),
    ('Pecho', 1),
    ('Espalda', 2),
    ('Hombros', 3),
    ('Bíceps', 4),
    ('Tríceps', 5),
    ('Abdominales', 6),
    ('Piernas', 7),
    ('Glúteos', 8),
    ('Cardio', 9),
    ('Estiramiento', 10),
    ('Cuerpo completo', 11),
]

PLANS = [
    {
        'name': 'Mensual',
        'duration_days': 30,
        'reference_price': 120000,
        'description': 'Ideal para empezar. Incluye valoración inicial, rutina personalizada y videos.',
    },
    {
        'name': 'Trimestral',
        'duration_days': 90,
        'reference_price': 300000,
        'description': 'El más elegido. Tiempo suficiente para ver resultados reales con seguimiento continuo.',
    },
    {
        'name': 'Semestral',
        'duration_days': 180,
        'reference_price': 540000,
        'description': 'Para quienes se comprometen con su transformación. Mejor precio por día.',
    },
]


class Command(BaseCommand):
    help = 'Carga categorías de ejercicios y planes de membresía iniciales'

    def handle(self, *args, **options):
        created_cats = 0
        for name, order in CATEGORIES:
            _, created = ExerciseCategory.objects.get_or_create(
                name=name,
                defaults={'order': order},
            )
            if created:
                created_cats += 1

        self.stdout.write(f'Categorías: {created_cats} creadas, {len(CATEGORIES) - created_cats} ya existían')

        created_plans = 0
        for data in PLANS:
            _, created = MembershipPlan.objects.get_or_create(
                name=data['name'],
                defaults={
                    'duration_days': data['duration_days'],
                    'reference_price': data['reference_price'],
                    'description': data['description'],
                },
            )
            if created:
                created_plans += 1

        self.stdout.write(f'Planes: {created_plans} creados, {len(PLANS) - created_plans} ya existían')
        self.stdout.write(self.style.SUCCESS('Datos iniciales cargados correctamente.'))
