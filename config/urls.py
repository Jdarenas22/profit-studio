from django.contrib import admin
from django.urls import path, re_path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from django.http import JsonResponse


def health_check(request):
    """Endpoint de salud para Railway — responde 200 OK sin tocar la base de datos."""
    return JsonResponse({'status': 'ok'})


admin.site.site_header = 'ProFit Studio — Panel Admin'
admin.site.site_title = 'ProFit Studio'
admin.site.index_title = 'Administración'

urlpatterns = [
    path('health/', health_check),      # ← Railway HealthCheck
    path('admin/', admin.site.urls),
    path('', include('apps.public.urls')),
    path('accounts/', include('apps.accounts.urls')),
    path('exercises/', include('apps.exercises.urls')),
    path('routines/', include('apps.routines.urls')),
    path('memberships/', include('apps.memberships.urls')),
    path('assessments/', include('apps.assessments.urls')),
    path('payments/', include('apps.payments.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    try:
        import debug_toolbar
        urlpatterns = [path('__debug__/', include(debug_toolbar.urls))] + urlpatterns
    except ImportError:
        pass
else:
    # En producción (Railway) Django sirve media files directamente.
    # No hay nginx ni R2 configurado — esto permite ver imágenes/videos subidos.
    # Nota: los archivos se pierden al redesplegar (Railway no tiene volumen persistente).
    # Para videos permanentes usa el campo "URL de YouTube" en el formulario de ejercicios.
    import re as _re
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]
