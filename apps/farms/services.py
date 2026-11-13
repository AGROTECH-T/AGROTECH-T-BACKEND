"""Casos de uso del dominio de fincas.

Propósito: crear, consultar, actualizar y desactivar fincas con membresía.
Contexto: el usuario autenticado se resuelve fuera del servicio.
"""
from datetime import timedelta

from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.farms.capabilities import first_farm_available
from apps.farms.models import Farm, FarmMembership, FarmRole, MembershipStatus
from apps.trazabilidad.models import AuditEvent, EventType
from apps.trazabilidad.services import record_audit_event

FARM_CREATION_FORBIDDEN = "tu cuenta no tiene permiso para crear fincas"
FARM_CREATION_EMAIL_UNVERIFIED = "verifica tu correo para crear fincas"
FIRST_FARM_REASON = "primera finca creada desde el onboarding"
FIRST_FARM_SOURCE = "first_farm_onboarding"
# Ventana en la que una solicitud idéntica a la de la primera finca se trata
# como reintento de esa misma operación de onboarding (F10.2-CORRECCIONES).
FIRST_FARM_REPLAY_WINDOW = timedelta(minutes=10)

PERMISSION_CLEANUP_SETTING = "FARMS_PERMISSION_CLEANUP"
PERMISSION_CLEANUP_REQUIRED = "required"
PERMISSION_CLEANUP_DISABLED = "disabled"
PERMISSIONS_APP = "apps.permissions"


def _permission_cleanup_enabled() -> bool:
    """Valida ``FARMS_PERMISSION_CLEANUP`` frente a la instalación de Permissions.

    La ausencia de la opción equivale a ``required``. ``disabled`` solo es
    válido cuando Permissions no está instalada (configuración aislada F2-2).
    Cualquier combinación incoherente o valor desconocido falla explícitamente.
    """
    mode = getattr(settings, PERMISSION_CLEANUP_SETTING, PERMISSION_CLEANUP_REQUIRED)
    installed = django_apps.is_installed(PERMISSIONS_APP)
    if mode == PERMISSION_CLEANUP_REQUIRED:
        if not installed:
            raise ImproperlyConfigured(
                f"{PERMISSION_CLEANUP_SETTING}='required' exige {PERMISSIONS_APP} "
                "en INSTALLED_APPS."
            )
        return True
    if mode == PERMISSION_CLEANUP_DISABLED:
        if installed:
            raise ImproperlyConfigured(
                f"{PERMISSION_CLEANUP_SETTING}='disabled' no se admite con "
                f"{PERMISSIONS_APP} instalada."
            )
        return False
    raise ImproperlyConfigured(
        f"{PERMISSION_CLEANUP_SETTING} debe ser 'required' o 'disabled'."
    )


def _clear_membership_permissions(*, membership: FarmMembership, actor, cause: str) -> None:
    """Limpia asignaciones y restricciones si la configuración lo exige.

    Debe invocarse dentro de la transacción del llamador, después de bloquear
    ``Farm`` y la membresía y de completar las validaciones de negocio. La
    importación es diferida para no acoplar el arranque de Farms a Permissions.
    """
    if not _permission_cleanup_enabled():
        return
    from apps.permissions.services import clear_membership_permissions

    clear_membership_permissions(membership=membership, actor=actor, cause=cause)


def create_farm(*, user: User, name: str) -> FarmMembership:
    """Crea una finca y la membresía administradora en una sola transacción.

    Exige cuenta activa y correo verificado. Con la capacidad global
    ``can_create_farms`` (DEC-11) crea sin más; sin ella, solo si la cuenta
    conserva la excepción de primera finca (F10.2), y en ese caso concede la
    capacidad dentro de la misma transacción. Ser administrador de otra finca
    no basta.

    La cuenta se bloquea primero, así que las solicitudes de una misma cuenta
    se serializan. Un reintento de la operación de onboarding (mismo nombre,
    dentro de FIRST_FARM_REPLAY_WINDOW y sin otras fincas creadas después)
    devuelve la finca ya creada en lugar de duplicarla; ver
    ``_first_farm_replay``.
    """
    with transaction.atomic():
        account = User.objects.select_for_update().get(pk=user.pk)
        _require_creator(account)
        first_farm = _authorize_farm_creation(account)
        validated_name = _validate_farm_name(name)
        if not first_farm:
            replay = _first_farm_replay(account, validated_name)
            if replay is not None:
                return replay
        farm = Farm.objects.create(name=validated_name)
        membership = _create_owner_membership(farm, account)
        record_audit_event(
            farm=farm,
            actor=account,
            event_type=EventType.FARM_CREATED,
            entity_type="farm",
            entity_id=farm.pk,
            after={"name": farm.name},
        )
        if first_farm:
            _grant_first_farm_capability(account, farm)
        return membership


