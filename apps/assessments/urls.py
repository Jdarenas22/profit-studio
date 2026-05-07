from django.urls import path
from . import views

urlpatterns = [
    path('', views.assessment_list, name='assessment_list'),
    path('trainer/new/<int:client_pk>/', views.trainer_assessment_create, name='trainer_assessment_create'),
    path('trainer/<int:pk>/', views.trainer_assessment_detail, name='trainer_assessment_detail'),
]
