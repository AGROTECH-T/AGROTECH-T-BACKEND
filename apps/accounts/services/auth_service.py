"""Ejecuta los casos de uso de registro, acceso y recuperación.

@author Cristian Deysdayr Jimenez
"""
import hashlib
from contextlib import contextmanager

from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, connection, transaction
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import LoginCode, OTPPurpose, User
from apps.accounts.services import login_security, otp_service, session_service, token_service
from apps.trazabilidad.models import EventType
from apps.trazabilidad.services import record_audit_event

EMAIL_TAKEN = "ese correo ya está registrado"
IDENTIFICATION_TAKEN = "esa identificacion ya esta registrada"
ACCOUNT_CONFLICT = "no se pudo crear la cuenta"
EMAIL_BUSY = "no pudimos completar la operación; inténtalo de nuevo"
EMAIL_LOCK_TIMEOUT_SECONDS = 5
EMAIL_LOCK_PREFIX = "agrotech:correo:"


@contextmanager
def email_lock(correo: str):
    """Serializa el alta o el cambio a un mismo correo normalizado.

    Usa un bloqueo con nombre de MariaDB (GET_LOCK) por huella del correo, de
    modo que la comprobación ``email_in_use`` y la escritura de dos solicitudes
    simultáneas no se intercalen. No es una restricción de la base de datos:
    solo protege a los llamadores que pasan por aquí (registro y perfil), y el
    bloqueo se libera al salir del bloque, así que la escritura debe estar
    confirmada antes (los llamadores no se ejecutan dentro de una transacción
    externa; no hay ATOMIC_REQUESTS). Fuera de MariaDB/MySQL no bloquea.
    """
    normalized = _normalize_correo(correo or "")
    if not normalized or connection.vendor != "mysql":
        yield
        return
    name = EMAIL_LOCK_PREFIX + hashlib.sha256(normalized.encode()).hexdigest()[:40]
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK(%s, %s)", [name, EMAIL_LOCK_TIMEOUT_SECONDS])
        acquired = cursor.fetchone()[0]
    if acquired != 1:
        raise AppError(EMAIL_BUSY, 503)
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK(%s)", [name])


def email_in_use(correo: str, exclude_pk: int | None = None) -> bool:
    """Indica si otra cuenta ya usa el correo, sin distinguir mayúsculas.

    Compara la forma normalizada (recortada y en minúsculas) para que la
    garantía de una cuenta por correo no dependa de cómo se guardó cada fila.
    """
    normalized = _normalize_correo(correo or "")
    if not normalized:
        return False
    matches = User.objects.annotate(normalized=Lower(Trim("correo"))).filter(
        normalized=normalized
    )
    if exclude_pk is not None:
        matches = matches.exclude(pk=exclude_pk)
    return matches.exists()


def register(data: dict) -> None:
    """Crea una cuenta y almacena únicamente el hash de contraseña.

    Exige un correo que ninguna otra cuenta use (una cuenta por correo). La
    comprobación y el alta ocurren bajo ``email_lock`` y el alta se confirma
    antes de liberarlo.
    """
    password = data.pop("password")
    correo = data.get("correo", "")
    with email_lock(correo):
        if email_in_use(correo):
            raise AppError(EMAIL_TAKEN, 409)
        try:
            with transaction.atomic():
                User.objects.create(password_hash=make_password(password), **data)
        except IntegrityError as exc:
            raise _registration_conflict(data) from exc


def _registration_conflict(data: dict) -> AppError:
    """Traduce un conflicto de integridad sin revelar datos de otra cuenta."""
    if User.objects.filter(identification=data.get("identification", "")).exists():
        return AppError(IDENTIFICATION_TAKEN, 409)
    if email_in_use(data.get("correo", "")):
        return AppError(EMAIL_TAKEN, 409)
    return AppError(ACCOUNT_CONFLICT, 409)


PROFILE_FIELDS = ("first_name", "last_name", "correo", "phone")


def _normalize_correo(value: str) -> str:
    """Normaliza una dirección de correo para comparar y guardar."""
    return value.strip().lower()


def update_profile(user: User, data: dict) -> User:
    """Actualiza los datos editables y gestiona el cambio de correo.

    Un cambio de correo se hace bajo ``email_lock`` del correo nuevo; la
    transacción se confirma antes de liberarlo.
    """
    with email_lock(data.get("correo", "")):
        return _update_profile_locked(user, data)


