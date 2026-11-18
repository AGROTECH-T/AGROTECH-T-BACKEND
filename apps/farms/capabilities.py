"""Capacidades calculadas por finca y por cuenta.

Propósito: exponer a los clientes lo que la cuenta puede hacer, derivado en el
servidor de las mismas reglas que aplican los servicios (BUILD F6, DEC-11).
Contexto: las capacidades son informativas para la interfaz. La autorización
real sigue en cada servicio (update_farm, deactivate_farm, reactivate_farm,
list_farm_events, _admin_actor_for_membership_op, invitation_services._require_admin,
apps.inventario.services.membership y apps.iot.services.access). Nunca se
aceptan desde el cliente.
Los permisos granulares de apps.permissions no intervienen (decisión B3).
"""
from apps.accounts.models import User
from apps.accounts.services.access import puede_crear_finca
from apps.farms.models import FarmMembership, FarmRole, MembershipStatus
from apps.trazabilidad.models import AuditEvent, EventType

# Roles que pueden escribir inventario; fuente única también para
# apps.inventario.services.membership.
INVENTORY_WRITE_ROLES = frozenset({FarmRole.ADMINISTRADOR, FarmRole.OPERADOR})

# Roles que administran dispositivos IoT (DEC-13, IoT-02.2); fuente única
# también para apps.iot.services.access.
IOT_MANAGE_ROLES = frozenset({FarmRole.ADMINISTRADOR})

CAPABILITY_KEYS = (
    "can_edit_farm",
    "can_deactivate_farm",
    "can_reactivate_farm",
    "can_view_history",
    "can_manage_members",
    "can_manage_invitations",
    "can_read_inventory",
    "can_write_inventory",
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
    reads_inventory = active_member and farm_active
    return {
        "can_edit_farm": is_admin and farm_active,
        "can_deactivate_farm": is_admin and farm_active,
        "can_reactivate_farm": is_admin and not farm_active and account_active,
        "can_view_history": is_admin,
        "can_manage_members": manages,
        "can_manage_invitations": manages,
        "can_read_inventory": reads_inventory,
        "can_write_inventory": reads_inventory and membership.role in INVENTORY_WRITE_ROLES,
        "can_read_iot": reads_inventory,
        "can_manage_iot": (
            reads_inventory and account_active and membership.role in IOT_MANAGE_ROLES
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
    return bool(
        user.is_active
        and user.email_verified
        and not can_create_farms(user)
        and first_farm_available(user)
    )


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
