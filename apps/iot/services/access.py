"""Acceso a finca para leer y administrar IoT (IoT-02.2, DEC-13).

Propósito: única puerta de autorización de los servicios IoT, para no repetir
reglas en cada operación.

- Lectura: membresía activa en finca activa (``can_read_iot``).
- Administración: bloqueo de ``Farm`` y de la membresía
  (``lock_active_membership``), rol en ``IOT_MANAGE_ROLES`` (solo
  administrador) con cuenta activa (``can_manage_iot``) y, después,
  ``require_mariadb()``.

Orden de una escritura IoT (IoT-02.2-R2):
1. Autorización: membresía activa, finca activa y rol administrador. Un no
   miembro recibe 404 y un integrante sin rol, 403, aunque envíe datos
   inválidos o el motor no sea MariaDB.
2. Motor: ``require_mariadb()``. Antes solo se han ejecutado lecturas y
   ``SELECT ... FOR UPDATE`` sobre la finca y la membresía, sin escrituras.
3. Validación de los datos recibidos.
4. Operación y auditoría en la misma transacción.

Orden de bloqueo: Farm → FarmMembership → Coordinator → Gateway (varios
gateways, por PK ascendente) → filas dependientes (sensores y autorizaciones).
El bloqueo de ``Farm`` serializa las escrituras IoT de una misma finca; los
bloqueos de dispositivos revalidan su estado.
"""
from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.farms import services as farm_services
from apps.farms.capabilities import IOT_MANAGE_ROLES
from apps.farms.models import FarmMembership
from apps.iot.db_guard import require_mariadb

IOT_MANAGE_FORBIDDEN = "no tienes permiso para administrar dispositivos IoT"


def require_read_membership(*, user: User, farm_id: int) -> FarmMembership:
    """Exige membresía activa en una finca activa para leer IoT."""
    return farm_services.require_active_membership(user=user, farm_id=farm_id)


def lock_manage_membership(*, user: User, farm_id: int) -> FarmMembership:
    """Autoriza la administración IoT y después exige MariaDB.

    Debe invocarse dentro de ``transaction.atomic()`` y antes de validar los
    datos de la operación.
    """
    membership = farm_services.lock_active_membership(user=user, farm_id=farm_id)
    if membership.role not in IOT_MANAGE_ROLES or not user.is_active:
        raise AppError(IOT_MANAGE_FORBIDDEN, 403)
    require_mariadb()
    return membership
