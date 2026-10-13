"""Escenario de las pruebas de planes con IA (sin red: siempre `FakeAIClient`).

Sobre el escenario de salud (boss, trainer_a/b, client_a/b/free con membresía vigente) agrega:
- textos de consentimiento SIN marcadores (como si el abogado ya los hubiera aprobado), para que
  `ai_texts_ready()` sea verdadero también con DEBUG=False;
- un catálogo de ejercicios etiquetados (gimnasio, principiante, intensidad moderada);
- `ready(client)`: consentimientos de salud y de IA + ficha confirmada.
Los trabajos corren en línea (`AI_PLANS_RUN_INLINE`) y el correo es el de memoria de Django.
"""
from contextlib import contextmanager
from unittest import mock

from django.test import override_settings

from apps.exercises.models import Exercise
from apps.health.tests.helpers import PURPOSE_AI, PURPOSE_HEALTH_DATA, HealthScenarioTestCase
from apps.plans import jobs, services
from apps.plans.ai.fake import FakeAIClient
from apps.plans.foods_data import food_rows
from apps.plans.models import KIND_EXERCISE, KIND_NUTRITION, Food, Plan, TrainerCredential

PLAN_SETTINGS = override_settings(
    HEALTH_FEATURES_ENABLED=True, AI_PLANS_MODE='simulated', AI_PLANS_RUN_INLINE=True,
    AI_PLANS_CLIENT_WEEKLY_LIMIT=3, AI_PLANS_TRAINER_DAILY_LIMIT=10, AI_PLANS_DAILY_JOB_LIMIT=100,
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
)


@PLAN_SETTINGS
class PlanScenarioTestCase(HealthScenarioTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for purpose in (PURPOSE_HEALTH_DATA, PURPOSE_AI):
            cls.publish_new_version(purpose, body='Texto revisado por el abogado, sin marcadores.')
        names = ['Prensa de piernas', 'Remo en máquina', 'Jalón al pecho', 'Press de pecho en máquina',
                 'Curl de bíceps', 'Extensión de tríceps', 'Plancha', 'Puente de glúteo',
                 'Elevaciones laterales', 'Abducción de cadera']
        cls.tagged = [
            Exercise.objects.create(name=name, level=Exercise.LEVEL_BEGINNER, description='x', muscles='y',
                                    intensity='moderate', equipment=['machines'], risk_tags=[])
            for name in names
        ]

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(jobs, '_sleep', lambda seconds: None)   # sin esperas reales
        patcher.start()
        self.addCleanup(patcher.stop)

    def ready(self, client, **profile_overrides):
        """La clienta queda lista para pedir un plan (consentimientos + ficha confirmada)."""
        self.grant(client, PURPOSE_HEALTH_DATA)
        self.grant(client, PURPOSE_AI)
        return self.make_profile(client, **profile_overrides)

    @contextmanager
    def fake_ai(self, scenario='ok'):
        """Reemplaza el cliente de IA; devuelve el doble para revisar qué recibió."""
        fake = FakeAIClient(scenario)
        with mock.patch('apps.plans.ai.get_ai_client', return_value=fake):
            yield fake

    def request(self, client, actor=None, scenario='ok', **kwargs):
        """Pide un plan de ejercicio y lo ejecuta (en línea). Devuelve (job, fake)."""
        with self.fake_ai(scenario) as fake, self.captureOnCommitCallbacks(execute=True):
            job, _created = services.request_plan(
                client, KIND_EXERCISE, actor or client, services.new_request_token(), **kwargs)
        job.refresh_from_db()
        return job, fake


class NutritionScenarioTestCase(PlanScenarioTestCase):
    """Además del escenario de planes: catálogo de alimentos validado y la superusuaria con
    credencial de nutrición (el entrenador asignado NO la tiene)."""

    CREDENTIAL = 'Nutricionista-dietista, RETHUS N.º 12345'

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        for row in food_rows():
            name = row.pop('name')
            Food.objects.create(name=name, validated_by='Prueba, nutricionista', **row)
        TrainerCredential.objects.create(user=cls.boss, can_validate_nutrition=True, registration_label=cls.CREDENTIAL)

    def request_nutrition(self, client, actor=None, scenario='ok', **kwargs):
        """Pide un plan de alimentación y lo ejecuta (en línea). Devuelve (job, fake)."""
        with self.fake_ai(scenario) as fake, self.captureOnCommitCallbacks(execute=True):
            job, _created = services.request_plan(
                client, KIND_NUTRITION, actor or client, services.new_request_token(), **kwargs)
        job.refresh_from_db()
        return job, fake

    def nutrition_draft(self, client, **profile_overrides):
        """Clienta lista + borrador de alimentación generado (simulado)."""
        self.ready(client, **profile_overrides)
        job, _fake = self.request_nutrition(client)
        assert job.status == job.STATUS_SUCCEEDED, job.error_code
        return job.plan

    def approved_nutrition(self, client, **profile_overrides):
        """Plan de alimentación APROBADO (se marca como no simulado, como si viniera de Gemini)."""
        plan = self.nutrition_draft(client, **profile_overrides)
        Plan.objects.filter(pk=plan.pk).update(is_simulated=False)
        plan.refresh_from_db()
        return services.approve_plan(plan, self.boss, note_for_client='Buen provecho')
