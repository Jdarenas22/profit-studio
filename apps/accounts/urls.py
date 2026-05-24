from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    # ── Auth básico ────────────────────────────────────────────────────────────
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),
    path('register/success/', views.register_success, name='register_success'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('profile/edit/', views.member_profile_edit, name='member_profile_edit'),

    # ── Recuperación de contraseña ─────────────────────────────────────────────
    path('password-reset/',
         auth_views.PasswordResetView.as_view(
             template_name='accounts/password_reset.html',
             email_template_name='accounts/password_reset_email.txt',
             subject_template_name='accounts/password_reset_subject.txt',
             success_url='/accounts/password-reset/sent/',
         ),
         name='password_reset'),
    path('password-reset/sent/',
         auth_views.PasswordResetDoneView.as_view(
             template_name='accounts/password_reset_done.html',
         ),
         name='password_reset_done'),
    path('reset/<uidb64>/<token>/',
         auth_views.PasswordResetConfirmView.as_view(
             template_name='accounts/password_reset_confirm.html',
             success_url='/accounts/reset/done/',
         ),
         name='password_reset_confirm'),
    path('reset/done/',
         auth_views.PasswordResetCompleteView.as_view(
             template_name='accounts/password_reset_complete.html',
         ),
         name='password_reset_complete'),

    # ── Panel de entrenadores ──────────────────────────────────────────────────
    path('trainer/', views.trainer_dashboard, name='trainer_dashboard'),
    path('trainer/clients/', views.trainer_client_list, name='trainer_clients'),
    path('trainer/clients/add/', views.trainer_add_client, name='trainer_add_client'),
    path('trainer/clients/<int:pk>/', views.trainer_client_detail, name='trainer_client_detail'),
    path('trainer/clients/<int:pk>/edit/', views.trainer_client_edit, name='trainer_client_edit'),
    path('trainer/clients/<int:pk>/delete/', views.trainer_client_delete, name='trainer_client_delete'),
    path('trainer/clients/<int:pk>/assign/', views.trainer_assign_client, name='trainer_assign_client'),

    # ── Gestión de entrenadores (solo Yiseth) ──────────────────────────────────
    path('trainer/staff/', views.trainer_staff_list, name='trainer_staff_list'),
    path('trainer/staff/create/', views.trainer_create_trainer, name='trainer_create_trainer'),
]
