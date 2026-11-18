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

    Puede crear fincas una cuenta activa que sea superadmin o administrador de
    plataforma, o que tenga la capacidad concedida ``can_create_farms`` con el
    correo verificado (DEC-11, F10.2).

    @param user Cuenta autenticada.
    @returns True si puede crear fincas.
    """
    if not user.is_active:
        return False
    if user.is_superadmin or user.is_administrador:
        return True
    return bool(user.can_create_farms and user.email_verified)


def modulos_visibles(user: User) -> list[str]:
    """Secciones del menú que esa cuenta puede abrir.

    @param user Cuenta autenticada.
    @returns Identificadores de módulo en el orden del menú.
    """
    if user.is_superadmin or user.is_administrador:
        return list(MODULOS_COMPLETOS)
    memberships = list(user.farm_memberships.filter(status=MembershipStatus.ACTIVE))
    if not memberships:
        return [HOME]
    modulos = [HOME, FINCAS, INVENTARIO, GALPON, IOT]
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