def _require_creator(account: User) -> None:
    """Exige cuenta activa y correo verificado (cuenta ya bloqueada)."""
    if not account.is_active:
        raise AppError(FARM_CREATION_FORBIDDEN, 403)
    if not account.email_verified:
        raise AppError(FARM_CREATION_EMAIL_UNVERIFIED, 403)


def _first_farm_replay(account: User, name: str) -> FarmMembership | None:
    """Devuelve la primera finca si la solicitud repite esa operación.

    Se considera reintento de la misma operación de onboarding cuando:
    - la cuenta recibió la capacidad por la primera finca hace menos de
      FIRST_FARM_REPLAY_WINDOW (evento ``farm_creation_granted`` con
      ``execution_source = first_farm_onboarding``);
    - el nombre validado es idéntico al de esa finca;
    - la cuenta sigue siendo su administradora activa, la finca está activa y
      no administra ninguna otra finca (no creó fincas adicionales después).
    Cubre el doble envío simultáneo (la cuenta bloqueada serializa ambos) y el
    reintento tras perder la respuesta. No usa esquema nuevo: se apoya en la
    auditoría de solo inserción. Una creación adicional con otro nombre, o
    fuera de la ventana, no se considera reintento.
    """
    since = timezone.now() - FIRST_FARM_REPLAY_WINDOW
    grants = AuditEvent.objects.filter(
        event_type=EventType.FARM_CREATION_GRANTED,
        entity_type="User",
        entity_id=str(account.pk),
        created_at__gte=since,
    ).order_by("-created_at", "-id")
    grant = next(
        (
            event
            for event in grants
            if (event.metadata or {}).get("execution_source") == FIRST_FARM_SOURCE
        ),
        None,
    )
    if grant is None:
        return None
    admin_memberships = list(
        FarmMembership.objects.filter(
            user=account, role=FarmRole.ADMINISTRADOR, status=MembershipStatus.ACTIVE
        ).select_related("farm")[:2]
    )
    if len(admin_memberships) != 1:
        return None
    membership = admin_memberships[0]
    if membership.farm_id != grant.metadata.get("farm_id"):
        return None
    if not membership.farm.is_active or membership.farm.name != name:
        return None
    return membership


def _authorize_farm_creation(account: User) -> bool:
    """Decide la vía de creación sobre la cuenta bloqueada y ya validada.

    Returns:
        True si se usa la excepción de primera finca; False si la cuenta ya
        tiene la capacidad global.
    """
    if account.can_create_farms:
        return False
    if first_farm_available(account):
        return True
    raise AppError(FARM_CREATION_FORBIDDEN, 403)


def _grant_first_farm_capability(account: User, farm: Farm) -> None:
    """Concede ``can_create_farms`` al creador de su primera finca.

    Solo cambia la bandera global: no toca is_staff, is_superuser ni ningún
    rol. El evento queda en la misma transacción que la finca.
    """
    account.can_create_farms = True
    account.save(update_fields=["can_create_farms"])
    record_audit_event(
        farm=None,
        actor=account,
        event_type=EventType.FARM_CREATION_GRANTED,
        entity_type="User",
        entity_id=str(account.pk),
        before={"can_create_farms": False},
        after={"can_create_farms": True},
        reason=FIRST_FARM_REASON,
        metadata={"execution_source": FIRST_FARM_SOURCE, "farm_id": farm.pk},
    )


