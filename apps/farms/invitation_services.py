"""Servicios de invitaciones a fincas (FARMS-108).

Propósito: crear, listar, reenviar, cambiar el rol, cancelar y aceptar
invitaciones de un solo uso, con control de concurrencia, entrega segura y
trazabilidad.

Contexto: la identidad del usuario autenticado se resuelve fuera del servicio.
Los tokens nunca se persisten en claro y su entrega ocurre fuera de la
transacción, aplicando el resultado del proveedor únicamente si el nonce de
entrega sigue vigente.
"""
import hashlib
import re
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models.functions import Lower, Trim
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from apps.accounts.errors import AppError
from apps.accounts.integrations.delivery import DeliveryOutcome, deliver_email
from apps.accounts.models import User
from apps.farms import invitation_codes, invitation_mail
from apps.farms import services as farm_services
from apps.farms.models import (
    Farm,
    FarmInvitation,
    FarmMembership,
    FarmRole,
    InvitationStatus,
    MembershipStatus,
)
from apps.trazabilidad.models import EventType
from apps.trazabilidad.services import record_audit_event
from config.frontend_url import build_accept_link, normalize_frontend_url

INVITATION_TTL_DAYS = 7
INVITATION_NOT_FOUND = "invitación no encontrada"
INVITATION_DELIVERY_FAILED = "no se pudo entregar la invitación"
ALREADY_MEMBER = "ya perteneces a esta finca"
EMAIL_NOT_VERIFIED = "verifica tu correo para aceptar invitaciones"
MAX_PENDING_INVITATIONS = 50

# F10.3.2: colisiones de code_hash. MariaDB informa una clave duplicada con el
# error 1062 y el mensaje "Duplicate entry '<valor>' for key '<índice>'"; el
# índice UNIQUE de la columna se llama como ella. El mensaje incluye la huella,
# por eso nunca se propaga al agotar los intentos.
MAX_CODE_ATTEMPTS = 3
INVITATION_CODE_UNAVAILABLE = "no se pudo preparar la invitación; inténtalo de nuevo"
MYSQL_DUPLICATE_ENTRY = 1062
CODE_HASH_UNIQUE_INDEX = "code_hash"
_DUPLICATE_KEY_RE = re.compile(r"for key '([^']+)'\s*$")


def _normalize_email(value: str) -> str:
    """Normaliza una dirección de correo para comparar y guardar."""
    return value.strip().lower()


def _new_token() -> str:
    """Genera 256 bits aleatorios criptográficamente seguros."""
    return secrets.token_urlsafe(32)


def _hash_token(token: str) -> str:
    """Crea una huella SHA-256 no reversible del token."""
    return hashlib.sha256(token.encode()).hexdigest()


@sensitive_variables("code")
def _new_code(email: str) -> tuple[str, str]:
    """Genera un código nuevo y su huella ligada al correo.

    Returns:
        ``(codigo, huella)``. La huella se guarda en ``code_hash``. El código
        solo viaja en memoria hasta ``_finalize_delivery``: nunca se persiste,
        registra, devuelve en la API ni muestra al administrador (F10.3.3-A).
    """
    code, digest = invitation_codes.new_code_for(email)
    return code, digest


def _is_code_hash_collision(exc: IntegrityError) -> bool:
    """Indica si el IntegrityError es una clave duplicada en code_hash.

    Solo el error 1062 sobre el índice ``code_hash`` cuenta como colisión
    técnica; cualquier otro (por ejemplo ``uniq_farm_invitation_pending_email``)
    no se reintenta. MySQL 8 antepone la tabla al índice; MariaDB no.
    """
    args = getattr(exc, "args", ())
    if len(args) < 2 or args[0] != MYSQL_DUPLICATE_ENTRY:
        return False
    match = _DUPLICATE_KEY_RE.search(str(args[1]))
    if match is None:
        return False
    return match.group(1).rsplit(".", 1)[-1] == CODE_HASH_UNIQUE_INDEX