def _update_profile_locked(user: User, data: dict) -> User:
    """Aplica el cambio de perfil en una transacción."""
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        changed_fields = []
        email_changed = False
        previous_verified = locked.email_verified

        if "correo" in data:
            normalized = _normalize_correo(data["correo"])
            if normalized != locked.correo and email_in_use(normalized, exclude_pk=locked.pk):
                raise AppError(EMAIL_TAKEN, 409)
            if normalized != locked.correo:
                locked.correo = normalized
                locked.email_verified = False
                locked.email_verified_at = None
                email_changed = True
            changed_fields.append("correo")

        for field in PROFILE_FIELDS:
            if field in data and field != "correo":
                setattr(locked, field, data[field])
                changed_fields.append(field)

        if email_changed:
            changed_fields += ["email_verified", "email_verified_at"]

        if changed_fields:
            locked.save(update_fields=changed_fields)

        if email_changed:
            LoginCode.objects.filter(
                user=locked,
                purpose=OTPPurpose.VERIFICATION,
            ).delete()
            record_audit_event(
                farm=None,
                actor=locked,
                event_type=EventType.EMAIL_CHANGED,
                entity_type="User",
                entity_id=str(locked.pk),
                before={"verified": previous_verified},
                after={"verified": False},
            )

        return locked


def request_email_verification(user: User) -> int:
    """Solicita un código de verificación de correo."""
    return otp_service.request_verification_code(user)


def verify_email(user: User, code: str) -> User:
    """Verifica el correo del usuario consumiendo un OTP de verificación."""
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        if locked.email_verified:
            return locked
        otp_service.verify_code(locked, code, OTPPurpose.VERIFICATION)
        locked.email_verified = True
        locked.email_verified_at = timezone.now()
        locked.save(update_fields=["email_verified", "email_verified_at"])
        record_audit_event(
            farm=None,
            actor=locked,
            event_type=EventType.EMAIL_VERIFIED,
            entity_type="User",
            entity_id=str(locked.pk),
            after={"verified": True},
        )
        return locked


def login(identification: str, password: str) -> tuple[User, str]:
    """Valida credenciales y abre una sesión única."""
    user = User.objects.filter(identification=identification).first()
    if not user or login_security.is_blocked(user):
        raise AppError("credenciales invalidas", 401)
    if not check_password(password, user.password_hash):
        login_security.register_failure(user)
        raise AppError("credenciales invalidas", 401)
    login_security.clear_failures(user)
    token = token_service.issue(user.pk)
    session_service.open_session(user, token)
    return user, token


def recover(identification: str, method: str) -> int:
    """Solicita un OTP sin revelar cuentas inexistentes."""
    user = User.objects.filter(identification=identification).first()
    needs_phone = method in {"whatsapp", "sms"}
    unavailable = not user or (needs_phone and not user.phone) or (
        method == "correo" and not user.correo
    )
    if unavailable:
        raise AppError("no se pudo enviar el codigo")
    return otp_service.request_code(user, method, OTPPurpose.RECOVERY)


def confirm_code(identification: str, code: str) -> None:
    """Comprueba el OTP sin consumirlo ni cambiar la contraseña."""
    user = User.objects.filter(identification=identification).first()
    if not user:
        raise AppError("codigo invalido", 401)
    otp_service.check_code(user, code, OTPPurpose.RECOVERY)


def reset_password(identification: str, code: str, password: str) -> None:
    """Consume un OTP y guarda una contraseña nueva."""
    user = User.objects.filter(identification=identification).first()
    if not user:
        raise AppError("codigo invalido", 401)
    otp_service.verify_code(user, code, OTPPurpose.RECOVERY)
    user.password_hash = make_password(password)
    user.save(update_fields=["password_hash"])
    login_security.clear_failures(user)
    session_service.revoke(user)


def renew(authorization: str) -> str:
    """Rota el token de una sesión que permanece activa."""
    return session_service.rotate(_bearer(authorization))


def logout(user: User) -> None:
    """Revoca la sesión activa de una cuenta."""
    session_service.revoke(user)


def _bearer(header: str) -> str:
    """Extrae un token del esquema HTTP Bearer."""
    scheme, separator, token = header.strip().partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise AppError("la sesion vencio", 401)
    return token.strip()
