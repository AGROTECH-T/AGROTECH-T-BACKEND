"""Alta de administradores de plataforma.

Propósito: dejar que solo el superadmin cree o nombre administradores.
Contexto: el administrador ya puede crear fincas, trabajadores y el resto.
@author Cristian Deysdayr Jimenez
"""
from django.contrib.auth.hashers import make_password
from django.db import transaction

from apps.accounts.errors import AppError
from apps.accounts.models import User


def listar_administradores(actor: User) -> dict:
    """Pendientes de nombramiento y administradores ya creados."""
    _exigir_superadmin(actor)
    pendientes = User.objects.filter(
        is_superadmin=False,
        is_administrador=False,
        farm_memberships__isnull=True,
    ).order_by("first_name", "last_name", "id")
    administradores = User.objects.filter(is_administrador=True).order_by(
        "first_name", "last_name", "id"
    )
    return {
        "pendientes": [_ficha(person) for person in pendientes],
        "administradores": [_ficha(person) for person in administradores],
    }


def crear_administrador(*, actor: User, datos: dict) -> dict:
    """Crea una cuenta nueva con permiso de administrador."""
    _exigir_superadmin(actor)
    if User.objects.filter(identification=datos["identification"]).exists():
        raise AppError("ya existe una cuenta con ese documento", 409)
    with transaction.atomic():
        person = User.objects.create(
            first_name=datos["first_name"],
            last_name=datos["last_name"],
            identification=datos["identification"],
            phone=datos.get("phone", ""),
            correo=datos["correo"],
            password_hash=make_password(datos["password"]),
            is_administrador=True,
        )
    return _ficha(person)


def promover_administrador(*, actor: User, user_id: int) -> dict:
    """Nombra administradora a una cuenta que ya se había registrado."""
    _exigir_superadmin(actor)
    person = User.objects.filter(id=user_id).first()
    if person is None:
        raise AppError("esa cuenta no existe", 404)
    if person.is_superadmin:
        raise AppError("esa cuenta es el super administrador", 400)
    if person.is_administrador:
        raise AppError("esa cuenta ya es administradora", 400)
    person.is_administrador = True
    person.save(update_fields=["is_administrador"])
    return _ficha(person)


def _exigir_superadmin(actor: User) -> None:
    """Corta si la sesión no es el superadmin sembrado."""
    if not actor.is_superadmin:
        raise AppError("solo el super administrador puede nombrar administradores", 403)


def _ficha(person: User) -> dict:
    """Datos públicos de una cuenta en el panel del superadmin."""
    return {
        "id": person.id,
        "nombre": f"{person.first_name} {person.last_name}".strip(),
        "identification": person.identification,
        "correo": person.correo,
        "activo": person.is_active,
        "is_administrador": person.is_administrador,
    }
