"""Límites de la muestra universitaria; nunca habilita facturación ni producción."""
from pathlib import Path
import ipaddress

from django.conf import settings
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

MEMBERS = ('demo_grasa', 'demo_musculo', 'demo_mantenimiento')
TRAINER = 'demo_entrenador'
DEMO_USERS = (TRAINER, *MEMBERS)
DEMO_MODEL = 'gemini-3.5-flash-lite'


def demo_environment():
    """La etiqueta sola no basta: módulo explícito, DEBUG y SQLite dedicada."""
    database = settings.DATABASES.get('default', {})
    return bool(
        getattr(settings, 'ACADEMIC_DEMO', False) and settings.DEBUG
        and settings.SETTINGS_MODULE == 'config.settings.demo'
        and database.get('ENGINE') == 'django.db.backends.sqlite3'
        and Path(str(database.get('NAME', ''))).resolve() == (settings.BASE_DIR / '.demo/demo.sqlite3').resolve()
    )


class DemoBoundaryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            local = ipaddress.ip_address(request.META.get('REMOTE_ADDR', '')).is_loopback
        except ValueError:
            local = False
        if not demo_environment() or not local:
            return HttpResponseForbidden('La demostración solo está disponible en este equipo.')
        response = self.get_response(request)
        response['X-ProFit-Demo'] = 'academic-synthetic-data'
        response['Cache-Control'] = 'no-store'
        return response


@require_GET
@never_cache
def demo_home(request):
    if not demo_environment():
        raise Http404
    from . import ai
    from .models import Plan
    plans = []
    if request.user.is_authenticated and request.user.username in DEMO_USERS:
        query = Plan.objects.filter(client__username__in=MEMBERS).select_related('client')
        if request.user.username != TRAINER:
            query = query.filter(client=request.user)
        plans = query.order_by('-created_at')[:12]
    return render(request, 'plans/academic_demo.html', {'demo_mode': ai.ai_mode(), 'demo_plans': plans})
