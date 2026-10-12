from django.urls import path

from . import views

urlpatterns = [
    # Clienta (siempre lo suyo)
    path('', views.member_plans, name='member_plans'),
    path('request/', views.member_plan_request, name='member_plan_request'),
    path('requests/<int:pk>/', views.member_plan_request_status, name='member_plan_request_status'),
    path('requests/<int:pk>/status/', views.member_plan_request_poll, name='member_plan_request_poll'),
    # Entrenador (solo clientas a las que tiene acceso)
    path('trainer/inbox/', views.trainer_plan_inbox, name='trainer_plan_inbox'),
    path('trainer/<int:pk>/', views.trainer_plan_detail, name='trainer_plan_detail'),
    path('trainer/<int:pk>/take/', views.trainer_plan_take, name='trainer_plan_take'),
    path('trainer/<int:pk>/approve/', views.trainer_plan_approve, name='trainer_plan_approve'),
    path('trainer/<int:pk>/regenerate/', views.trainer_plan_regenerate, name='trainer_plan_regenerate'),
    path('trainer/<int:pk>/archive/', views.trainer_plan_archive, name='trainer_plan_archive'),
    path('trainer/clients/<int:client_pk>/generate/', views.trainer_plan_generate, name='trainer_plan_generate'),
    path('trainer/jobs/<int:pk>/', views.trainer_plan_job, name='trainer_plan_job'),
    path('trainer/jobs/<int:pk>/status/', views.trainer_plan_job_poll, name='trainer_plan_job_poll'),
]
