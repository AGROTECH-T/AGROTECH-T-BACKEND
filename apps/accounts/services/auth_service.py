"""Ejecuta los casos de uso de registro, acceso y recuperación.

@author Cristian Deysdayr Jimenez
"""
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.accounts.services import login_security, otp_service, session_service, token_service


def register(data: dict) -> None:
    """Crea una cuenta y almacena únicamente el hash de contraseña."""
    password = data.pop("password")
    try:
        User.objects.create(password_hash=make_password(password), **data)
    except IntegrityError as exc:
        raise AppError("esa identificacion ya esta registrada", 409) from exc


def login(identification: str, password: str) -> str:
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
    return token


def recover(identification: str, method: str) -> int:
    """Solicita un OTP sin revelar cuentas inexistentes."""
    user = User.objects.filter(identification=identification).first()
    needs_phone = method in {"whatsapp", "sms"}
    unavailable = not user or (needs_phone and not user.phone) or (
        method == "correo" and not user.correo
    )
    if unavailable:
        raise AppError("no se pudo enviar el codigo")
    return otp_service.request_code(user, method)


def confirm_code(identification: str, code: str) -> None:
    """Comprueba el OTP sin consumirlo ni cambiar la contraseña."""
    user = User.objects.filter(identification=identification).first()
    if not user:
        raise AppError("codigo invalido", 401)
    otp_service.check_code(user, code)


def reset_password(identification: str, code: str, password: str) -> None:
    """Consume un OTP y guarda una contraseña nueva."""
    user = User.objects.filter(identification=identification).first()
    if not user:
        raise AppError("codigo invalido", 401)
    otp_service.verify_code(user, code)
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
