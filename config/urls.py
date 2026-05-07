from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

admin.site.site_header = 'ProFit Studio — Panel Admin'
admin.site.site_title = 'ProFit Studio'
admin.site.index_title = 'Administración'

urlpatterns = [
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
