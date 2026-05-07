from django.urls import path
from . import views

urlpatterns = [
    path('', views.public_exercises, name='exercises'),
    path('<int:pk>/', views.exercise_detail, name='exercise_detail'),
    # Trainer CRUD
    path('trainer/', views.trainer_exercise_list, name='trainer_exercise_list'),
    path('trainer/new/', views.trainer_exercise_create, name='trainer_exercise_create'),
    path('trainer/<int:pk>/edit/', views.trainer_exercise_edit, name='trainer_exercise_edit'),
    path('trainer/<int:pk>/delete/', views.trainer_exercise_delete, name='trainer_exercise_delete'),
]
