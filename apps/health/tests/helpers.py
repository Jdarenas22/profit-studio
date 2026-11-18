"""Datos comunes de las pruebas de salud (medidas, consentimiento y ficha).

Sobre el escenario de apps/accounts/tests/helpers.py (boss, trainer_a/b, client_a/b/free) agrega
membresías vigentes y utilidades para otorgar consentimiento.

Las pantallas las construye frontend-architect: si alguna plantilla aún no existe, las pruebas
usan una plantilla mínima de reemplazo SOLO para esa (`stub_missing_templates`); cuando la real
existe, se usa la real.
"""
from datetime import date, timedelta

from django.conf import settings
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.test import override_settings

from apps.accounts.models import User
from apps.accounts.tests.helpers import ScenarioTestCase, make_user
from apps.health import dates, services
from apps.health.forms import HealthProfileForm
from apps.health.models import PURPOSE_AI, PURPOSE_HEALTH_DATA, ConsentTextVersion
from apps.memberships.models import Membership

# Plantillas pendientes del frontend (docs/contracts/requests.md, "Contrato A1" y "Contrato A2").
# Mientras no existan se usa una plantilla mínima; cuando existe la real se usa la real, por eso
# las pruebas comprueban el CONTEXTO (response.context) y no el HTML.
_STUBS = {
    'health/minor_blocked.html': 'MINOR_BLOCKED',
    'trainer/body_measurement_edit.html':
        'EDIT weight={{ form.weight }} hip={{ form.hip_cm }} '
        '{% for key, value in errors.items %}[{{ key }}]{% endfor %}',
    # Fase D (docs/contracts/plan-ia-fase-d.md; las plantillas reales son de frontend-architect)
    'trainer/client_plans.html': 'CLIENT_PLANS {{ client.pk }}',
    'trainer/client_progress.html': 'CLIENT_PROGRESS {{ client.pk }}',
}


def stub_missing_templates():
    missing = {}
    for name, body in _STUBS.items():
        try:
            get_template(name)
        except TemplateDoesNotExist:
            missing[name] = body
    if not missing:
        return lambda cls: cls
    config = dict(settings.TEMPLATES[0])
    config['APP_DIRS'] = False
    options = dict(config.get('OPTIONS', {}))
    options['loaders'] = [
        ('django.template.loaders.locmem.Loader', missing),
        'django.template.loaders.filesystem.Loader',
        'django.template.loaders.app_directories.Loader',
    ]
    config['OPTIONS'] = options
    return override_settings(TEMPLATES=[config])


def adult_birth_date(years=30):
    """Fecha de nacimiento de alguien con `years` años cumplidos hoy (más unos días de margen)."""
    today = dates.today()
    return today.replace(year=today.year - years, day=1) - timedelta(days=10)


# Valor neutro de cada campo médico / alimentario en un POST (lo que manda el navegador cuando la
# clienta no marca nada)
_HEALTH_POST_NEUTRAL = {
    'conditions': [], 'condition_controlled': '', 'medical_clearance': '', 'clearance_date': '',
    'medications': [], 'injuries': [], 'recent_surgery': 'none', 'pregnancy_status': 'none',
    'eating_disorder_history': 'no', 'other_condition_text': '', 'medications_text': '',
    'injuries_text': '', **{f'parq_q{number}': 'no' for number in range(1, 8)},
}
_FOOD_POST_NEUTRAL = {'diet_type': 'omnivore', 'allergies': [], 'allergy_severity': '', 'intolerances': []}


def _screen_for(overrides, neutral):
    """'yes' si `overrides` trae algún dato distinto del neutro; si no, 'no'."""
    for name, value in overrides.items():
        if name in neutral and value is not None and value != neutral[name]:
            return 'yes'
    return 'no'


