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
    # Producción: Django sirve /media/ SOLO si NO hay almacenamiento externo (R2)
    # y MEDIA_ROOT está definido y no vacío. Con R2 los archivos salen del bucket
    # (R2_PUBLIC_URL) y esta ruta no debe existir; con MEDIA_ROOT vacío serviría el
    # directorio de trabajo y expondría .env y el código fuente.
    # Nota: los archivos locales se pierden al redesplegar (Railway no tiene volumen persistente).
    # Para videos permanentes usa el campo "URL de YouTube" en el formulario de ejercicios.
    _media_root = str(getattr(settings, 'MEDIA_ROOT', '') or '').strip()
    _uses_r2 = bool(getattr(settings, 'AWS_STORAGE_BUCKET_NAME', ''))
    if _media_root and not _uses_r2:
        urlpatterns += [
            re_path(r'^media/(?P<path>.*)$', serve, {'document_root': _media_root}),
        ]
