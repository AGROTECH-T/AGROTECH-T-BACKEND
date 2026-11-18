"""Rutas de consulta IoT bajo ``/api/v1/iot/``."""
from django.urls import path

from apps.iot.api.provisioning import (
    CoordinatorProvisioningView,
    CredentialHistoryView,
    GatewayProvisioningView,
)
from apps.iot.api.views import (
    CoordinatorGatewayAuthorizationView,
    CoordinatorGatewayRevokeView,
    MeasurementCorrectionListCreateView,
    ReadingListView,
    SensorListView,
    SensorSourceDetailView,
    SensorSourceListView,
)

urlpatterns = [
    path("farms/<int:farm_id>/gateways/", GatewayProvisioningView.as_view(), name="iot-gateways"),
    path(
        "farms/<int:farm_id>/coordinators/",
        CoordinatorProvisioningView.as_view(),
        name="iot-coordinators",
    ),
    path(
        "farms/<int:farm_id>/coordinators/<str:uid>/credentials/",
        CredentialHistoryView.as_view(),
        {"device_kind": "coordinator"},
        name="iot-coordinator-credentials",
    ),
    path(
        "farms/<int:farm_id>/coordinators/<str:coordinator_uid>/gateways/",
        CoordinatorGatewayAuthorizationView.as_view(),
        name="iot-coordinator-gateways",
    ),
    path(
        "farms/<int:farm_id>/coordinators/<str:coordinator_uid>/gateways/<str:gateway_uid>/revoke/",
        CoordinatorGatewayRevokeView.as_view(),
        name="iot-coordinator-gateway-revoke",
    ),
    path(
        "farms/<int:farm_id>/gateways/<str:uid>/credentials/",
        CredentialHistoryView.as_view(),
        {"device_kind": "gateway"},
        name="iot-gateway-credentials",
    ),
    path("farms/<int:farm_id>/sensors/", SensorListView.as_view(), name="iot-sensors"),
    path(
        "farms/<int:farm_id>/sensor-sources/",
        SensorSourceListView.as_view(),
        name="iot-sensor-sources",
    ),
    path(
        "farms/<int:farm_id>/sensor-sources/<uuid:source_uid>/",
        SensorSourceDetailView.as_view(),
        name="iot-sensor-source-detail",
    ),
    path(
        "farms/<int:farm_id>/sensor-sources/<uuid:source_uid>/deactivate/",
        SensorSourceDetailView.as_view(),
        {"action": "deactivate"},
        name="iot-sensor-source-deactivate",
    ),
    path("farms/<int:farm_id>/readings/", ReadingListView.as_view(), name="iot-readings"),
    path(
        "farms/<int:farm_id>/readings/<uuid:measurement_uid>/corrections/",
        MeasurementCorrectionListCreateView.as_view(),
        name="iot-reading-corrections",
    ),
]
