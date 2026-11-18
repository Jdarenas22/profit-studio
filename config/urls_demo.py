from django.urls import path
from apps.plans.academic import demo_home
from .urls import urlpatterns as application_patterns

urlpatterns = [path('demo/', demo_home, name='academic_demo'), *[pattern for pattern in application_patterns if not str(pattern.pattern).startswith('__debug__/')]]
