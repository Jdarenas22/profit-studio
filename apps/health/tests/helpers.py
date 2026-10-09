"""Datos comunes de las pruebas de salud (medidas + consentimiento).

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
from apps.health import services
from apps.health.models import PURPOSE_AI, PURPOSE_HEALTH_DATA, ConsentTextVersion
from apps.memberships.models import Membership

# Plantillas pendientes del frontend (docs/contracts/requests.md, "Contrato A1")
_STUBS = {
    'health/minor_blocked.html': 'MINOR_BLOCKED',
    'trainer/body_measurement_edit.html':
        'EDIT weight={{ form.weight }} hip={{ form.hip_cm }} '
        '{% for key, value in errors.items %}[{{ key }}]{% endfor %}',
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


__all__ = ['HealthScenarioTestCase', 'stub_missing_templates', 'PURPOSE_AI', 'PURPOSE_HEALTH_DATA']
