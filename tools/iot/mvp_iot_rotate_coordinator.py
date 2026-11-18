"""MVP-IOT-F8 - Rota la credencial HMAC del coordinador del MVP.

Uso (carpeta del backend, venv activo):
    python manage.py shell -c "exec(open('tools/iot/mvp_iot_rotate_coordinator.py', encoding='utf-8').read())"

Revoca la clave vigente de CO-7Q89YZ9MXC (expuesta) y emite otra en la misma
transaccion (services.rotate_coordinator_credential). Muestra UNA vez la nueva
linea SET para cargarla en el coordinador. No la guarde en el repositorio ni la
pegue en chats.
"""
from django.db.models import Q

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.iot import services

COORDINATOR_UID = "CO-7Q89YZ9MXC"
FARM_ID = 4


def _run():
    print(f"\n=== MVP-IOT-F8: rotar clave de {COORDINATOR_UID} (finca {FARM_ID}) ===\n")
    ident = input("Documento o correo del usuario administrador: ").strip()
    users = list(User.objects.filter(Q(identification=ident) | Q(correo__iexact=ident))[:2])
    if len(users) != 1:
        print("ERROR: no se encontro un unico usuario. Nada se modifico.")
        return
    print("\nLa clave actual dejara de funcionar de inmediato y se emitira una nueva.")
    if input("Escriba CONFIRMAR para continuar: ").strip() != "CONFIRMAR":
        print("Cancelado. Nada se modifico.")
        return
    try:
        issued = services.rotate_coordinator_credential(
            user=users[0], farm_id=FARM_ID, coordinator_uid=COORDINATOR_UID
        )
    except AppError as exc:
        print(f"\nERROR: {exc.message} (HTTP {exc.status_code}). Nada se modifico.")
        return
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: {type(exc).__name__}. Nada se modifico.")
        return
    print("\n=== LISTO ===")
    print("--- NUEVA LINEA PARA EL COORDINADOR. NO la pegue en chats. ---")
    print(f"SET {COORDINATOR_UID} {issued.credential.key_version} {issued.provisioning_key()}")
    print("--- Copiela ahora: no se puede volver a mostrar. ---\n")


_run()