def _with_code_retry(operation):
    """Ejecuta ``operation`` en una transacción y la repite ante colisión de code_hash.

    Cada intento abre su propia transacción (o savepoint, si ya hay una
    exterior) y ``operation`` genera dentro un código y una huella nuevos. Una
    colisión revierte el intento completo, incluidos bloqueos y eventos de
    auditoría, así que no quedan estados parciales ni eventos duplicados. Tras
    ``MAX_CODE_ATTEMPTS`` colisiones responde con un error interno controlado
    que no incluye el mensaje de la base de datos.
    """
    for _attempt in range(MAX_CODE_ATTEMPTS):
        try:
            with transaction.atomic():
                return operation()
        except IntegrityError as exc:
            if not _is_code_hash_collision(exc):
                raise
    raise AppError(INVITATION_CODE_UNAVAILABLE, 500)


def _accept_link(token: str) -> str:
    """Arma el enlace de aceptación de un solo uso (formato heredado).

    Desde F10.3.3-A ningún correo nuevo lo incluye. Se conserva porque define
    el formato de los enlaces ya enviados, que siguen aceptándose hasta vencer,
    y se retirará en la etapa planificada de retirada del enlace.
    """
    base = normalize_frontend_url(
        getattr(settings, "FRONTEND_URL", ""), debug=settings.DEBUG
    )
    return build_accept_link(base, token)


def _verified_accounts(normalized_email: str):
    """Retorna las cuentas verificadas cuyo correo normalizado coincide."""
    return (
        User.objects.filter(email_verified=True)
        .annotate(normalized=Lower(Trim("correo")))
        .filter(normalized=normalized_email)
    )


def _require_admin(farm, user: User) -> FarmMembership:
    """Exige una membresía activa con rol administrador y cuenta activa."""
    membership = (
        FarmMembership.objects.select_for_update()
        .filter(farm_id=farm.pk, user=user)
        .select_related("user")
        .first()
    )
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        raise AppError("finca no encontrada", 404)
    if membership.role != FarmRole.ADMINISTRADOR:
        raise AppError("solo el administrador puede hacer este cambio", 403)
    if not membership.user.is_active:
        raise AppError("la cuenta no está activa", 403)
    return membership


def _lock_active_farm(farm_id: int):
    """Bloquea la finca y rechaza si está desactivada."""
    farm = farm_services._lock_farm(farm_id=farm_id)
    if not farm.is_active:
        raise AppError("finca no encontrada", 404)
    return farm


def _lock_pending_invitation(farm, invitation_id: int) -> FarmInvitation:
    """Bloquea una invitación de la finca y exige que siga pendiente."""
    invitation = (
        FarmInvitation.objects.select_for_update()
        .filter(farm=farm, pk=invitation_id)
        .first()
    )
    if invitation is None:
        raise AppError(INVITATION_NOT_FOUND, 404)
    if invitation.status != InvitationStatus.PENDING:
        raise AppError("la invitación ya no está pendiente", 409)
    return invitation


def _expire_lazy_farm(farm, now=None, exclude_pk=None) -> int:
    """Expira de forma perezosa las invitaciones pendientes vencidas."""
    now = now or timezone.now()
    with transaction.atomic():
        stale_qs = FarmInvitation.objects.filter(
            farm=farm,
            status=InvitationStatus.PENDING,
            expires_at__lte=now,
        )
        if exclude_pk is not None:
            stale_qs = stale_qs.exclude(pk=exclude_pk)
        stale = list(stale_qs.values_list("pk", flat=True))
        if not stale:
            return 0
        FarmInvitation.objects.filter(pk__in=stale).update(
            status=InvitationStatus.EXPIRED,
            pending_email=None,
            token_hash="",
            code_hash=None,
            delivery_pending=False,
            delivery_reserved_at=None,
            delivery_nonce=None,
            updated_at=now,
        )
        for pk in stale:
            record_audit_event(
                farm=farm,
                actor=None,
                event_type=EventType.INVITATION_EXPIRED,
                entity_type="FarmInvitation",
                entity_id=str(pk),
                after={"status": InvitationStatus.EXPIRED},
            )
        return len(stale)


