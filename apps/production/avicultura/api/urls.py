"""Rutas del módulo de galpón.

Prefijo: /api/v1/avicultura/
@author Cristian Deysdayr Jimenez
"""
from django.urls import path

from apps.production.avicultura.api.views import (
    AlertCloseView,
    AlertListView,
    EventCorrectionView,
    EventDetailView,
    EventListCreateView,
    HouseDetailView,
    HouseListCreateView,
    LotCloseView,
    LotDetailView,
    LotListCreateView,
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
    ),
    path(
        "avicultura/<int:farm_id>/lotes/<int:lot_id>/eventos/",
        EventListCreateView.as_view(),
        name="avi-events",
    ),
    path(
        "avicultura/<int:farm_id>/eventos/<int:event_id>/corregir/",
        EventCorrectionView.as_view(),
        name="avi-event-correction",
    ),
    path(
        "avicultura/<int:farm_id>/eventos/<int:event_id>/",
        EventDetailView.as_view(),
        name="avi-event",
    ),
    path("avicultura/<int:farm_id>/alertas/", AlertListView.as_view(), name="avi-alerts"),
    path(
        "avicultura/<int:farm_id>/alertas/<int:alert_id>/cerrar/",
        AlertCloseView.as_view(),
        name="avi-alert-close",
    ),
]
