from django.urls import path

from .api import ruffier_dickson

urlpatterns = [
    path('<int:assessment_pk>/ruffier-dickson/', ruffier_dickson, name='api_ruffier_dickson'),
]

