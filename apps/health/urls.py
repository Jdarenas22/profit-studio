from django.urls import path

from . import views

urlpatterns = [
    path('consent/<str:purpose>/', views.member_consent, name='member_consent'),
    path('consent/<str:purpose>/revoke/', views.member_consent_revoke, name='member_consent_revoke'),
]
