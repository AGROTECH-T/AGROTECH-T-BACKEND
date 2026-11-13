"""Gestiona sesiones revocables sin persistir tokens completos.

@author Cristian Deysdayr Jimenez
"""

from django.db import IntegrityError
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import Session, User
from apps.accounts.services import token_service


def open_session(user: User, compact: str, session_key: str | None = None) -> Session:
    """Crea una fila nueva por cada inicio de sesión."""
    now = timezone.now()
    return Session.objects.create(
        user=user,
        session_key=session_key,
        token_hash=token_service.digest(compact),
        last_seen=now,
        created_at=now,
    )


def _find_open_session(user_id: int, token_hash: str) -> Session | None:
    """Busca una sesión abierta, vigente y de una cuenta activa."""
    threshold = timezone.now() - token_service.ttl()
    return (
        Session.objects.select_related("user")
        .filter(
            user_id=user_id,
            token_hash=token_hash,
            closed_at__isnull=True,
            last_seen__gt=threshold,
            user__is_active=True,
        )
        .first()
    )


def authenticate(compact: str) -> tuple[User, token_service.TokenClaims]:
    """Valida JWE, vigencia, huella persistida y estado de la cuenta."""
    claims = token_service.read(compact)
    session = _find_open_session(claims.user_id, token_service.digest(compact))
    if not session:
        raise AppError("la sesion vencio", 401)
    return session.user, claims


def authenticate_by_session_key(session_key: str) -> Session | None:
    """Busca una sesión abierta por su clave Django y cuenta activa."""
    if not session_key:
        return None
    return (
        Session.objects.select_related("user")
        .filter(
            session_key=session_key,
            closed_at__isnull=True,
            user__is_active=True,
        )
        .first()
    )


def rotate(compact: str) -> str:
    """Invalida el token actual y entrega uno nuevo mediante CAS."""
    claims = token_service.read(compact)
    old_hash = token_service.digest(compact)
    session = _find_open_session(claims.user_id, old_hash)
    if not session:
        raise AppError("la sesion vencio", 401)
    next_token = token_service.issue(claims.user_id)
    new_hash = token_service.digest(next_token)
    try:
        updated = Session.objects.filter(
            pk=session.pk,
            token_hash=old_hash,
            closed_at__isnull=True,
        ).update(token_hash=new_hash, last_seen=timezone.now())
    except IntegrityError as exc:
        raise AppError("error interno de sesion", 500) from exc
    if updated != 1:
        raise AppError("la sesion vencio", 401)
    return next_token


def revoke_session(session: Session) -> bool:
    """Cierra lógicamente una sesión individual de forma idempotente."""
    updated = Session.objects.filter(pk=session.pk, closed_at__isnull=True).update(
        closed_at=timezone.now()
    )
    return updated == 1


def revoke_other_sessions(session: Session) -> int:
    """Cierra las demás sesiones abiertas del usuario, excluyendo la actual."""
    return (
        Session.objects.filter(
            user_id=session.user_id,
            closed_at__isnull=True,
        )
        .exclude(pk=session.pk)
        .update(closed_at=timezone.now())
    )


def revoke_all(user: User) -> int:
    """Cierra todas las sesiones abiertas de una cuenta."""
    return Session.objects.filter(user=user, closed_at__isnull=True).update(
        closed_at=timezone.now()
    )


def revoke(user: User) -> int:
    """Alias transitorio de ``revoke_all``."""
    return revoke_all(user)
