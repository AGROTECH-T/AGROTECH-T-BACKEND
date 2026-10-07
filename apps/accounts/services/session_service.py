"""Gestiona sesiones revocables sin persistir tokens completos.

@author Cristian Deysdayr Jimenez
"""

from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import Session, User
from apps.accounts.services import token_service


def open_session(user: User, compact: str) -> None:
    """Crea o reemplaza la sesión única de una cuenta."""
    Session.objects.update_or_create(
        user=user,
        defaults={
            "last_seen": timezone.now(),
            "token_hash": token_service.digest(compact),
        },
    )


def authenticate(compact: str) -> tuple[User, token_service.TokenClaims]:
    """Valida JWE, vigencia, huella persistida y estado de la cuenta."""
    claims = token_service.read(compact)
    threshold = timezone.now() - token_service.ttl()
    session = (
        Session.objects.select_related("user")
        .filter(
            user_id=claims.user_id,
            token_hash=token_service.digest(compact),
            last_seen__gt=threshold,
            user__is_active=True,
        )
        .first()
    )
    if not session:
        raise AppError("la sesion vencio", 401)
    return session.user, claims


def rotate(compact: str) -> str:
    """Invalida el token actual y entrega uno nuevo."""
    user, _ = authenticate(compact)
    next_token = token_service.issue(user.pk)
    open_session(user, next_token)
    return next_token


def revoke(user: User) -> None:
    """Elimina la sesión activa de una cuenta."""
    Session.objects.filter(user=user).delete()
