from django.urls import path

from . import views

urlpatterns = [
    path('consent/<str:purpose>/', views.member_consent, name='member_consent'),
    path('consent/<str:purpose>/revoke/', views.member_consent_revoke, name='member_consent_revoke'),
    # Ficha de salud y alimentación — la clienta (siempre la suya)
    path('profile/', views.member_health_profile, name='member_health_profile'),
    path('profile/confirm/', views.member_health_profile_confirm, name='member_health_profile_confirm'),
    path('profile/export/', views.member_health_export, name='member_health_export'),
    path('profile/delete/', views.member_health_delete, name='member_health_delete'),
    # Ficha de salud — el entrenador (solo clientas a las que tiene acceso)
    path('clients/<int:client_pk>/profile/', views.trainer_health_profile, name='trainer_health_profile'),
    path('clients/<int:client_pk>/profile/edit/', views.trainer_health_profile_edit,
         name='trainer_health_profile_edit'),
]
