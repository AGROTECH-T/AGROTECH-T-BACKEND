"""Gestiona generación, huella, vigencia e intentos del OTP.

@author Cristian Deysdayr Jimenez
"""
import hashlib
import hmac
import math
import os
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.integrations import email, sms, whatsapp
from apps.accounts.models import LoginCode, User

RESEND_WAIT = 10
INVALID_CODE = "codigo invalido"


def request_code(user: User, channel: str) -> int:
    """Entrega un OTP y reemplaza el anterior después de diez segundos."""
    existing = LoginCode.objects.filter(user=user).first()
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
        defaults={
            "code_hash": stored,
            "expires_at": now + timedelta(minutes=minutes),
            "attempts": 0,
            "channel": channel,
        },
    )
    return minutes * 60


def check_code(user: User, raw_code: str) -> None:
    """Comprueba el OTP vigente sin gastarlo."""
    _match(user, raw_code)


def verify_code(user: User, raw_code: str) -> None:
    """Valida y consume un OTP vigente."""
    _match(user, raw_code)
    LoginCode.objects.filter(user=user).delete()


def _match(user: User, raw_code: str) -> None:
    """Acepta el código o suma un intento fallido."""
    match = re.search(r"\d{6}", raw_code)
    if not match:
        raise AppError(INVALID_CODE, 401)
    record = LoginCode.objects.filter(user=user).first()
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