def expire_pending_invitations(*, now=None) -> int:
    """Expira invitaciones pendientes vencidas, bloqueando primero cada finca."""
    now = now or timezone.now()
    farm_ids = list(
        FarmInvitation.objects.filter(
            status=InvitationStatus.PENDING,
            expires_at__lte=now,
        )
        .order_by("farm_id")
        .values_list("farm_id", flat=True)
        .distinct()
    )
    total = 0
    for farm_id in farm_ids:
        with transaction.atomic():
            farm = farm_services._lock_farm(farm_id=farm_id)
            total += _expire_lazy_farm(farm, now=now)
    return total


@sensitive_variables()
def _send_invitation(
    *, email: str, code: str, farm_name: str, role: str, expires_at
) -> DeliveryOutcome:
    """Envía el correo con el código y devuelve el resultado del proveedor.

    El correo va solo a la dirección de la invitación y no incluye enlace,
    token, huella ni identificadores internos. El cuerpo no se registra.
    ``sensitive_variables()`` oculta todas las variables locales en los
    informes de error de esta función y de las que llama (``deliver_email`` y
    el proveedor), que manejan el cuerpo con el código.
    """
    content = {"code": code, "farm_name": farm_name, "role": role, "expires_at": expires_at}
    text = invitation_mail.plain(**content)
    html = invitation_mail.html(**content)
    return deliver_email(
        to=email,
        subject=invitation_mail.SUBJECT,
        text=text,
        html=html,
        timeout=60,
    )


def _lock_farm_for_delivery(farm_id: int) -> None:
    """Bloquea la finca antes de aplicar el resultado de una entrega.

    Mantiene el orden global de bloqueos (finca y después invitación). Sin él,
    la actualización condicionada bloqueaba primero la invitación y el evento
    de auditoría pedía después la finca (por su clave foránea), mientras un
    reenvío o cambio de rol concurrente tenía la finca y esperaba la
    invitación: MariaDB lo resolvía como interbloqueo (1213). Si la finca ya no
    existe no hay nada que bloquear y la actualización no encontrará filas.
    """
    Farm.objects.select_for_update().filter(pk=farm_id).values_list("pk", flat=True).first()


@sensitive_variables("code")
def _finalize_delivery(
    *,
    invitation: FarmInvitation,
    farm,
    actor,
    code: str,
    success_event_type: str | None = None,
) -> DeliveryOutcome:
    """Envía el código y aplica el resultado condicionado al nonce vigente.

    Se llama después de confirmar la transacción. El resultado solo se aplica
    si ``delivery_nonce`` sigue siendo el de esta entrega: una entrega antigua
    que termine tarde no puede marcar como entregado un código más reciente.
    """
    outcome = _send_invitation(
        email=invitation.email,
        code=code,
        farm_name=farm.name,
        role=invitation.role,
        expires_at=invitation.expires_at,
    )
    now = timezone.now()
    if outcome == DeliveryOutcome.ACCEPTED:
        with transaction.atomic():
            _lock_farm_for_delivery(invitation.farm_id)
            updated = FarmInvitation.objects.filter(
                pk=invitation.pk, delivery_nonce=invitation.delivery_nonce
            ).update(
                delivery_pending=False,
                delivery_confirmed_at=now,
                last_sent_at=now,
            )
            if updated == 1:
                invitation.delivery_pending = False
                invitation.delivery_confirmed_at = now
                invitation.last_sent_at = now
                if success_event_type is not None:
                    record_audit_event(
                        farm=farm,
                        actor=actor,
                        event_type=success_event_type,
                        entity_type="FarmInvitation",
                        entity_id=str(invitation.pk),
                        after={"status": InvitationStatus.PENDING},
                    )
    elif outcome == DeliveryOutcome.REJECTED:
        with transaction.atomic():
            _lock_farm_for_delivery(invitation.farm_id)
            updated = FarmInvitation.objects.filter(
                pk=invitation.pk, delivery_nonce=invitation.delivery_nonce
            ).update(delivery_pending=False)
            if updated == 1:
                invitation.delivery_pending = False
                record_audit_event(
                    farm=farm,
                    actor=actor,
                    event_type=EventType.INVITATION_DELIVERY_FAILED,
                    entity_type="FarmInvitation",
                    entity_id=str(invitation.pk),
                    after={"status": InvitationStatus.PENDING},
                )
    return outcome


