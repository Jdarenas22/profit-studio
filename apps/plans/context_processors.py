from apps.health.services import health_features_enabled

from .ai import ai_enabled


def plan_flags(request):
    """`AI_PLANS_ENABLED` en las plantillas (enlaces "Mi plan" y "Planes por revisar"). No toca la
    base de datos: los textos legales y los cupos se revisan al entrar a la pantalla."""
    return {'AI_PLANS_ENABLED': health_features_enabled() and ai_enabled()}
