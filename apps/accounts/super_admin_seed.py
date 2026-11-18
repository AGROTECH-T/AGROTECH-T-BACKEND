"""Siembra las cuentas iniciales de la plataforma.

Propósito: crear o actualizar los usuarios del archivo seeders al arrancar.
Contexto: el superadmin nombra administradores; la finca demo se siembra
después en ``apps.farms.seed_demo_farm``.
@author Cristian Deysdayr Jimenez
"""
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone
from rest_framework.serializers import ValidationError

from apps.accounts.models import User
from apps.accounts.seeders import USUARIOS_INICIALES
from apps.accounts.validators import (
    normalize_identification,
    normalize_name,
    normalize_phone,
    validate_password_strength,
)


class SeedSuperAdminError(ValueError):
    """Error de datos al sembrar el super administrador."""


def seed_usuarios_iniciales() -> list[User]:
    """Crea o actualiza todas las cuentas del archivo seeders y la finca demo.

    @returns Cuentas sembradas en el mismo orden del archivo.
    """
    usuarios = [seed_usuario(**datos) for datos in USUARIOS_INICIALES]
    from apps.farms.seed_demo_farm import seed_demo_farm

    seed_demo_farm()
    return usuarios


def seed_super_admin(
    *,
    identification: str,
    password: str | None,
    first_name: str = "Super",
    last_name: str = "Admin",
    correo: str = "",
    phone: str = "",
) -> User:
    """Crea o actualiza la cuenta de superadmin.

    @param identification Documento de la cuenta.
    @param password Clave nueva; obligatoria si la cuenta no existe.
    @param first_name Nombre visible.
    @param last_name Apellido visible.
    @param correo Correo opcional.
    @param phone Celular opcional.
    @returns Cuenta de super administrador.
    """
    return seed_usuario(
        identification=identification,
        password=password,
        first_name=first_name,
        last_name=last_name,
        correo=correo,
        phone=phone,
        is_superadmin=True,
        is_administrador=False,
    )


def seed_usuario(
    *,
    identification: str,
    password: str | None,
    first_name: str = "Super",
    last_name: str = "Admin",
    correo: str = "",
    phone: str = "",
    is_superadmin: bool = False,
    is_administrador: bool = False,
) -> User:
    """Crea o actualiza una cuenta inicial.

    @param identification Documento de la cuenta.
    @param password Clave nueva; obligatoria si la cuenta no existe.
    @param first_name Nombre visible.
    @param last_name Apellido visible.
    @param correo Correo opcional.
    @param phone Celular opcional.
    @param is_superadmin Si nombra administradores de plataforma.
    @param is_administrador Si puede crear fincas y trabajadores.
    @returns Cuenta sembrada.
    """
    documento = _validar(normalize_identification, identification)
    nombre = _validar(lambda value: normalize_name(value, "nombre"), first_name)
    apellido = _validar(lambda value: normalize_name(value, "apellido"), last_name)
    celular = _validar(normalize_phone, phone) if phone.strip() else ""

    with transaction.atomic():
        user = User.objects.filter(identification=documento).first()
        if user is None:
            clave = _clave(password)
            if clave is None:
                raise SeedSuperAdminError("la contraseña es obligatoria para crear la cuenta")
            return User.objects.create(
                first_name=nombre,
                last_name=apellido,
                identification=documento,
                phone=celular,
                correo=correo.strip(),
                password_hash=make_password(clave),
                is_superadmin=is_superadmin,
                is_administrador=is_administrador,
                email_verified=True,
                email_verified_at=timezone.now(),
            )
        user.first_name = nombre
        user.last_name = apellido
        user.correo = correo.strip() or user.correo
        if celular:
            user.phone = celular
        user.is_active = True
        user.is_superadmin = is_superadmin
        user.is_administrador = is_administrador
        if not user.email_verified:
            user.email_verified = True
            user.email_verified_at = timezone.now()
        user.save()
        return user


def _clave(password: str | None) -> str | None:
    """Normaliza la clave y exige la misma fortaleza que el registro."""
    if password is None or not password.strip():
        return None
    return _validar(validate_password_strength, password)


def _validar(funcion, valor: str) -> str:
    """Traduce un error de serializer a un mensaje de siembra."""
    try:
        return funcion(valor)
    except ValidationError as exc:
        raise SeedSuperAdminError(_texto(exc)) from exc


def _texto(exc: ValidationError) -> str:
    """Toma el primer mensaje de un error de validación."""
    detail = exc.detail
    if isinstance(detail, dict):
        detail = next(iter(detail.values()))
    if isinstance(detail, (list, tuple)):
        return str(detail[0])
    return str(detail)
