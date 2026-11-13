"""Casos de uso del dominio IoT (IoT-02.2).

Independientes de HTTP. Toda escritura pasa por
``apps.iot.services.access.lock_manage_membership`` (MariaDB, bloqueo y rol).
"""
from apps.iot.services.authorizations import (
    authorize_gateway,
    list_coordinator_authorizations,
    revoke_gateway_authorization,
)
from apps.iot.services.devices import (
    create_coordinator,
    create_gateway,
    deactivate_coordinator,
    deactivate_gateway,
    get_coordinator,
    get_gateway,
    list_coordinators,
    list_gateways,
    reactivate_coordinator,
    reactivate_gateway,
)
from apps.iot.services.sensors import (
    create_sensor,
    deactivate_sensor,
    get_sensor,
    list_sensors,
    reactivate_sensor,
    sensor_unit,
)

__all__ = [
    "authorize_gateway",
    "create_coordinator",
    "create_gateway",
    "create_sensor",
    "deactivate_coordinator",
    "deactivate_gateway",
    "deactivate_sensor",
    "get_coordinator",
    "get_gateway",
    "get_sensor",
    "list_coordinator_authorizations",
    "list_coordinators",
    "list_gateways",
    "list_sensors",
    "reactivate_coordinator",
    "reactivate_gateway",
    "reactivate_sensor",
    "revoke_gateway_authorization",
    "sensor_unit",
]
