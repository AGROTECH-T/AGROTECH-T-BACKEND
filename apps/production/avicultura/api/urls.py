"""Rutas del modulo de galpon.

Prefijo: /api/v1/avicultura/
@author Cristian Deysdayr Jimenez
"""
from django.urls import path

from apps.production.avicultura.api.views import (
    HouseListCreateView,
    HouseDetailView,
    LotListCreateView,
    LotDetailView,
    LotCloseView,
    LotReopenView,
)

urlpatterns = [
    path("avicultura/<int:farm_id>/galpones/", HouseListCreateView.as_view(), name="avi-houses"),
    path(
        "avicultura/<int:farm_id>/galpones/<int:house_id>/",
        HouseDetailView.as_view(),
        name="avi-house",
    ),
    path("avicultura/<int:farm_id>/lotes/", LotListCreateView.as_view(), name="avi-lots"),
    path(
        "avicultura/<int:farm_id>/lotes/<int:lot_id>/",
        LotDetailView.as_view(),
        name="avi-lot",
    ),
    path(
        "avicultura/<int:farm_id>/lotes/<int:lot_id>/cerrar/",
        LotCloseView.as_view(),
        name="avi-lot-close",
    ),
    path(
        "avicultura/<int:farm_id>/lotes/<int:lot_id>/reabrir/",
        LotReopenView.as_view(),
        name="avi-lot-reopen",
    ),]
