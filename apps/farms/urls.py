"""Rutas del dominio de fincas.

Propósito: publicar los recursos de fincas bajo el prefijo versionado.
Contexto: se incluyen desde config.api_urls junto a los demás dominios.
"""
from django.urls import path

from apps.farms.views import FarmDetailView, FarmListCreateView

app_name = "farms"

urlpatterns = [
    path("farms/", FarmListCreateView.as_view(), name="farm-list-create"),
    path("farms/<int:farm_id>/", FarmDetailView.as_view(), name="farm-detail"),
]