def _raise_if_delivery_rejected(
    *, outcome: DeliveryOutcome, invitation: FarmInvitation
) -> None:
    """Convierte un rechazo definitivo del proveedor en error recuperable."""
    if outcome == DeliveryOutcome.REJECTED:
        raise AppError(
            INVITATION_DELIVERY_FAILED,
            502,
            invitation_id=invitation.pk,
        )


@sensitive_variables("token", "code")
def create_invitation(*, user: User, farm_id: int, email: str, role: str) -> FarmInvitation:
    """Crea una invitación pendiente y envía su código; solo el administrador.

    F10.3.3-A: el correo lleva solo el código. ``token_hash`` se sigue
    generando durante la transición, pero ese token ya no se entrega.
    """
    if role not in FarmRole.values:
        raise AppError("rol no válido", 400)
    normalized = _normalize_email(email)
    if not normalized:
        raise AppError("el correo es obligatorio", 400)
    token = _new_token()
    token_hash = _hash_token(token)
    nonce = uuid.uuid4()
    now = timezone.now()

    @sensitive_variables("code")
    def operation():
        code, code_hash = _new_code(normalized)
        farm = _lock_active_farm(farm_id)
        _require_admin(farm, user)
        _expire_lazy_farm(farm, now=now)
        existing = (
            FarmInvitation.objects.select_for_update()
            .filter(farm=farm, email=normalized, status=InvitationStatus.PENDING)
            .first()
        )
        if existing is not None:
            raise AppError("ya existe una invitación vigente para este correo", 409)
        pending_count = FarmInvitation.objects.filter(
            farm=farm, status=InvitationStatus.PENDING
        ).count()
        if pending_count >= MAX_PENDING_INVITATIONS:
            raise AppError("límite de invitaciones pendientes alcanzado", 409)
        invitation = FarmInvitation.objects.create(
            farm=farm,
            email=normalized,
            pending_email=normalized,
            role=role,
            status=InvitationStatus.PENDING,
            token_hash=token_hash,
            code_hash=code_hash,
            expires_at=now + timedelta(days=INVITATION_TTL_DAYS),
            invited_by=user,
            delivery_pending=True,
            delivery_reserved_at=now,
            delivery_nonce=nonce,
        )
        record_audit_event(
            farm=farm,
            actor=user,
            event_type=EventType.INVITATION_CREATED,
            entity_type="FarmInvitation",
            entity_id=str(invitation.pk),
            after={"role": role, "status": InvitationStatus.PENDING},
        )
        return invitation, farm, code

    # La entrega ocurre tras confirmar la transacción; el código solo vive en
    # memoria hasta aquí y se descarta al terminar.
    invitation, farm, code = _with_code_retry(operation)
    try:
        outcome = _finalize_delivery(
            invitation=invitation, farm=farm, actor=user, code=code
        )
    finally:
        del code
    _raise_if_delivery_rejected(outcome=outcome, invitation=invitation)
    return invitation


def list_invitations(*, user: User, farm_id: int):
    """Lista las invitaciones de una finca; solo el administrador."""
    with transaction.atomic():
        farm = _lock_active_farm(farm_id)
        _require_admin(farm, user)
        _expire_lazy_farm(farm)
    return FarmInvitation.objects.filter(farm_id=farm_id).order_by("-created_at", "-id")


