"""Casos de uso del dominio IoT (IoT-02.2).

Independientes de HTTP. Toda escritura pasa por
``apps.iot.services.access.lock_manage_membership`` (MariaDB, bloqueo y rol).
"""

from apps.iot.services.authorizations import (
    authorize_gateway,
    list_coordinator_authorizations,
    revoke_gateway_authorization,
)
from apps.iot.services.broker_credentials import (
    BrokerConfirmationResult,
    confirm_broker_credential,
    finalize_broker_rotation,
    get_active_broker_credential,
    list_broker_credentials,
    register_pending_broker_credential,
    revoke_broker_credential,
)
from apps.iot.services.coordinator_credentials import (
    AuthenticatedCoordinatorFrame,
    CoordinatorGenerationExhausted,
    CoordinatorGenerationInconsistent,
    IssuedCoordinatorCredential,
    LegacyGenerationUnresolved,
    SequenceOutOfWindow,
    accept_coordinator_sequence,
    issue_coordinator_credential,
    list_coordinator_credentials,
    revoke_coordinator_credential,
    rotate_coordinator_credential,
    verify_coordinator_frame,
)
from apps.iot.services.credentials import (
    IssuedGatewayCredential,
    VerifiedGateway,
    issue_gateway_credential,
    list_gateway_credentials,
    revoke_gateway_credential,
    rotate_gateway_credential,
    verify_gateway_credential,
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
from apps.iot.services.ingestion import IngestionResult, IngestionStatus, ingest
from apps.iot.services.measurement_corrections import (
    create_measurement_correction,
    list_measurement_corrections,
)
from apps.iot.services.readings import list_readings
from apps.iot.services.sensors import (
    create_sensor,
    deactivate_sensor,
    get_sensor,
    list_sensors,
    reactivate_sensor,
    sensor_unit,
)
from apps.iot.services.sensor_sources import (
    create_sensor_source,
    deactivate_sensor_source,
    list_sensor_sources,
    reactivate_sensor_source,
    update_sensor_source,
)

__all__ = [
    "AuthenticatedCoordinatorFrame",
    "BrokerConfirmationResult",
    "confirm_broker_credential",
    "finalize_broker_rotation",
    "get_active_broker_credential",
    "list_broker_credentials",
    "register_pending_broker_credential",
    "revoke_broker_credential",
    "CoordinatorGenerationExhausted",
    "CoordinatorGenerationInconsistent",
    "IssuedCoordinatorCredential",
    "IssuedGatewayCredential",
    "LegacyGenerationUnresolved",
    "SequenceOutOfWindow",
    "VerifiedGateway",
    "IngestionResult",
    "IngestionStatus",
    "accept_coordinator_sequence",
    "authorize_gateway",
    "create_coordinator",
    "create_gateway",
    "create_sensor",
    "create_sensor_source",
    "create_measurement_correction",
    "deactivate_coordinator",
    "deactivate_gateway",
    "deactivate_sensor",
    "deactivate_sensor_source",
    "get_coordinator",
    "get_gateway",
    "get_sensor",
    "ingest",
    "issue_gateway_credential",
    "issue_coordinator_credential",
    "list_coordinator_authorizations",
    "list_coordinator_credentials",
    "list_coordinators",
    "list_gateway_credentials",
    "list_gateways",
    "list_readings",
    "list_measurement_corrections",
    "list_sensors",
    "list_sensor_sources",
    "reactivate_coordinator",
    "reactivate_gateway",
    "reactivate_sensor",
    "reactivate_sensor_source",
    "revoke_coordinator_credential",
    "revoke_gateway_authorization",
    "revoke_gateway_credential",
    "rotate_coordinator_credential",
    "rotate_gateway_credential",
    "sensor_unit",
    "update_sensor_source",
    "verify_coordinator_frame",
    "verify_gateway_credential",
]
