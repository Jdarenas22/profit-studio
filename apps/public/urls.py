from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('about/', views.about, name='about'),
    path('contact/', views.contact, name='contact'),
    # Service worker de la PWA: en la raíz para que pueda controlar todo el sitio (scope '/')
    path('sw.js', views.service_worker, name='service_worker'),
]
