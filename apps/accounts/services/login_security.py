"""Controla bloqueos progresivos por credenciales incorrectas.

@author Cristian Deysdayr Jimenez
"""

from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User

MAX_ATTEMPTS = 5
LOCK_MINUTES = 15


def is_blocked(user: User) -> bool:
    """Indica si una cuenta permanece temporalmente bloqueada."""
    if not user.is_active:
        return True
    return bool(user.locked_until and user.locked_until > timezone.now())


def register_failure(user: User) -> None:
    """Incrementa intentos y bloquea temporalmente al alcanzar el límite."""
    User.objects.filter(pk=user.pk).update(
        failed_login_attempts=F("failed_login_attempts") + 1
    )
    user.refresh_from_db(fields=["failed_login_attempts", "locked_until"])
    if user.failed_login_attempts >= MAX_ATTEMPTS:
        user.locked_until = timezone.now() + timedelta(minutes=LOCK_MINUTES)
        user.save(update_fields=["locked_until"])


def clear_failures(user: User) -> None:
    """Limpia bloqueos después de una autenticación válida."""
    if user.failed_login_attempts or user.locked_until:
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save(update_fields=["failed_login_attempts", "locked_until"])