def valid_post(**overrides):
    """POST completo y válido de la ficha (como lo enviaría el navegador), con TODOS los datos
    opcionales declarados. Con `clave=None` se quita el campo; las listas van como listas.

    Ficha básica: si no se pasan `health_screen` / `food_screen`, se deducen como lo haría la
    clienta: 'yes' si las sobrescrituras traen algún dato médico (o alimentario) distinto del
    neutro, y 'no' si no. Así `valid_post(conditions=['asthma'])` responde «Sí» y lo marca."""
    data = {
        'birth_date': adult_birth_date(30).isoformat(),
        'sex_for_calculation': 'F',
        'training_goal': 'toning',
        'target_weight_kg': '58,5',
        'conditions': [], 'condition_controlled': '', 'medical_clearance': '', 'clearance_date': '',
        'medications': [], 'injuries': [],
        'recent_surgery': 'none', 'pregnancy_status': 'none', 'eating_disorder_history': 'no',
        'other_condition_text': '', 'medications_text': '', 'injuries_text': '',
        'diet_type': 'omnivore', 'allergies': [], 'allergy_severity': '', 'intolerances': [],
        'meal_slots': ['breakfast', 'lunch', 'dinner'],
        'meal_time_breakfast': '07:00', 'meal_time_lunch': '12:30', 'meal_time_dinner': '19:00',
        'cooking_access': 'basic', 'budget_level': 'medium', 'eats_out_per_week': '2',
        'disliked_foods_text': '', 'liked_foods_text': '',
        'training_days_per_week': '3', 'session_minutes': '60', 'training_place': 'gym',
        'equipment': [], 'experience_level': 'beginner', 'activity_level': 'light', 'sleep_hours': '7,5',
    }
    for number in range(1, 8):
        data[f'parq_q{number}'] = 'no'
    data['health_screen'] = _screen_for(overrides, _HEALTH_POST_NEUTRAL)
    data['food_screen'] = _screen_for(overrides, _FOOD_POST_NEUTRAL)
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def basic_post(**overrides):
    """Ficha básica MÍNIMA (contrato ficha-basica, sección 2): solo los pasos 1 a 4, con «No» en
    salud y en alimentación y sin abrir «Más detalles». Las comidas obligatorias llegan siempre
    (campos ocultos del asistente)."""
    data = {
        'birth_date': adult_birth_date(30).isoformat(),
        'sex_for_calculation': 'F',
        'training_goal': 'toning',
        'health_screen': 'no',
        'food_screen': 'no',
        'training_days_per_week': '3', 'session_minutes': '45', 'training_place': 'gym',
        'experience_level': 'beginner',
        'meal_slots': ['breakfast', 'lunch', 'dinner'],
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def cleaned_profile(known_height=None, basic=False, **overrides):
    """`cleaned_data` de un POST válido (falla la prueba si el formulario lo rechaza). Con
    `basic=True` parte de la ficha básica mínima (`basic_post`)."""
    post = basic_post(**overrides) if basic else valid_post(**overrides)
    form = HealthProfileForm(post, known_height=known_height)
    assert form.is_valid(), form.errors.as_json()
    return form.cleaned_data


class HealthScenarioTestCase(ScenarioTestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        today = date.today()
        for client in (cls.client_a, cls.client_b, cls.client_free):
            Membership.objects.create(
                user=client, plan=cls.plan, start_date=today,
                end_date=today + timedelta(days=30), activated_by=cls.boss,
            )

    # ─── utilidades ───────────────────────────────────────────────────────────
    @staticmethod
    def grant(user, purpose=PURPOSE_HEALTH_DATA):
        text = services.current_text(purpose)
        return services.grant_consent(user, purpose, text.version)[0]

    @staticmethod
    def publish_new_version(purpose, body='Texto nuevo sin marcadores.'):
        last = ConsentTextVersion.objects.filter(purpose=purpose).order_by('-version').first()
        return ConsentTextVersion.objects.create(
            purpose=purpose, version=last.version + 1, body=body, is_current=True,
        )

    def make_profile(self, user, actor=None, grant=True, basic=False, **overrides):
        """Guarda una ficha (versión nueva) por el servicio, como lo haría la vista. Con
        `basic=True`, la ficha básica mínima."""
        if grant and not services.has_health_consent(user):
            self.grant(user)
        return services.save_profile(user, cleaned_profile(basic=basic, **overrides), actor=actor or user)

    @staticmethod
    def new_member(username, with_membership=True, plan=None):
        user = make_user(username, User.ROLE_MEMBER)
        if with_membership:
            today = date.today()
            Membership.objects.create(
                user=user, plan=plan, start_date=today,
                end_date=today + timedelta(days=30),
            )
        return user


__all__ = ['HealthScenarioTestCase', 'stub_missing_templates', 'PURPOSE_AI', 'PURPOSE_HEALTH_DATA',
           'valid_post', 'basic_post', 'cleaned_profile', 'adult_birth_date']
