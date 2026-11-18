"""Capacidades calculadas por finca y por cuenta.

Propósito: exponer a los clientes lo que la cuenta puede hacer, derivado en el
servidor de las mismas reglas que aplican los servicios (BUILD F6, DEC-11).
Contexto: las capacidades son informativas para la interfaz. La autorización
real sigue en cada servicio (update_farm, deactivate_farm, reactivate_farm,
list_farm_events, _admin_actor_for_membership_op, invitation_services._require_admin,
apps.inventario.services.membership y apps.iot.services.access). Nunca se
aceptan desde el cliente.
INT-02: los permisos de inventario e IoT se leen de la fuente única de
permisos (``apps.farms.permissions``: rol + permisos directos ``FarmPermission``).
"""
from apps.accounts.models import User
from apps.accounts.services.access import puede_crear_finca
from apps.farms import permissions as farm_permissions
from apps.farms.models import FarmMembership, FarmRole, MembershipStatus
from apps.trazabilidad.models import AuditEvent, EventType


def _roles_with(permission: str) -> frozenset:
    """Roles cuyo permiso base incluye ``permission`` (sin permisos directos)."""
    return frozenset(
        role for role, granted in farm_permissions.PERMISOS_POR_ROL.items() if permission in granted
    )


# Compatibilidad: roles que traen el permiso por defecto. Se derivan de la
# matriz de permisos; la autorización real usa ``farm_permissions.tiene_permiso``
# (rol + permisos directos).
INVENTORY_WRITE_ROLES = _roles_with(farm_permissions.REGISTRAR_MOVIMIENTOS)
IOT_MANAGE_ROLES = _roles_with(farm_permissions.GESTIONAR_IOT)

CAPABILITY_KEYS = (
    "can_edit_farm",
    "can_deactivate_farm",
    "can_reactivate_farm",
    "can_view_history",
    "can_manage_members",
    "can_manage_invitations",
    "can_read_inventory",
    "can_write_inventory",
    "can_read_galpon",
    "can_write_galpon",
    "can_read_iot",
    "can_manage_iot",
)


def farm_capabilities(membership: FarmMembership) -> dict[str, bool]:
    """Calcula las capacidades de la cuenta en la finca de la membresía."""
    active_member = membership.status == MembershipStatus.ACTIVE
    farm_active = membership.farm.is_active
    account_active = membership.user.is_active
    is_admin = active_member and membership.role == FarmRole.ADMINISTRADOR
    manages = is_admin and farm_active and account_active
    operates = active_member and farm_active
    granted = farm_permissions.permisos_efectivos(membership) if active_member else set()
    return {
        "can_edit_farm": is_admin and farm_active,
        "can_deactivate_farm": is_admin and farm_active,
        "can_reactivate_farm": is_admin and not farm_active and account_active,
        "can_view_history": is_admin,
        "can_manage_members": manages,
        "can_manage_invitations": manages,
        "can_read_inventory": operates and farm_permissions.VER_INVENTARIO in granted,
        "can_write_inventory": (
            operates and farm_permissions.REGISTRAR_MOVIMIENTOS in granted
        ),
        "can_read_galpon": operates and farm_permissions.VER_GALPON in granted,
        "can_write_galpon": operates and farm_permissions.REGISTRAR_GALPON in granted,
        "can_read_iot": operates and farm_permissions.VER_IOT in granted,
        "can_manage_iot": (
            operates and account_active and farm_permissions.GESTIONAR_IOT in granted
        ),
    }


def can_create_farms(user: User) -> bool:
    """Capacidad global de crear fincas (no depende de ninguna membresía).

    Delega en la regla única ``accounts.services.access.puede_crear_finca``
    (INT-02): roles de plataforma o bandera con correo verificado.
    """
    return puede_crear_finca(user)


def first_farm_available(user: User) -> bool:
    """Indica si la cuenta conserva la excepción de primera finca (F10.2).

    Se deriva del historial persistente, sin bandera nueva. La excepción ya no
    está disponible si la cuenta:
    - tiene o tuvo una membresía de administrador en cualquier estado (cubre
      las fincas creadas antes de que existiera la auditoría de creación);
    - figura como autora de un evento ``farm_created``;
    - recibió o perdió la capacidad por cualquier vía (eventos
      ``farm_creation_granted`` o ``farm_creation_revoked``), de modo que un
      retiro explícito con ``revoke_farm_creation`` no se elude.
    Las membresías no se borran (estados lógicos) y AuditEvent es de solo
    inserción, así que la respuesta no puede volver a ser True.
    """
    if FarmMembership.objects.filter(user=user, role=FarmRole.ADMINISTRADOR).exists():
        return False
    if AuditEvent.objects.filter(event_type=EventType.FARM_CREATED, actor=user).exists():
        return False
    return not AuditEvent.objects.filter(
        event_type__in=(EventType.FARM_CREATION_GRANTED, EventType.FARM_CREATION_REVOKED),
        entity_type="User",
        entity_id=str(user.pk),
    ).exists()


def can_create_first_farm(user: User) -> bool:
    """Puede crear su primera finca por la excepción de onboarding (F10.2).

    Solo aplica a cuentas activas y verificadas que aún no tienen la capacidad
    global; consultarla no concede nada.
    """
    # INT-02b: la excepción de primera finca queda desactivada; solo el
    # superadmin habilita la creación nombrando administradores.
    return False


def can_open_inactive_farms(user: User) -> bool:
    """Indica si la cuenta administra alguna finca, activa o desactivada.

    Habilita en la interfaz "Abrir por identificador", que sirve para que un
    administrador consulte o reactive fincas desactivadas (no listadas).
    """
    return FarmMembership.objects.filter(
        user=user,
        status=MembershipStatus.ACTIVE,
        role=FarmRole.ADMINISTRADOR,
    ).exists()
