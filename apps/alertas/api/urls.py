"""Rutas de alertas.

Prefijo: /api/v1/
@author Cristian Deysdayr Jimenez
"""
from django.urls import path

from apps.alertas.api.views import AlertListView

app_name = "alertas"

urlpatterns = [
    path("alerts/", AlertListView.as_view(), name="alert-list"),
]