def _validate_farm_name(name: str) -> str:
    """Normaliza y valida el nombre antes de persistir la finca."""
    normalized = name.strip()
    if not normalized:
        raise AppError("el nombre de la finca es obligatorio", 400)

    max_length = Farm._meta.get_field("name").max_length
    if len(normalized) > max_length:
        raise AppError(
            f"el nombre de la finca no puede superar los {max_length} caracteres",
            400,
        )
    return normalized


def list_memberships(user: User):
    """Retorna las membresías activas del usuario con su finca precargada."""
    return (
        FarmMembership.objects.filter(
            user=user,
            status=MembershipStatus.ACTIVE,
            farm__is_active=True,
        )
        .select_related("farm")
        .order_by("farm_id")
    )


def get_membership(*, user: User, farm_id: int) -> FarmMembership | None:
    """Retorna la membresía del usuario en la finca o None si no pertenece."""
    return (
        FarmMembership.objects.filter(user=user, farm_id=farm_id)
        .select_related("farm")
        .first()
    )


def get_readable_membership(*, user: User, farm_id: int) -> FarmMembership | None:
    """Retorna la membresía visible según estado y rol."""
    membership = get_membership(user=user, farm_id=farm_id)
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        return None
    if not membership.farm.is_active and membership.role != FarmRole.ADMINISTRADOR:
        return None
    return membership


def list_farm_events(*, user: User, farm_id: int):
    """Retorna los eventos de auditoría de una finca; solo el administrador actual.

    Permite consultar fincas activas y desactivadas. No produce efectos
    secundarios. La autorización exige membresía con rol administrador.
    """
    membership = get_readable_membership(user=user, farm_id=farm_id)
    if membership is None or membership.role != FarmRole.ADMINISTRADOR:
        raise AppError("finca no encontrada", 404)
    return AuditEvent.objects.filter(farm_id=farm_id).order_by("-created_at", "-id")


def require_active_membership(*, user: User, farm_id: int) -> FarmMembership:
    """Exige membresía activa en una finca activa para operaciones de lectura."""
    membership = get_membership(user=user, farm_id=farm_id)
    if (
        membership is None
        or membership.status != MembershipStatus.ACTIVE
        or not membership.farm.is_active
    ):
        raise AppError("finca no encontrada", 404)
    return membership


def lock_active_membership(*, user: User, farm_id: int) -> FarmMembership:
    """Bloquea la finca y su membresía activa para una operación de escritura.

    Debe invocarse dentro de ``transaction.atomic()``. El orden de bloqueo es
    ``Farm`` y después ``FarmMembership``.
    """
    return _lock_membership(user=user, farm_id=farm_id, require_active=True)


def update_farm(*, user: User, farm_id: int, name: str) -> FarmMembership:
    """Actualiza el nombre de una finca activa; solo el administrador."""
    with transaction.atomic():
        membership = _lock_membership(
            user=user,
            farm_id=farm_id,
            require_active=True,
        )
        if membership.role != FarmRole.ADMINISTRADOR:
            raise AppError("solo el administrador puede hacer este cambio", 403)
        validated_name = _validate_farm_name(name)
        previous_name = membership.farm.name
        membership.farm.name = validated_name
        membership.farm.save(update_fields=["name", "updated_at"])
        record_audit_event(
            farm=membership.farm,
            actor=user,
            event_type=EventType.FARM_UPDATED,
            entity_type="farm",
            entity_id=membership.farm.pk,
            before={"name": previous_name},
            after={"name": validated_name},
        )
        return membership


def deactivate_farm(*, user: User, farm_id: int) -> Farm:
    """Desactiva una finca de forma idempotente; solo el administrador."""
    with transaction.atomic():
        membership = _lock_membership(
            user=user,
            farm_id=farm_id,
            require_active=False,
        )
        if membership.role != FarmRole.ADMINISTRADOR:
            raise AppError("solo el administrador puede hacer este cambio", 403)
        farm = membership.farm
        if farm.is_active:
            farm.is_active = False
            farm.deactivated_at = timezone.now()
            farm.deactivated_by = user
            farm.save(
                update_fields=[
                    "is_active",
                    "deactivated_at",
                    "deactivated_by",
                    "updated_at",
                ]
            )
            record_audit_event(
                farm=farm,
                actor=user,
                event_type=EventType.FARM_DEACTIVATED,
                entity_type="farm",
                entity_id=farm.pk,
                after={"is_active": False},
            )
        return farm


