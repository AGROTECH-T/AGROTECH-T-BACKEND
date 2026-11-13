"""Asignación de roles y permisos por finca.

Propósito: listar a las personas de las fincas que administra el usuario,
cambiar su rol, sumar permisos directos, activar o inactivar la cuenta y crear
personas nuevas con su rol inicial.
Contexto: el rol pertenece a la membresía; el permiso directo solo suma.
@author Cristian Deysdayr Jimenez
"""
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.db.models import Q
from apps.accounts.errors import AppError
from apps.accounts.models import Session, User
from apps.farms import permissions as farm_permissions
from apps.farms.models import Farm, FarmMembership, FarmPermission, FarmRole
def listar_personas(actor: User, search: str = "") -> dict:
    """Devuelve las personas de las fincas donde el actor es administrador."""
    farm_ids = _fincas_administradas(actor)
    if not farm_ids:
        return {"puedo_asignar": False, "fincas": [], "usuarios": []}
    return {
        "puedo_asignar": True,
        "fincas": list(
            Farm.objects.filter(id__in=farm_ids).values("id", "name").order_by("id")
        ),
        "usuarios": _agrupar(actor, farm_ids, search=search),
    }
def detalle_persona(*, actor: User, user_id: int) -> dict:
    """Ficha completa: rol por finca, permisos del rol y permisos directos."""
    farm_ids = _fincas_administradas(actor)
    if not farm_ids:
        raise AppError("solo el administrador puede ver los permisos", 403)
    person = _persona(actor, farm_ids, user_id)
    return _ficha_detalle(actor, person, farm_ids)
def _fincas_administradas(actor: User) -> list[int]:
    """Identificadores de fincas donde el actor es administrador."""
    return list(
        FarmMembership.objects.filter(
            user=actor,
            role=FarmRole.ADMINISTRADOR,
        ).values_list("farm_id", flat=True)
    )
def _persona(actor: User, farm_ids: list[int], user_id: int) -> User:
    """Cuenta visible para el actor o error 404."""
    person = (
        User.objects.filter(id=user_id, farm_memberships__farm_id__in=farm_ids)
        .distinct()
        .first()
    )
    if person is None:
        raise AppError("esa persona no pertenece a tus fincas", 404)
    return person
def _agrupar(
    actor: User,
    farm_ids: list[int],
    *,
    search: str = "",
) -> list[dict]:
    """Arma una ficha por persona con sus membresías visibles."""
    consulta = FarmMembership.objects.filter(farm_id__in=farm_ids).select_related("user", "farm")
    if search:
        consulta = consulta.filter(
            Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(user__identification__icontains=search)
            | Q(user__correo__icontains=search)
        )
    personas: dict[int, dict] = {}
    for membership in consulta.order_by("user__first_name", "user__last_name", "farm_id"):
        ficha = personas.setdefault(membership.user_id, _ficha(actor, membership.user))
        ficha["membresias"].append(
            {
                "farm_id": membership.farm_id,
                "farm_name": membership.farm.name,
                "role": membership.role,
            }
        )
    return list(personas.values())
def _ficha(actor: User, person: User) -> dict:
    """Datos públicos de la cuenta y sus membresías, todavía vacías."""
    return {
        "id": person.id,
        "nombre": f"{person.first_name} {person.last_name}".strip(),
        "identification": person.identification,
        "correo": person.correo,
        "activo": person.is_active,
        "es_yo": person.id == actor.id,
        "membresias": [],
    }
def _ficha_detalle(actor: User, person: User, farm_ids: list[int]) -> dict:
    """Ficha con el rol y los permisos de cada finca administrada."""
    ficha = _ficha(actor, person)
    for membership in (
        FarmMembership.objects.filter(user=person, farm_id__in=farm_ids)
        .select_related("farm")
        .order_by("farm_id")
    ):
        directos = farm_permissions.permisos_directos(membership)
        efectivos = farm_permissions.permisos_efectivos(membership)
        ficha["membresias"].append(
            {
                "farm_id": membership.farm_id,
                "farm_name": membership.farm.name,
                "role": membership.role,
                "permisos_efectivos": _describir(efectivos),
                "permisos_directos": _describir(directos),
            }
        )
    return ficha
def _describir(permisos: set[str]) -> list[dict]:
    """Convierte códigos en filas con nombre y módulo, en orden estable."""
    filas = []
    for permiso in farm_permissions.PERMISOS_CATALOGO:
        if permiso["id"] in permisos:
            filas.append(permiso)
    conocidos = {fila["id"] for fila in filas}
    for permiso in permisos - conocidos:
        filas.append(
            {
                "id": permiso,
                "nombre": farm_permissions.NOMBRES_PERMISO.get(permiso, permiso),
                "modulo": "Otros",
            }
        )
    return filas
_IDS_ASIGNABLES = {
    permiso["id"] for permiso in farm_permissions.PERMISOS_ASIGNABLES
}
