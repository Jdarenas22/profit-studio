from .services import health_features_enabled


def health_flags(request):
    """`HEALTH_FEATURES_ENABLED` en las plantillas, para mostrar u ocultar los enlaces de salud
    (por ejemplo "Mis medidas" en el menú de la clienta)."""
    return {'HEALTH_FEATURES_ENABLED': health_features_enabled()}
