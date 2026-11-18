"""FLESPI-01: catálogo de eventos de credenciales de broker.

Solo cambia las opciones de ``event_type`` (no el esquema en MariaDB); es
reversible sin pérdida de datos.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trazabilidad", "0012_alter_auditevent_event_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("farm_created", "Finca creada"),
                    ("farm_updated", "Finca actualizada"),
                    ("farm_deactivated", "Finca desactivada"),
                    ("farm_reactivated", "Finca reactivada"),
                    ("member_role_changed", "Rol de integrante cambiado"),
                    ("member_revoked", "Integrante revocado"),
                    ("member_left", "Integrante abandonó la finca"),
                    ("admin_transferred", "Administración transferida"),
                    ("email_verified", "Correo verificado"),
                    ("email_changed", "Correo cambiado"),
                    ("permission_granted", "Permiso concedido"),
                    ("permission_revoked", "Permiso revocado"),
                    ("permission_restricted", "Permiso restringido"),
                    ("permission_unrestricted", "Restricción retirada"),
                    ("session_opened", "Sesión abierta"),
                    ("session_closed", "Sesión cerrada"),
                    ("invitation_created", "Invitación creada"),
                    ("invitation_resend_started", "Reenvío de invitación iniciado"),
                    ("invitation_resent", "Invitación reenviada"),
                    ("invitation_delivery_failed", "Entrega de invitación fallida"),
                    ("invitation_role_changed", "Rol de invitación cambiado"),
                    ("invitation_cancelled", "Invitación cancelada"),
                    ("invitation_expired", "Invitación expirada"),
                    ("invitation_accepted", "Invitación aceptada"),
                    ("member_joined", "Integrante incorporado"),
                    ("member_reactivated", "Integrante reactivado"),
                    ("farm_creation_granted", "Capacidad de crear fincas concedida"),
                    ("farm_creation_revoked", "Capacidad de crear fincas retirada"),
                    ("iot_gateway_created", "Gateway IoT registrado"),
                    ("iot_gateway_deactivated", "Gateway IoT desactivado"),
                    ("iot_gateway_reactivated", "Gateway IoT reactivado"),
                    ("iot_coordinator_created", "Coordinador IoT registrado"),
                    ("iot_coordinator_deactivated", "Coordinador IoT desactivado"),
                    ("iot_coordinator_reactivated", "Coordinador IoT reactivado"),
                    ("iot_sensor_created", "Sensor IoT registrado"),
                    ("iot_sensor_deactivated", "Sensor IoT desactivado"),
                    ("iot_sensor_reactivated", "Sensor IoT reactivado"),
                    ("iot_gateway_authorized", "Coordinador IoT autorizado en gateway"),
                    (
                        "iot_gateway_authorization_revoked",
                        "Autorización de coordinador en gateway revocada",
                    ),
                    ("iot_gateway_credential_issued", "Credencial de gateway emitida"),
                    ("iot_gateway_credential_rotated", "Credencial de gateway rotada"),
                    ("iot_gateway_credential_revoked", "Credencial de gateway revocada"),
                    (
                        "iot_gateway_credential_compromised",
                        "Credencial de gateway revocada por compromiso",
                    ),
                    (
                        "iot_gateway_credential_cascade_revoked",
                        "Credencial de gateway revocada por desactivación del gateway",
                    ),
                    (
                        "iot_coordinator_credential_issued",
                        "Credencial de coordinador emitida",
                    ),
                    (
                        "iot_coordinator_credential_rotated",
                        "Credencial de coordinador rotada",
                    ),
                    (
                        "iot_coordinator_credential_revoked",
                        "Credencial de coordinador revocada",
                    ),
                    (
                        "iot_coordinator_credential_compromised",
                        "Credencial de coordinador revocada por compromiso",
                    ),
                    (
                        "iot_coordinator_credential_cascade_revoked",
                        "Credencial de coordinador revocada por desactivación",
                    ),
                    (
                        "iot_coordinator_credential_generation_exhausted",
                        "Generación de credencial de coordinador agotada",
                    ),
                    (
                        "iot_broker_credential_registered",
                        "Credencial de broker registrada",
                    ),
                    (
                        "iot_broker_credential_confirmed",
                        "Credencial de broker confirmada por el proveedor",
                    ),
                    (
                        "iot_broker_credential_activated",
                        "Credencial de broker activada",
                    ),
                    (
                        "iot_broker_credential_revoked",
                        "Credencial de broker revocada",
                    ),
                ],
                max_length=48,
            ),
        ),
    ]
