from django.urls import path
from . import views

urlpatterns = [
    path('checkout/<int:plan_pk>/', views.payment_checkout, name='payment_checkout'),
    path('return/', views.payment_return, name='payment_return'),
    path('webhook/', views.payment_webhook, name='payment_webhook'),
    # Panel de pagos manuales
    path('manual/', views.trainer_manual_payment_list, name='trainer_manual_payment_list'),
    path('manual/<int:client_pk>/add/', views.trainer_manual_payment_add, name='trainer_manual_payment_add'),
    path('history/', views.member_payment_history, name='member_payment_history'),
]
