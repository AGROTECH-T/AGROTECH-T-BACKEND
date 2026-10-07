"""Gestiona generación, huella, vigencia e intentos del OTP.

@author Cristian Deysdayr Jimenez
"""
import hashlib
import hmac
import os
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.integrations import email, whatsapp
from apps.accounts.models import LoginCode, User


def request_code(user: User, channel: str) -> int:
    """Entrega un OTP si no existe otro vigente."""
    existing = LoginCode.objects.filter(user=user).first()
    now = timezone.now()
    if existing and existing.expires_at > now:
        wait = max(1, int((existing.expires_at - now).total_seconds()))
        raise AppError("espera para enviar otro codigo", 429, wait=wait)
    digits = f"{secrets.randbelow(1_000_000):06d}"
    if channel == "whatsapp":
        whatsapp.send_code(user.phone)
        stored = "twilio-verify"
    else:
        email.send_code(user.correo, digits)
        stored = _digest(user.pk, digits)
    minutes = _otp_minutes()
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


def verify_code(user: User, raw_code: str) -> None:
    """Valida y consume un OTP vigente."""
    match = re.search(r"\d{6}", raw_code)
    if not match:
        raise AppError("codigo invalido", 401)
    record = LoginCode.objects.filter(user=user).first()
    if not record or record.attempts >= 5:
        raise AppError("codigo invalido", 401)
    if record.expires_at <= timezone.now():
        record.delete()
        raise AppError("el codigo vencio", 401)
    digits = match.group()
    valid = (
        whatsapp.check_code(user.phone, digits)
        if record.channel == "whatsapp"
        else hmac.compare_digest(record.code_hash, _digest(user.pk, digits))
    )
    if not valid:
        record.attempts += 1
        record.save(update_fields=["attempts"])
        raise AppError("codigo invalido", 401)
    record.delete()


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
