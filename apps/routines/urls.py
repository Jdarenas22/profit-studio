from django.urls import path
from . import views

urlpatterns = [
    path('my-routine/', views.member_routine, name='member_routine'),
    # Trainer
    path('trainer/', views.trainer_routine_list, name='trainer_routine_list'),
    path('trainer/new/', views.trainer_routine_create, name='trainer_routine_create'),
    path('trainer/<int:pk>/', views.trainer_routine_builder, name='trainer_routine_builder'),
    path('trainer/<int:pk>/delete/', views.trainer_routine_delete, name='trainer_routine_delete'),
    # HTMX — días
    path('trainer/<int:pk>/days/add/', views.trainer_add_day, name='trainer_add_day'),
    path('trainer/<int:pk>/days/<int:day_pk>/delete/', views.trainer_delete_day, name='trainer_delete_day'),
    # HTMX — ejercicios en día
    path('trainer/<int:pk>/days/<int:day_pk>/exercises/add/', views.trainer_add_exercise_to_day, name='trainer_add_exercise_to_day'),
    path('trainer/<int:pk>/days/<int:day_pk>/exercises/<int:re_pk>/delete/', views.trainer_remove_exercise, name='trainer_remove_exercise'),
]
