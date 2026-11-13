"""Rutas del modulo de galpon.

Prefijo: /api/v1/avicultura/
@author Cristian Deysdayr Jimenez
"""
from django.urls import path

from apps.production.avicultura.api.views import (
    HouseListCreateView,
)

urlpatterns = [
    path("avicultura/<int:farm_id>/galpones/", HouseListCreateView.as_view(), name="avi-houses"),]
