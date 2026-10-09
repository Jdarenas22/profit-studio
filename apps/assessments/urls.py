from django.urls import path
from . import views

urlpatterns = [
    path('', views.assessment_list, name='assessment_list'),
    path('trainer/new/<int:client_pk>/', views.trainer_assessment_create, name='trainer_assessment_create'),
    path('trainer/<int:pk>/', views.trainer_assessment_detail, name='trainer_assessment_detail'),
    # Mediciones corporales
    path('measurements/<int:client_pk>/add/', views.trainer_measurement_add, name='trainer_measurement_add'),
    path('measurements/<int:pk>/edit/', views.trainer_measurement_edit, name='trainer_measurement_edit'),
    path('measurements/<int:pk>/delete/', views.trainer_measurement_delete, name='trainer_measurement_delete'),
    # Mediciones de la clienta (login + membresía vigente)
    path('me/measurements/', views.member_measurement_list, name='member_measurement_list'),
    path('me/measurements/add/', views.member_measurement_add, name='member_measurement_add'),
    path('me/measurements/<int:pk>/delete/', views.member_measurement_delete, name='member_measurement_delete'),
]
