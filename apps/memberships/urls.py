from django.urls import path
from . import views

urlpatterns = [
    path('plans/', views.plans_view, name='plans'),
    path('trainer/<int:client_pk>/', views.trainer_membership_manage, name='trainer_membership_manage'),
]
