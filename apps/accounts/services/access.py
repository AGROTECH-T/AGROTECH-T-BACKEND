"""Acceso a módulos según el rol de plataforma y la membresía.

Propósito: decir qué puede ver cada cuenta después del login.
Contexto: una cuenta recién registrada solo ve Inicio hasta que el superadmin
la nombre administradora, o un administrador la cree como trabajador.
@author Cristian Deysdayr Jimenez
"""
from apps.accounts.models import User
from apps.farms.models import FarmRole

HOME = "home"
FINCAS = "fincas"
INVENTARIO = "inventario"
GALPON = "galpon"
CONFIGURACION = "configuracion"

MODULOS_COMPLETOS = [HOME, FINCAS, INVENTARIO, GALPON, CONFIGURACION]


def puede_crear_finca(user: User) -> bool:
    """El superadmin y el administrador nombrado pueden abrir fincas.

    @param user Cuenta autenticada.
    @returns True si puede crear fincas.
    """
    return user.is_superadmin or user.is_administrador


def modulos_visibles(user: User) -> list[str]:
    """Secciones del menú que esa cuenta puede abrir.

    @param user Cuenta autenticada.
    @returns Identificadores de módulo en el orden del menú.
    """
    if user.is_superadmin or user.is_administrador:
        return list(MODULOS_COMPLETOS)
    memberships = list(user.farm_memberships.all())
    if not memberships:
        return [HOME]
    modulos = [HOME, FINCAS, INVENTARIO, GALPON]
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
    }
