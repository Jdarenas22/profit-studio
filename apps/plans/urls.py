from django.urls import path

from . import views

urlpatterns = [
    # Clienta (siempre lo suyo)
    path('', views.member_plans, name='member_plans'),
    path('request/', views.member_plan_request, name='member_plan_request'),
    path('requests/<int:pk>/', views.member_plan_request_status, name='member_plan_request_status'),
    path('requests/<int:pk>/status/', views.member_plan_request_poll, name='member_plan_request_poll'),
    path('nutrition/', views.member_nutrition_plan, name='member_nutrition_plan'),
    path('nutrition/choices/', views.member_meal_choice, name='member_meal_choice'),
    path('nutrition/choices/<int:pk>/complete/', views.member_meal_complete, name='member_meal_complete'),
    path('nutrition/slots/<int:slot_pk>/alternatives/', views.member_meal_alternative, name='member_meal_alternative'),
    path('nutrition/exchanges/', views.member_exchanges, name='member_exchanges'),
    path('nutrition/shopping-list/', views.member_shopping_list, name='member_shopping_list'),
    # Entrenador (solo clientas a las que tiene acceso)
    path('trainer/inbox/', views.trainer_plan_inbox, name='trainer_plan_inbox'),
    path('trainer/inbox/badge/', views.trainer_plan_badge, name='trainer_plan_badge'),
    path('trainer/<int:pk>/', views.trainer_plan_detail, name='trainer_plan_detail'),
    path('trainer/<int:pk>/take/', views.trainer_plan_take, name='trainer_plan_take'),
    path('trainer/<int:pk>/approve/', views.trainer_plan_approve, name='trainer_plan_approve'),
    path('trainer/<int:pk>/regenerate/', views.trainer_plan_regenerate, name='trainer_plan_regenerate'),
    path('trainer/<int:pk>/archive/', views.trainer_plan_archive, name='trainer_plan_archive'),
    path('trainer/<int:pk>/options/<int:opt_pk>/edit/', views.trainer_meal_option_edit, name='trainer_meal_option_edit'),
    path('trainer/<int:pk>/options/<int:opt_pk>/review/', views.trainer_meal_option_review,
         name='trainer_meal_option_review'),
    path('trainer/clients/<int:client_pk>/generate/', views.trainer_plan_generate, name='trainer_plan_generate'),
    path('trainer/jobs/<int:pk>/', views.trainer_plan_job, name='trainer_plan_job'),
    path('trainer/jobs/<int:pk>/status/', views.trainer_plan_job_poll, name='trainer_plan_job_poll'),
]
