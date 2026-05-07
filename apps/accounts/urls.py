from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),
    path('register/success/', views.register_success, name='register_success'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('trainer/', views.trainer_dashboard, name='trainer_dashboard'),
    path('trainer/clients/', views.trainer_client_list, name='trainer_clients'),
    path('trainer/clients/add/', views.trainer_add_client, name='trainer_add_client'),
    path('trainer/clients/<int:pk>/', views.trainer_client_detail, name='trainer_client_detail'),
    path('trainer/clients/<int:pk>/edit/', views.trainer_client_edit, name='trainer_client_edit'),
    path('trainer/clients/<int:pk>/delete/', views.trainer_client_delete, name='trainer_client_delete'),
    path('profile/edit/', views.member_profile_edit, name='member_profile_edit'),
]