@sensitive_variables("token", "code")
def resend_invitation(*, user: User, farm_id: int, invitation_id: int) -> FarmInvitation:
    """Genera un código nuevo, renueva la vigencia y lo reenvía; solo el administrador.

    El código y el token anteriores dejan de valer al confirmar la transacción.
    """
    token = _new_token()
    token_hash = _hash_token(token)
    nonce = uuid.uuid4()
    now = timezone.now()

    @sensitive_variables("code")
    def operation():
        farm = _lock_active_farm(farm_id)
        _require_admin(farm, user)
        _expire_lazy_farm(farm, now=now)
        invitation = _lock_pending_invitation(farm, invitation_id)
        code, invitation.code_hash = _new_code(invitation.email)
        invitation.token_hash = token_hash
        invitation.expires_at = now + timedelta(days=INVITATION_TTL_DAYS)
        invitation.delivery_pending = True
        invitation.delivery_reserved_at = now
        invitation.delivery_confirmed_at = None
        invitation.delivery_nonce = nonce
        invitation.save(
            update_fields=[
                "token_hash",
                "code_hash",
                "expires_at",
                "delivery_pending",
                "delivery_reserved_at",
                "delivery_confirmed_at",
                "delivery_nonce",
                "updated_at",
            ]
        )
        record_audit_event(
            farm=farm,
            actor=user,
            event_type=EventType.INVITATION_RESEND_STARTED,
            entity_type="FarmInvitation",
            entity_id=str(invitation.pk),
            after={"status": InvitationStatus.PENDING},
        )
        return invitation, farm, code

    invitation, farm, code = _with_code_retry(operation)
    try:
        outcome = _finalize_delivery(
            invitation=invitation,
            farm=farm,
            actor=user,
            code=code,
            success_event_type=EventType.INVITATION_RESENT,
        )
    finally:
        del code
    _raise_if_delivery_rejected(outcome=outcome, invitation=invitation)
    return invitation


@sensitive_variables("token", "code")
def change_invitation_role(
    *, user: User, farm_id: int, invitation_id: int, role: str
) -> FarmInvitation:
    """Cambia el rol y envía un código nuevo; solo el administrador.

    El código anterior deja de valer: nunca permite aceptar con el rol previo.
    """
    if role not in FarmRole.values:
        raise AppError("rol no válido", 400)
    token = _new_token()
    token_hash = _hash_token(token)
    nonce = uuid.uuid4()
    now = timezone.now()

    @sensitive_variables("code")
    def operation():
        farm = _lock_active_farm(farm_id)
        _require_admin(farm, user)
        _expire_lazy_farm(farm, now=now)
        invitation = _lock_pending_invitation(farm, invitation_id)
        previous_role = invitation.role
        invitation.role = role
        code, invitation.code_hash = _new_code(invitation.email)
        invitation.token_hash = token_hash
        invitation.expires_at = now + timedelta(days=INVITATION_TTL_DAYS)
        invitation.delivery_pending = True
        invitation.delivery_reserved_at = now
        invitation.delivery_confirmed_at = None
        invitation.delivery_nonce = nonce
        invitation.save(
            update_fields=[
                "role",
                "token_hash",
                "code_hash",
                "expires_at",
                "delivery_pending",
                "delivery_reserved_at",
                "delivery_confirmed_at",
                "delivery_nonce",
                "updated_at",
            ]
        )
        record_audit_event(
            farm=farm,
            actor=user,
            event_type=EventType.INVITATION_ROLE_CHANGED,
            entity_type="FarmInvitation",
            entity_id=str(invitation.pk),
            before={"role": previous_role},
            after={"role": role},
        )
        return invitation, farm, code

    invitation, farm, code = _with_code_retry(operation)
    try:
        outcome = _finalize_delivery(
            invitation=invitation, farm=farm, actor=user, code=code
        )
    finally:
        del code
    _raise_if_delivery_rejected(outcome=outcome, invitation=invitation)
    return invitation


def cancel_invitation(*, user: User, farm_id: int, invitation_id: int) -> FarmInvitation:
    """Cancela una invitación pendiente; solo el administrador."""
    now = timezone.now()
    with transaction.atomic():
        farm = _lock_active_farm(farm_id)
        _require_admin(farm, user)
        _expire_lazy_farm(farm, now=now)
        invitation = _lock_pending_invitation(farm, invitation_id)
        invitation.status = InvitationStatus.CANCELLED
        invitation.pending_email = None
        invitation.token_hash = ""
        invitation.code_hash = None
        invitation.cancelled_at = now
        invitation.delivery_pending = False
        invitation.delivery_reserved_at = None
        invitation.delivery_nonce = None
        invitation.save(
            update_fields=[
                "status",
                "pending_email",
                "token_hash",
                "code_hash",
                "cancelled_at",
                "delivery_pending",
                "delivery_reserved_at",
                "delivery_nonce",
                "updated_at",
            ]
        )
        record_audit_event(
            farm=farm,
            actor=user,
            event_type=EventType.INVITATION_CANCELLED,
            entity_type="FarmInvitation",
            entity_id=str(invitation.pk),
            after={"status": InvitationStatus.CANCELLED},
        )
    return invitation


