from functools import wraps

from django.http import Http404
from django.views.decorators.cache import never_cache

from .services import health_features_enabled


def health_feature_required(view_func):
    """404 mientras `HEALTH_FEATURES_ENABLED` esté apagado (producción hasta aprobar los textos
    legales). Además marca la respuesta `Cache-Control: no-store`: son datos de salud."""
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not health_features_enabled():
            raise Http404
        return view_func(request, *args, **kwargs)
    return never_cache(_wrapped)
