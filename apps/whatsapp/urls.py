"""Rutas de los avisos por WhatsApp. `config/urls.py` incluye `account_urlpatterns` bajo
`accounts/whatsapp/` y `urlpatterns` (el webhook) bajo `whatsapp/`."""
from django.urls import path

from . import views

urlpatterns = [
    path('webhook/', views.whatsapp_webhook, name='whatsapp_webhook'),
]

account_urlpatterns = [
    path('', views.whatsapp_settings, name='whatsapp_settings'),
    path('enable/', views.whatsapp_enable, name='whatsapp_enable'),
    path('disable/', views.whatsapp_disable, name='whatsapp_disable'),
    path('resend/', views.whatsapp_resend_confirmation, name='whatsapp_resend_confirmation'),
]