@sensitive_variables("token", "credential", "stored")
def accept_invitation(*, user: User, token: str) -> FarmMembership:
    """Acepta una invitación por enlace (flujo heredado) y crea o reactiva la membresía."""
    if not token:
        raise AppError(INVITATION_NOT_FOUND, 404)
    return _accept_pending(
        user=user, field="token_hash", credential=_hash_token(token)
    )


def _accept_pending(
    *,
    user: User,
    field: str,
    credential: str,
    uniform_not_found: bool = False,
    metadata: dict | None = None,
) -> FarmMembership:
    """Consume una invitación pendiente y crea o reactiva la membresía.

    Lógica común de la aceptación por enlace (``token_hash``) y por código
    (``code_hash``). Orden de bloqueos: finca, membresía de la cuenta e
    invitación, el mismo de las demás operaciones. Todo ocurre en una sola
    transacción: la membresía y el consumo de la invitación se confirman juntos
    o no se confirma nada.

    Args:
        field: Columna de la credencial (``token_hash`` o ``code_hash``).
        credential: Huella calculada a partir de lo que envió la cuenta.
        uniform_not_found: Si es True, una finca desactivada responde con el
            mismo 404 genérico que una invitación inexistente (flujo por código).
        metadata: Metadatos del evento ``invitation_accepted``.
    """
    now = timezone.now()
    with transaction.atomic():
        hint = FarmInvitation.objects.filter(**{field: credential}).first()
        if hint is None:
            raise AppError(INVITATION_NOT_FOUND, 404)
        if uniform_not_found:
            farm = farm_services._lock_farm(farm_id=hint.farm_id)
            if not farm.is_active:
                raise AppError(INVITATION_NOT_FOUND, 404)
        else:
            farm = _lock_active_farm(hint.farm_id)
        membership = (
            FarmMembership.objects.select_for_update()
            .filter(farm=farm, user=user)
            .first()
        )
        invitation = (
            FarmInvitation.objects.select_for_update()
            .filter(pk=hint.pk)
            .first()
        )
        if invitation is None:
            raise AppError(INVITATION_NOT_FOUND, 404)
        if invitation.status != InvitationStatus.PENDING:
            raise AppError(INVITATION_NOT_FOUND, 404)
        stored = getattr(invitation, field) or ""
        if not stored or not invitation_codes.hashes_match(stored, credential):
            raise AppError(INVITATION_NOT_FOUND, 404)
        if invitation.expires_at <= now:
            raise AppError(INVITATION_NOT_FOUND, 404)

        normalized = _normalize_email(invitation.email)
        if _normalize_email(user.correo) != normalized or not user.email_verified:
            raise AppError(INVITATION_NOT_FOUND, 404)
        matches = _verified_accounts(normalized)
        if matches.count() != 1 or matches.first().pk != user.pk:
            raise AppError(INVITATION_NOT_FOUND, 404)

        if membership is not None and membership.status == MembershipStatus.ACTIVE:
            raise AppError(ALREADY_MEMBER, 409)

        reactivated = False
        if membership is not None:
            membership.farm = farm
            farm_services._clear_membership_permissions(
                membership=membership, actor=user, cause="member_reactivated"
            )
            membership.role = invitation.role
            membership.status = MembershipStatus.ACTIVE
            membership.left_at = None
            membership.revoked_at = None
            membership.revoked_by = None
            membership.save(
                update_fields=[
                    "role",
                    "status",
                    "left_at",
                    "revoked_at",
                    "revoked_by",
                    "updated_at",
                ]
            )
            reactivated = True
        else:
            membership = FarmMembership.objects.create(
                farm=farm, user=user, role=invitation.role
            )

        invitation.status = InvitationStatus.ACCEPTED
        invitation.pending_email = None
        invitation.token_hash = ""
        invitation.code_hash = None
        invitation.accepted_at = now
        invitation.delivery_pending = False
        invitation.delivery_nonce = None
        invitation.save(
            update_fields=[
                "status",
                "pending_email",
                "token_hash",
                "code_hash",
                "accepted_at",
                "delivery_pending",
                "delivery_nonce",
                "updated_at",
            ]
        )

        record_audit_event(
            farm=farm,
            actor=user,
            event_type=EventType.INVITATION_ACCEPTED,
            entity_type="FarmInvitation",
            entity_id=str(invitation.pk),
            after={"status": InvitationStatus.ACCEPTED},
            metadata=metadata,
        )
        joined_event = (
            EventType.MEMBER_REACTIVATED if reactivated else EventType.MEMBER_JOINED
        )
        record_audit_event(
            farm=farm,
            actor=user,
            event_type=joined_event,
            entity_type="FarmMembership",
            entity_id=str(membership.pk),
            after={"role": invitation.role, "status": MembershipStatus.ACTIVE},
        )
        _expire_lazy_farm(farm, now=now, exclude_pk=invitation.pk)

    return membership


