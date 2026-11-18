"""MVP-IOT-F3 - Aprovisionamiento local IoT para la demostracion del MVP.

Uso (desde la carpeta del backend, con el venv activo):
    python manage.py shell -c "exec(open('tools/iot/mvp_iot_provision.py', encoding='utf-8').read())"

Crea, con los servicios existentes y en UNA transaccion:
  gateway + GatewayCredential, coordinador + CoordinatorCredential,
  autorizacion coordinador->gateway, fuentes BME680/DHT11 y sensores 1..4.
No imprime el secreto del gateway. La clave del coordinador se muestra UNA vez
como linea SET para cargarla por serie en el coordinador (F8). No la guarde en
archivos del repositorio ni la pegue en chats.
"""
from django.db import transaction
from django.db.models import Q

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.farms.capabilities import IOT_MANAGE_ROLES
from apps.farms.models import FarmMembership, MembershipStatus
from apps.iot import services
from apps.iot.models import Coordinator, Gateway

SENSORS = (
    # canal, metrica, fuente
    (1, "temperature", "BME680"),
    (2, "relative_humidity", "BME680"),
    (3, "temperature", "DHT11"),
    (4, "relative_humidity", "DHT11"),
)


def _run():
    print("\n=== MVP-IOT-F3: aprovisionamiento IoT local ===\n")
    ident = input("Documento o correo del usuario administrador: ").strip()
    users = list(User.objects.filter(Q(identification=ident) | Q(correo__iexact=ident))[:2])
    if len(users) != 1:
        print("ERROR: no se encontro un unico usuario con ese dato. Nada se modifico.")
        return
    user = users[0]
    if not user.is_active:
        print("ERROR: el usuario esta inactivo. Nada se modifico.")
        return

    memberships = list(
        FarmMembership.objects.filter(
            user=user, status=MembershipStatus.ACTIVE, role__in=IOT_MANAGE_ROLES, farm__is_active=True
        ).select_related("farm").order_by("farm_id")
    )
    if not memberships:
        print("ERROR: el usuario no es ADMINISTRADOR de ninguna finca activa. Nada se modifico.")
        return
    print("\nFincas donde el usuario es ADMINISTRADOR:")
    for m in memberships:
        print(f"  ID {m.farm_id}: {m.farm.name}")
    raw = input("\nID de la finca a usar: ").strip()
    if not raw.isdigit() or int(raw) not in {m.farm_id for m in memberships}:
        print("ERROR: ID no valido. Nada se modifico.")
        return
    farm_id = int(raw)

    if (
        Gateway.objects.filter(farm_id=farm_id, is_active=True).exists()
        or Coordinator.objects.filter(farm_id=farm_id, is_active=True).exists()
    ):
        print("ERROR: la finca ya tiene gateways o coordinadores activos. "
              "Se aborta para no duplicar. Nada se modifico.")
        return

    print(f"""
Se creara en la finca {farm_id}:
  - 1 gateway 'Gateway MVP' + credencial interna (no se muestra)
  - 1 coordinador 'Coordinador MVP' + credencial HMAC (se muestra una vez)
  - autorizacion coordinador -> gateway
  - fuentes: BME680 (primary), DHT11 (backup_comparison)
  - sensores: canal 1 temperatura + 2 humedad (BME680), 3 temperatura + 4 humedad (DHT11)
""")
    if input("Escriba CONFIRMAR para continuar: ").strip() != "CONFIRMAR":
        print("Cancelado. Nada se modifico.")
        return

    step = "inicio"
    try:
        with transaction.atomic():
            step = "gateway"
            gw = services.create_gateway(user=user, farm_id=farm_id, name="Gateway MVP")
            step = "credencial gateway"
            services.issue_gateway_credential(user=user, farm_id=farm_id, gateway_uid=gw.uid)
            step = "coordinador"
            co = services.create_coordinator(user=user, farm_id=farm_id, name="Coordinador MVP")
            step = "credencial coordinador"
            issued = services.issue_coordinator_credential(
                user=user, farm_id=farm_id, coordinator_uid=co.uid
            )
            step = "autorizacion"
            services.authorize_gateway(
                user=user, farm_id=farm_id, coordinator_uid=co.uid, gateway_uid=gw.uid
            )
            step = "fuentes"
            sources = {
                "BME680": services.create_sensor_source(
                    user=user, farm_id=farm_id, coordinator_uid=co.uid, name="BME680",
                    role="primary", model="BME680", manufacturer="Bosch",
                    description="Sensor primario (canales 1 y 2)",
                ),
                "DHT11": services.create_sensor_source(
                    user=user, farm_id=farm_id, coordinator_uid=co.uid, name="DHT11",
                    role="backup_comparison", model="DHT11", manufacturer="Aosong",
                    description="Sensor de respaldo (canales 3 y 4)",
                ),
            }
            created = []
            for channel, metric, src in SENSORS:
                step = f"sensor canal {channel}"
                sensor = services.create_sensor(
                    user=user, farm_id=farm_id, coordinator_uid=co.uid,
                    channel=channel, metric=metric, source_uid=sources[src].uid,
                )
                created.append((channel, metric, src, sensor.uid))
    except AppError as exc:
        print(f"\nERROR en '{step}': {exc.message} (HTTP {exc.status_code}). "
              "Se revirtio todo; nada se modifico.")
        return
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR en '{step}': {type(exc).__name__}. Se revirtio todo; nada se modifico.")
        return

    print("\n=== LISTO ===")
    print(f"Gateway UID:     {gw.uid}   <- compartir para F4 (ACL Flespi)")
    print(f"Coordinador UID: {co.uid}")
    for channel, metric, src, uid in created:
        print(f"  canal {channel}: {metric:<18} {src:<6} sensor {uid}")
    print("\n--- LINEA PARA EL COORDINADOR (F8). NO la pegue en chats. ---")
    print(f"SET {co.uid} {issued.credential.key_version} {issued.provisioning_key()}")
    print("--- Guardela en un lugar seguro: no se puede volver a mostrar. ---\n")


_run()
