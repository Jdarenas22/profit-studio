"""Datos comunes para las pruebas de autorización.

Escenario:
  boss        superusuaria (trainer + is_superuser): ve todo y es la única que asigna.
  trainer_a   entrenador A (cliente_a asignado)
  trainer_b   entrenador B (cliente_b asignado)
  client_a    cliente asignado a A
  client_b    cliente asignado a B
  client_free cliente auto-registrado, sin entrenador asignado (solo lo ve la superusuaria)
"""
from datetime import date

from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.assessments.models import InitialAssessment, BodyMeasurement
from apps.exercises.models import Exercise
from apps.memberships.models import MembershipPlan
from apps.payments.models import ManualPayment
from apps.routines.models import Routine, RoutineDay, RoutineExercise

PASSWORD = 'Clave-De-Prueba-9182!'
# Los templates usan {% static %}: en pruebas no hay collectstatic ni manifest
NO_MANIFEST = override_settings(
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)


def make_user(username, role, **extra):
    return User.objects.create_user(
        username=username, password=PASSWORD, role=role,
        email=f'{username}@example.com', first_name=username.capitalize(),
        last_name='Prueba', **extra,
    )


@NO_MANIFEST
class ScenarioTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.boss = make_user('boss', User.ROLE_TRAINER, is_staff=True, is_superuser=True)
        cls.trainer_a = make_user('trainer_a', User.ROLE_TRAINER, is_staff=True)
        cls.trainer_b = make_user('trainer_b', User.ROLE_TRAINER, is_staff=True)
        cls.client_a = make_user('client_a', User.ROLE_MEMBER, assigned_trainer=cls.trainer_a)
        cls.client_b = make_user('client_b', User.ROLE_MEMBER, assigned_trainer=cls.trainer_b)
        cls.client_free = make_user('client_free', User.ROLE_MEMBER)

        cls.plan = MembershipPlan.objects.create(
            name='Mensual', duration_days=30, reference_price=100000,
        )
        cls.exercise = Exercise.objects.create(
            name='Sentadilla', level=Exercise.LEVEL_BEGINNER,
            description='x', muscles='piernas',
        )

        def assessment_for(client, trainer):
            return InitialAssessment.objects.create(
                user=client, trainer=trainer, age=30, sex='F', weight=60,
                height='1.65', goal='tonificar',
            )

        cls.assessment_a = assessment_for(cls.client_a, cls.trainer_a)
        cls.assessment_b = assessment_for(cls.client_b, cls.trainer_b)
        cls.assessment_free = assessment_for(cls.client_free, cls.boss)

        def routine_for(client, trainer):
            routine = Routine.objects.create(name=f'Rutina {client.username}', user=client, trainer=trainer)
            day = RoutineDay.objects.create(routine=routine, name='Dia 1', order=0)
            item = RoutineExercise.objects.create(
                day=day, exercise=cls.exercise, sets=3, reps='10', rest_seconds=60,
            )
            return routine, day, item

        cls.routine_a, cls.day_a, cls.item_a = routine_for(cls.client_a, cls.trainer_a)
        cls.routine_b, cls.day_b, cls.item_b = routine_for(cls.client_b, cls.trainer_b)
        cls.routine_free, cls.day_free, cls.item_free = routine_for(cls.client_free, cls.boss)

        for client, trainer in ((cls.client_a, cls.trainer_a), (cls.client_b, cls.trainer_b),
                                (cls.client_free, cls.boss)):
            ManualPayment.objects.create(
                user=client, trainer=trainer, amount=50000, payment_date=date.today(),
            )
            BodyMeasurement.objects.create(user=client, trainer=trainer, weight=60)

    def login(self, user):
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        return user