def reactivate_farm(*, user: User, farm_id: int) -> Farm:
    """Reactiva una finca desactivada; solo un administrador activo actual."""
    with transaction.atomic():
        membership = _lock_membership(
            user=user,
            farm_id=farm_id,
            require_active=False,
        )
        if membership.role != FarmRole.ADMINISTRADOR:
            raise AppError("solo el administrador puede hacer este cambio", 403)
        if not user.is_active:
            raise AppError("la cuenta no está activa", 403)
        farm = membership.farm
        if not farm.is_active:
            farm.is_active = True
            farm.deactivated_at = None
            farm.deactivated_by = None
            farm.save(
                update_fields=[
                    "is_active",
                    "deactivated_at",
                    "deactivated_by",
                    "updated_at",
                ]
            )
            record_audit_event(
                farm=farm,
                actor=user,
                event_type=EventType.FARM_REACTIVATED,
                entity_type="farm",
                entity_id=farm.pk,
                after={"is_active": True},
            )
        return farm


def delete_farm(*, user: User, farm_id: int) -> None:
    """Conserva el nombre histórico del caso de uso de baja lógica."""
    deactivate_farm(user=user, farm_id=farm_id)


def _lock_farm(*, farm_id: int) -> Farm:
    """Bloquea la fila de una finca existente o responde 404."""
    try:
        return Farm.objects.select_for_update().get(pk=farm_id)
    except Farm.DoesNotExist:
        raise AppError("finca no encontrada", 404) from None


def _lock_membership(
    *,
    user: User,
    farm_id: int,
    require_active: bool,
) -> FarmMembership:
    """Bloquea finca y membresía, y revalida estado y pertenencia.

    La lectura previa evita bloquear una finca ajena o inexistente. Después del
    bloqueo se vuelve a leer la membresía y se comprueba ``is_active`` sobre la
    fila bloqueada. El orden es siempre ``Farm`` y después ``FarmMembership``.
    """
    if get_membership(user=user, farm_id=farm_id) is None:
        raise AppError("finca no encontrada", 404)

    farm = _lock_farm(farm_id=farm_id)
    membership = (
        FarmMembership.objects.select_for_update()
        .filter(user=user, farm_id=farm_id)
        .first()
    )
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        raise AppError("finca no encontrada", 404)
    if require_active and not farm.is_active:
        raise AppError("finca no encontrada", 404)
    membership.farm = farm
    return membership


def _create_owner_membership(farm: Farm, user: User) -> FarmMembership:
    """Asigna el rol administrador al creador de la finca."""
    return FarmMembership.objects.create(
        farm=farm,
        user=user,
        role=FarmRole.ADMINISTRADOR,
    )


def _lock_farm_with_memberships(*, farm_id: int, membership_ids):
    """Bloquea la finca y las membresías indicadas, en orden por PK."""
    farm = _lock_farm(farm_id=farm_id)
    ordered = sorted({int(pk) for pk in membership_ids})
    rows = {
        row.pk: row
        for row in FarmMembership.objects.select_for_update()
        .filter(farm_id=farm_id, pk__in=ordered)
        .select_related("user")
        .order_by("pk")
    }
    return farm, rows


def _ensure_not_last_admin(*, farm_id: int, membership_pk: int) -> None:
    """Rechaza si membership_pk es el único administrador efectivo de la finca.

    Un administrador efectivo debe cumplir simultáneamente rol ADMINISTRADOR,
    estado ACTIVE y cuenta de usuario activa (``user.is_active=True``).
    """
    has_other = (
        FarmMembership.objects.filter(
            farm_id=farm_id,
            status=MembershipStatus.ACTIVE,
            role=FarmRole.ADMINISTRADOR,
            user__is_active=True,
        )
        .exclude(pk=membership_pk)
        .exists()
    )
    if not has_other:
        raise AppError("la finca debe conservar al menos un administrador", 409)


