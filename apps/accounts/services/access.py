"""Acceso a módulos según el rol de plataforma y la membresía.

Propósito: decir qué puede ver cada cuenta después del login.
Contexto: una cuenta recién registrada solo ve Inicio hasta que el superadmin
la nombre administradora, o un administrador la cree como trabajador.
@author Cristian Deysdayr Jimenez
"""
from apps.accounts.models import User
from apps.farms.models import FarmRole, MembershipStatus

HOME = "home"
FINCAS = "fincas"
INVENTARIO = "inventario"
GALPON = "galpon"
IOT = "iot"
CONFIGURACION = "configuracion"

MODULOS_COMPLETOS = [HOME, FINCAS, INVENTARIO, GALPON, IOT, CONFIGURACION]


def puede_crear_finca(user: User) -> bool:
    """Regla única para crear fincas (INT-02).

    Puede crear fincas una cuenta activa que sea superadmin, o administrador
    de plataforma nombrado por el superadmin con el correo verificado
    (INT-02b). Una cuenta registrada no crea fincas hasta ser nombrada.

    @param user Cuenta autenticada.
    @returns True si puede crear fincas.
    """
    if not user.is_active:
        return False
    if user.is_superadmin:
        return True
    return bool(user.is_administrador and user.email_verified)


def modulos_visibles(user: User) -> list[str]:
    """Secciones del menú que esa cuenta puede abrir.

    Plataforma (superadmin / administrador) ve todo. Un trabajador solo ve los
    módulos que le conceden los permisos efectivos de sus fincas activas
    (``apps.farms.permissions``), no por el mero hecho de tener membresía.

    @param user Cuenta autenticada.
    @returns Identificadores de módulo en el orden del menú.
    """
    if user.is_superadmin or user.is_administrador:
        return list(MODULOS_COMPLETOS)
    memberships = list(
        user.farm_memberships.filter(status=MembershipStatus.ACTIVE)
        .select_related("farm", "user")
        .prefetch_related("extra_permissions")
    )
    if not memberships:
        return [HOME]

    # Fuente única operativa: misma matriz que capacidades e inventario/IoT.
    from apps.farms import permissions as farm_permissions

    granted: set[str] = set()
    for membership in memberships:
        if membership.farm.is_active:
            granted |= farm_permissions.permisos_efectivos(membership)

    modulos = [HOME, FINCAS]
    if farm_permissions.VER_INVENTARIO in granted:
        modulos.append(INVENTARIO)
    if farm_permissions.VER_GALPON in granted:
        modulos.append(GALPON)
    if farm_permissions.VER_IOT in granted:
        modulos.append(IOT)
    if any(membership.role == FarmRole.ADMINISTRADOR for membership in memberships):
        modulos.append(CONFIGURACION)
    return modulos


def cuenta_publica(user: User) -> dict:
    """Datos visibles de la sesión, con banderas y módulos permitidos."""
    return {
        "id": user.pk,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "identification": user.identification,
        "phone": user.phone,
        "correo": user.correo,
        "is_active": user.is_active,
        "is_superadmin": user.is_superadmin,
        "is_administrador": user.is_administrador,
        "modulos": modulos_visibles(user),
        **_capacidades(user),
    }


def _capacidades(user: User) -> dict:
    """Capacidades de creación de fincas expuestas a la interfaz (F10.2)."""
    # Import diferido: farms.capabilities depende de este módulo.
    from apps.farms.capabilities import (
        can_create_farms,
        can_create_first_farm,
        can_open_inactive_farms,
    )

    return {
        "email_verified": user.email_verified,
        "can_create_farms": can_create_farms(user),
        "can_create_first_farm": can_create_first_farm(user),
        "can_open_inactive_farms": can_open_inactive_farms(user),
    }
