"""Crea exclusivamente datos ficticios en la base académica aislada."""
from datetime import timedelta
import secrets

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.assessments.models import BodyMeasurement
from apps.exercises.models import Exercise
from apps.health import services as health
from apps.health.forms import HealthProfileForm
from apps.health.models import ConsentTextVersion, PURPOSE_AI, PURPOSE_HEALTH_DATA
from apps.memberships.models import Membership, MembershipPlan
from apps.plans.academic import DEMO_USERS, MEMBERS, TRAINER, demo_environment
from apps.routines.models import Routine, RoutineDay, RoutineExercise


class Command(BaseCommand):
    help = 'Prepara la muestra académica aislada, sin red ni datos reales.'

    def handle(self, **options):
        if not demo_environment():
            raise CommandError('Usa --settings=config.settings.demo; no se modifica la base normal.')
        if User.objects.exclude(username__in=DEMO_USERS).exists():
            raise CommandError('La base contiene otras cuentas. Se cancela sin cambiar datos.')
        access = settings.BASE_DIR / '.demo/access.local.txt'
        if User.objects.exists():
            if User.objects.count() == 4 and access.exists():
                self.stdout.write('Demo ya preparada; se conservan cuentas, claves y planes.')
                return
            raise CommandError('Preparación parcial: revisar manualmente, sin borrar ni sobrescribir.')
        credentials = []
        with transaction.atomic():
            for purpose in (PURPOSE_HEALTH_DATA, PURPOSE_AI):
                previous = ConsentTextVersion.objects.filter(purpose=purpose).order_by('-version').first()
                ConsentTextVersion.objects.create(
                    purpose=purpose, version=(previous.version + 1 if previous else 1), is_current=True,
                    body='Demostración universitaria. Se usan únicamente perfiles ficticios y datos sintéticos. '
                         'No ingresar datos personales reales. Los menús son sugerencias alimenticias de muestra.')
            def account(username, role, **extra):
                password = secrets.token_urlsafe(18)
                user = User.objects.create_user(username=username, password=password, role=role,
                    email=f'{username}@example.invalid', first_name='Demo', last_name=username.split('_')[-1], **extra)
                credentials.append(f'{username}: {password}')
                return user
            trainer = account(TRAINER, User.ROLE_TRAINER, is_staff=True)
            membership = MembershipPlan.objects.create(name='Muestra universitaria', duration_days=90, reference_price=0)
            names = ['Prensa de piernas', 'Remo en máquina', 'Jalón al pecho', 'Press de pecho en máquina',
                     'Curl de bíceps', 'Extensión de tríceps', 'Plancha', 'Puente de glúteo',
                     'Elevaciones laterales', 'Abducción de cadera']
            exercises = [Exercise.objects.create(name=name, level='beginner', description='Ejercicio de muestra',
                muscles='General', intensity='moderate', equipment=['machines'], risk_tags=[]) for name in names]
            today = timezone.localdate()
            for username, goal, weight, height, days, activity in zip(
                    MEMBERS, ('weight_loss', 'muscle_gain', 'cardio'), (78, 72, 65), ('1.68', '1.78', '1.70'),
                    (3, 4, 3), ('light', 'moderate', 'light')):
                client = account(username, User.ROLE_MEMBER, assigned_trainer=trainer, training_goal=goal)
                Membership.objects.create(user=client, plan=membership, start_date=today,
                    end_date=today + timedelta(days=90), activated_by=trainer)
                BodyMeasurement.objects.create(user=client, trainer=trainer, weight=weight, height=height)
                for purpose in (PURPOSE_HEALTH_DATA, PURPOSE_AI):
                    health.grant_consent(client, purpose, health.current_text(purpose).version)
                post = {
                    'birth_date': today.replace(year=today.year - 28, day=1).isoformat(),
                    'sex_for_calculation': 'M', 'training_goal': goal, 'health_screen': 'no', 'food_screen': 'no',
                    'training_days_per_week': str(days), 'session_minutes': '60', 'training_place': 'gym',
                    'experience_level': 'beginner', 'activity_level': activity, 'equipment': ['machines'],
                    'diet_type': 'omnivore', 'cooking_access': 'basic', 'budget_level': 'medium',
                    'meal_slots': ['breakfast', 'mid_morning', 'lunch', 'snack', 'dinner'],
                }
                form = HealthProfileForm(post, known_height=height)
                if not form.is_valid():
                    raise CommandError('Ficha ficticia inválida: ' + form.errors.as_json())
                health.save_profile(client, form.cleaned_data, actor=client)
                routine = Routine.objects.create(user=client, trainer=trainer, name=f'Rutina académica {days} días')
                for number in range(days):
                    day = RoutineDay.objects.create(routine=routine, name=f'Día {number + 1}', order=number)
                    for order, exercise in enumerate(exercises[:5]):
                        RoutineExercise.objects.create(day=day, exercise=exercise, sets=3, reps='10', rest_seconds=60, order=order)
            call_command('load_foods', stdout=self.stdout)
            call_command('load_usda_catalog', apply=True, activate_demo=True, stdout=self.stdout)
            access.write_text('CUENTAS FICTICIAS · SOLO localhost · no reutilizar estas claves\n' +
                              '\n'.join(credentials) + '\n', encoding='utf-8')
        self.stdout.write(self.style.SUCCESS('Demo preparada: 1 entrenador, 3 clientes y 5 comidas.'))
        self.stdout.write('Accesos guardados únicamente en .demo/access.local.txt (excluido de git).')