def _admin_actor_for_membership_op(*, user: User, farm_id: int, membership_id: int):
    """Bloquea finca y membresías (actor + objetivo) y retorna (farm, actor, target).

    El actor debe ser administrador activo; el objetivo debe existir y estar
    activo. El orden de bloqueo es Farm y luego las membresías por PK.
    """
    actor_membership = get_membership(user=user, farm_id=farm_id)
    if actor_membership is None:
        raise AppError("finca no encontrada", 404)
    farm, rows = _lock_farm_with_memberships(
        farm_id=farm_id,
        membership_ids=[actor_membership.pk, membership_id],
    )
    if not farm.is_active:
        raise AppError("finca no encontrada", 404)
    actor = rows.get(actor_membership.pk)
    if actor is None or actor.status != MembershipStatus.ACTIVE:
        raise AppError("finca no encontrada", 404)
    if actor.role != FarmRole.ADMINISTRADOR:
        raise AppError("solo el administrador puede hacer este cambio", 403)
    if not actor.user.is_active:
        raise AppError("la cuenta no está activa", 403)
    target = rows.get(membership_id)
    if target is None or target.status != MembershipStatus.ACTIVE:
        raise AppError("integrante no encontrado", 404)
    return farm, actor, target


def change_member_role(
    *,
    user: User,
    farm_id: int,
    membership_id: int,
    role: str,
) -> FarmMembership:
    """Cambia el rol de una membresía activa; solo el administrador."""
    if role not in FarmRole.values:
        raise AppError("rol no válido", 400)
    with transaction.atomic():
        farm, _actor, target = _admin_actor_for_membership_op(
            user=user,
            farm_id=farm_id,
            membership_id=membership_id,
        )
        if target.role == FarmRole.ADMINISTRADOR and role != FarmRole.ADMINISTRADOR:
            _ensure_not_last_admin(farm_id=farm_id, membership_pk=target.pk)
        if role == FarmRole.ADMINISTRADOR and not target.user.is_active:
            raise AppError("la cuenta no está activa", 403)
        previous_role = target.role
        target.role = role
        target.save(update_fields=["role", "updated_at"])
        record_audit_event(
            farm=farm,
            actor=user,
            event_type=EventType.MEMBER_ROLE_CHANGED,
            entity_type="FarmMembership",
            entity_id=str(target.pk),
            before={"role": previous_role},
            after={"role": role},
        )
        return target


def revoke_member(
    *,
    user: User,
    farm_id: int,
    membership_id: int,
    reason: str = "",
) -> FarmMembership:
    """Revoca una membresía activa; solo el administrador, sin auto-revocación."""
    with transaction.atomic():
        farm, actor, target = _admin_actor_for_membership_op(
            user=user,
            farm_id=farm_id,
            membership_id=membership_id,
        )
        if target.pk == actor.pk:
            raise AppError("no puedes revocarte a ti mismo", 400)
        if target.role == FarmRole.ADMINISTRADOR:
            _ensure_not_last_admin(farm_id=farm_id, membership_pk=target.pk)
        target.farm = farm
        _clear_membership_permissions(
            membership=target, actor=user, cause="member_revoked"
        )
        target.status = MembershipStatus.REVOKED
        target.revoked_at = timezone.now()
        target.revoked_by = user
        target.save(
            update_fields=["status", "revoked_at", "revoked_by", "updated_at"]
        )
        record_audit_event(
            farm=farm,
            actor=user,
            event_type=EventType.MEMBER_REVOKED,
            entity_type="FarmMembership",
            entity_id=str(target.pk),
            before={"status": MembershipStatus.ACTIVE},
            after={"status": MembershipStatus.REVOKED},
            reason=reason,
        )
        return target


