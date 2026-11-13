"""Rutas del modulo de galpon.

Prefijo: /api/v1/avicultura/
@author Cristian Deysdayr Jimenez
"""
from django.urls import path

from apps.production.avicultura.api.views import (
    HouseListCreateView,
    HouseDetailView,
    LotListCreateView,
)

urlpatterns = [
    path("avicultura/<int:farm_id>/galpones/", HouseListCreateView.as_view(), name="avi-houses"),
    path(
        "avicultura/<int:farm_id>/galpones/<int:house_id>/",
        HouseDetailView.as_view(),
        name="avi-house",
    ),
    path("avicultura/<int:farm_id>/lotes/", LotListCreateView.as_view(), name="avi-lots"),]
