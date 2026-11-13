"""Gestiona generación, huella, vigencia e intentos del OTP.

@author Cristian Deysdayr Jimenez
"""
import hashlib
import hmac
import math
import os
import re
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.integrations import email, sms, whatsapp
from apps.accounts.integrations.delivery import DeliveryOutcome, deliver_email
from apps.accounts.integrations.otp_mail_body import html as otp_html
from apps.accounts.integrations.otp_mail_body import plain as otp_plain
from apps.accounts.models import LoginCode, OTPPurpose, User

RESEND_WAIT = 10
INVALID_CODE = "codigo invalido"


def request_code(user: User, channel: str, purpose: str) -> int:
    """Entrega un OTP para un propósito y reemplaza el anterior del mismo."""
    existing = LoginCode.objects.filter(user=user, purpose=purpose).first()
    now = timezone.now()
    if existing:
        elapsed = (now - existing.sent_at).total_seconds()
        if elapsed < RESEND_WAIT:
            wait = max(1, math.ceil(RESEND_WAIT - elapsed))
            raise AppError(f"Podrás pedir otro código en {wait} segundos.", 429, wait=wait)
    digits = f"{secrets.randbelow(1_000_000):06d}"
    minutes = _otp_minutes()
    _deliver(user, channel, digits, minutes)
    stored = _digest(user.pk, digits)
    LoginCode.objects.update_or_create(
        user=user,
        purpose=purpose,
        defaults={
            "code_hash": stored,
            "expires_at": now + timedelta(minutes=minutes),
            "attempts": 0,
            "channel": channel,
        },
    )
    return minutes * 60


def request_verification_code(user: User) -> int:
    """Reserva y entrega un OTP de verificación con control de concurrencia.

    La reserva y la huella del código se persisten en una transacción y el
    envío ocurre fuera de ella, sin retener bloqueos de base de datos. El
    resultado del proveedor se aplica únicamente si el ``delivery_nonce``
    continúa vigente.
    """
    digits = f"{secrets.randbelow(1_000_000):06d}"
    minutes = _otp_minutes()
    nonce = uuid.uuid4()
    stored = _digest(user.pk, digits)
    now = timezone.now()
    expires_at = now + timedelta(minutes=minutes)

    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        record = (
            LoginCode.objects.select_for_update()
            .filter(user=locked_user, purpose=OTPPurpose.VERIFICATION)
            .first()
        )
        previous_nonce = None
        if record is not None:
            previous_nonce = record.delivery_nonce
            if record.sent_at is not None:
                elapsed = (now - record.sent_at).total_seconds()
                if elapsed < RESEND_WAIT:
                    wait = max(1, math.ceil(RESEND_WAIT - elapsed))
                    raise AppError(
                        f"Podrás pedir otro código en {wait} segundos.", 429, wait=wait
                    )
        else:
            record = LoginCode(user=locked_user, purpose=OTPPurpose.VERIFICATION)

        # Libera cualquier reserva anterior condicionada al nonce previo para
        # que una respuesta tardía del proveedor no afecte la reserva nueva.
        if previous_nonce is not None:
            LoginCode.objects.filter(
                user=locked_user,
                purpose=OTPPurpose.VERIFICATION,
                delivery_nonce=previous_nonce,
            ).update(delivery_pending=False, delivery_reserved_at=None)

        record.code_hash = stored
        record.expires_at = expires_at
        record.attempts = 0
        record.channel = "correo"
        record.delivery_pending = True
        record.delivery_reserved_at = now
        record.delivery_confirmed_at = None
        record.delivery_nonce = nonce
        record.save()

    outcome = _deliver_verification(locked_user.correo, digits, minutes)
    _apply_verification_outcome(locked_user, nonce, outcome)
    return minutes * 60


def _deliver_verification(address: str, digits: str, minutes: int) -> DeliveryOutcome:
    """Envía el OTP de verificación y devuelve el resultado del proveedor."""
    if not settings.DEFAULT_FROM_EMAIL or not address:
        return DeliveryOutcome.REJECTED
    subject = f"Tu código AGROTECH-T · {minutes} min"
    return deliver_email(
        to=address,
        subject=subject,
        text=otp_plain(digits, minutes),
        html=otp_html(digits, minutes),
        timeout=15,
    )


def _apply_verification_outcome(user: User, nonce: uuid.UUID, outcome: DeliveryOutcome) -> None:
    """Actualiza la entrega únicamente si el nonce sigue vigente."""
    if outcome == DeliveryOutcome.ACCEPTED:
        LoginCode.objects.filter(
            user=user, purpose=OTPPurpose.VERIFICATION, delivery_nonce=nonce
        ).update(delivery_pending=False, delivery_confirmed_at=timezone.now())
    elif outcome == DeliveryOutcome.REJECTED:
        LoginCode.objects.filter(
            user=user, purpose=OTPPurpose.VERIFICATION, delivery_nonce=nonce
        ).update(delivery_pending=False)


def check_code(user: User, raw_code: str, purpose: str) -> None:
    """Comprueba el OTP vigente de un propósito sin gastarlo."""
    _match(user, raw_code, purpose)


def verify_code(user: User, raw_code: str, purpose: str) -> None:
    """Valida y consume un OTP vigente de un propósito."""
    _match(user, raw_code, purpose)
    LoginCode.objects.filter(user=user, purpose=purpose).delete()


def _match(user: User, raw_code: str, purpose: str) -> None:
    """Acepta el código del propósito indicado o suma un intento fallido."""
    match = re.search(r"\d{6}", raw_code)
    if not match:
        raise AppError(INVALID_CODE, 401)
    record = LoginCode.objects.filter(user=user, purpose=purpose).first()
    if not record or record.attempts >= 5:
        raise AppError(INVALID_CODE, 401)
    if record.expires_at <= timezone.now():
        record.delete()
        raise AppError("el codigo vencio", 401)
    digits = match.group()
    expected = _digest(user.pk, digits)
    valid = len(record.code_hash) == len(expected) and hmac.compare_digest(
        record.code_hash, expected
    )
    if not valid:
        record.attempts += 1
        record.save(update_fields=["attempts"])
        raise AppError(INVALID_CODE, 401)


def _deliver(user: User, channel: str, digits: str, minutes: int) -> None:
    """Entrega el código por correo, WhatsApp o mensaje de texto."""
    if channel == "correo":
        email.send_code(user.correo, digits, minutes)
    elif channel == "whatsapp":
        whatsapp.send_code(user.phone, digits)
    elif channel == "sms":
        sms.send_code(user.phone, digits)
    else:
        raise AppError("no se pudo enviar el codigo")


def _digest(user_id: int, digits: str) -> str:
    """Crea una huella HMAC no reversible para el OTP."""
    secret = os.getenv("OTP_HMAC_KEY", settings.SECRET_KEY).encode()
    return hmac.new(secret, f"{user_id}:{digits}".encode(), hashlib.sha256).hexdigest()


def _otp_minutes() -> int:
    """Obtiene la vigencia OTP entre uno y quince minutos."""
    try:
        value = int(os.getenv("OTP_TTL_MINUTES", "5"))
    except ValueError:
        value = 5
    return min(max(value, 1), 15)