def leave_farm(*, user: User, farm_id: int) -> FarmMembership:
    """Permite que el usuario abandone su propia finca; sin dejarla sin admin."""
    with transaction.atomic():
        membership = _lock_membership(
            user=user,
            farm_id=farm_id,
            require_active=True,
        )
        if membership.role == FarmRole.ADMINISTRADOR:
            _ensure_not_last_admin(farm_id=farm_id, membership_pk=membership.pk)
        _clear_membership_permissions(
            membership=membership, actor=user, cause="member_left"
        )
        membership.status = MembershipStatus.LEFT
        membership.left_at = timezone.now()
        membership.save(update_fields=["status", "left_at", "updated_at"])
        record_audit_event(
            farm=membership.farm,
            actor=user,
            event_type=EventType.MEMBER_LEFT,
            entity_type="FarmMembership",
            entity_id=str(membership.pk),
            before={"status": MembershipStatus.ACTIVE},
            after={"status": MembershipStatus.LEFT},
        )
        return membership


def transfer_admin(
    *,
    user: User,
    farm_id: int,
    membership_id: int,
) -> FarmMembership:
    """Transfiere la administración a otra membresía activa de forma atómica."""
    with transaction.atomic():
        farm, actor, target = _admin_actor_for_membership_op(
            user=user,
            farm_id=farm_id,
            membership_id=membership_id,
        )
        if target.pk == actor.pk:
            raise AppError("no puedes transferirte la administración a ti mismo", 400)
        if target.role == FarmRole.ADMINISTRADOR:
            raise AppError("el integrante ya es administrador", 400)
        if not target.user.is_active:
            raise AppError("la cuenta no está activa", 403)
        previous_target_role = target.role
        target.role = FarmRole.ADMINISTRADOR
        actor.role = FarmRole.OPERADOR
        target.save(update_fields=["role", "updated_at"])
        actor.save(update_fields=["role", "updated_at"])
        record_audit_event(
            farm=farm,
            actor=user,
            event_type=EventType.ADMIN_TRANSFERRED,
            entity_type="FarmMembership",
            entity_id=str(target.pk),
            before={
                "from_role": FarmRole.ADMINISTRADOR,
                "to_role": previous_target_role,
            },
            after={
                "from_role": FarmRole.OPERADOR,
                "to_role": FarmRole.ADMINISTRADOR,
            },
        )
        return target


def require_admin_membership(*, user: User, farm_id: int) -> FarmMembership:
    """Exige membresía activa con rol administrador en una finca activa."""
    membership = require_active_membership(user=user, farm_id=farm_id)
    if membership.role != FarmRole.ADMINISTRADOR:
        raise AppError("solo el administrador puede hacer este cambio", 403)
    return membership


def list_farm_members(
    *,
    user: User,
    farm_id: int,
    status_filter: str = MembershipStatus.ACTIVE,
    search: str = "",
):
    """Lista los integrantes de una finca; solo el administrador activo."""
    require_admin_membership(user=user, farm_id=farm_id)
    queryset = FarmMembership.objects.filter(farm_id=farm_id).select_related("user")
    if status_filter == "all":
        pass
    elif status_filter in MembershipStatus.values:
        queryset = queryset.filter(status=status_filter)
    else:
        raise AppError("filtro de estado no válido", 400)
    needle = search.strip()
    if needle:
        queryset = queryset.filter(
            Q(user__first_name__icontains=needle)
            | Q(user__last_name__icontains=needle)
        )
    return queryset.order_by("pk")


def get_farm_member(*, user: User, farm_id: int, membership_id: int) -> FarmMembership:
    """Retorna una membresía: el administrador ve todas; cada usuario la propia activa."""
    own = require_active_membership(user=user, farm_id=farm_id)
    if own.pk == membership_id:
        return own
    if own.role != FarmRole.ADMINISTRADOR:
        raise AppError("solo el administrador puede hacer este cambio", 403)
    target = (
        FarmMembership.objects.filter(farm_id=farm_id, pk=membership_id)
        .select_related("user")
        .first()
    )
    if target is None:
        raise AppError("integrante no encontrado", 404)
    return target


def list_member_history(*, user: User, farm_id: int, membership_id: int):
    """Retorna el historial de auditoría de una membresía; solo el administrador."""
    require_admin_membership(user=user, farm_id=farm_id)
    return AuditEvent.objects.filter(
        farm_id=farm_id,
        entity_type="FarmMembership",
        entity_id=str(membership_id),
    ).order_by("-created_at", "-id")
