"""Emite y abre JWT cifrados con JWE A256GCM.

@author Cristian Deysdayr Jimenez
"""
import base64
import hashlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from jwcrypto import jwe, jwk

from apps.accounts.errors import AppError

ISSUER = "agrotech-t"
AUDIENCE = "agrotech-t-api"


@dataclass(frozen=True)
class TokenClaims:
    """Representa los datos mínimos contenidos en una sesión cifrada."""

    user_id: int
    token_id: str
    expires_at: int


def _key() -> jwk.JWK:
    """Carga una clave simétrica de exactamente 32 bytes."""
    raw = os.getenv("JWT_ENCRYPTION_KEY", "")
    try:
        decoded = base64.b64decode(raw, validate=True)
    except ValueError as exc:
        raise AppError("configuración de token inválida", 500) from exc
    if len(decoded) != 32:
        raise AppError("configuración de token inválida", 500)
    encoded = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode()
    return jwk.JWK(kty="oct", k=encoded)


def ttl() -> timedelta:
    """Obtiene la duración de sesión dentro de límites seguros."""
    try:
        minutes = int(os.getenv("JWT_TTL_MINUTES", "15"))
    except ValueError:
        minutes = 15
    return timedelta(minutes=min(max(minutes, 1), 1440))


def issue(user_id: int) -> str:
    """Crea un JWE que contiene únicamente identidad interna y metadatos."""
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl()).timestamp()),
        "iss": ISSUER,
        "aud": AUDIENCE,
        "jti": secrets.token_urlsafe(24),
    }
    token = jwe.JWE(
        json.dumps(claims).encode(),
        protected={"alg": "dir", "enc": "A256GCM", "typ": "JWT"},
    )
    token.add_recipient(_key())
    return token.serialize(compact=True)


def read(compact: str) -> TokenClaims:
    """Descifra y valida una sesión JWE vigente."""
    try:
        token = jwe.JWE()
        token.deserialize(compact, key=_key())
        claims = json.loads(token.payload)
        now = int(datetime.now(UTC).timestamp())
        issued_at = int(claims["iat"])
        expires_at = int(claims["exp"])
        if (
            now >= expires_at
            or issued_at > now + 60
            or claims["iss"] != ISSUER
            or claims["aud"] != AUDIENCE
            or not claims["jti"]
        ):
            raise ValueError("expired")
        return TokenClaims(
            user_id=int(claims["sub"]),
            token_id=str(claims["jti"]),
            expires_at=expires_at,
        )
    except (KeyError, TypeError, ValueError, jwe.InvalidJWEData) as exc:
        raise AppError("la sesion vencio", 401) from exc


def digest(compact: str) -> str:
    """Genera una huella irreversible del token para persistencia."""
    return hashlib.sha256(compact.encode()).hexdigest()