# --- Aceptación por código (F10.3.3-B) ----------------------------------------


def ensure_code_eligible(user: User) -> None:
    """Exige una cuenta activa con el correo verificado (403 en otro caso).

    Se comprueba antes de mirar el código, así que no cuenta como intento
    fallido.
    """
    if not user.is_active:
        raise AppError("la cuenta no está activa", 403)
    if not user.email_verified:
        raise AppError(EMAIL_NOT_VERIFIED, 403)


@sensitive_variables("code")
def _code_credential(user: User, code) -> str:
    """Calcula la huella del código con el correo de la cuenta autenticada.

    El correo sale de la cuenta, nunca del cliente: un código solo puede
    encontrar invitaciones dirigidas a ese correo.

    Raises:
        AppError 403: cuenta inactiva o correo sin verificar.
        AppError 404: código mal formado o cuenta sin correo (genérico).
    """
    ensure_code_eligible(user)
    normalized_email = _normalize_email(user.correo or "")
    if not normalized_email:
        raise AppError(INVITATION_NOT_FOUND, 404)
    try:
        return invitation_codes.code_hash(normalized_email, code)
    except invitation_codes.InvalidInvitationCode:
        raise AppError(INVITATION_NOT_FOUND, 404) from None


@sensitive_variables("code", "credential", "invitation")
def preview_invitation_code(*, user: User, code) -> dict:
    """Muestra la finca, el rol y la expiración de una invitación por código.

    Solo lectura: no bloquea, no crea membresías ni consume el código. Las
    mismas condiciones que la aceptación responden con el 404 genérico.
    """
    credential = _code_credential(user, code)
    now = timezone.now()
    invitation = (
        FarmInvitation.objects.select_related("farm")
        .filter(code_hash=credential)
        .first()
    )
    normalized = _normalize_email(user.correo or "")
    valid = (
        invitation is not None
        and invitation.status == InvitationStatus.PENDING
        and invitation_codes.hashes_match(invitation.code_hash or "", credential)
        and invitation.expires_at > now
        and invitation.farm.is_active
        and _normalize_email(invitation.email) == normalized
    )
    if not valid:
        raise AppError(INVITATION_NOT_FOUND, 404)
    matches = _verified_accounts(normalized)
    if matches.count() != 1 or matches.first().pk != user.pk:
        raise AppError(INVITATION_NOT_FOUND, 404)
    if FarmMembership.objects.filter(
        farm_id=invitation.farm_id, user=user, status=MembershipStatus.ACTIVE
    ).exists():
        raise AppError(ALREADY_MEMBER, 409)
    return {
        "farm_name": invitation.farm.name,
        "role": invitation.role,
        "expires_at": invitation.expires_at,
    }


@sensitive_variables("code", "credential", "stored")
def accept_invitation_code(*, user: User, code) -> FarmMembership:
    """Acepta una invitación por código y crea o reactiva la membresía.

    Reutiliza ``_accept_pending``: mismos bloqueos, comprobaciones, limpieza de
    permisos y eventos que la aceptación por enlace. El evento
    ``invitation_accepted`` lleva ``metadata={"channel": "code"}``.
    """
    credential = _code_credential(user, code)
    return _accept_pending(
        user=user,
        field="code_hash",
        credential=credential,
        uniform_not_found=True,
        metadata={"channel": "code"},
    )
