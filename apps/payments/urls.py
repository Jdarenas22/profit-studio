from django.urls import path
from . import views

urlpatterns = [
    path('checkout/<int:plan_pk>/', views.payment_checkout, name='payment_checkout'),
    path('return/', views.payment_return, name='payment_return'),
    path('webhook/', views.payment_webhook, name='payment_webhook'),
]
