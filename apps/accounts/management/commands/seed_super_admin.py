"""Comando para sembrar el super administrador.

Propósito: subir al sistema la cuenta que nombra administradores.
Contexto: la clave sale del argumento o de SEED_SUPERADMIN_PASSWORD.
@author Cristian Deysdayr Jimenez
"""
import os

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.super_admin_seed import SeedSuperAdminError, seed_super_admin

IDENTIFICACION_DEFECTO = "1000000001"


class Command(BaseCommand):
    """Crea o actualiza el super administrador de la plataforma."""

    help = "Crea el super administrador que nombra a los administradores."

    def add_arguments(self, parser):
        parser.add_argument(
            "--identification",
            default="",
            help="Documento de la cuenta. Si falta, usa SEED_SUPERADMIN_IDENTIFICATION.",
        )
        parser.add_argument(
            "--password",
            default="",
            help="Clave de la cuenta. Si falta, usa SEED_SUPERADMIN_PASSWORD.",
        )
        parser.add_argument("--first-name", default="Super", help="Nombre visible.")
        parser.add_argument("--last-name", default="Admin", help="Apellido visible.")
        parser.add_argument("--correo", default="", help="Correo opcional.")
        parser.add_argument("--phone", default="", help="Celular opcional.")

    def handle(self, *args, **options):
        identification = (
            options["identification"].strip()
            or os.environ.get("SEED_SUPERADMIN_IDENTIFICATION", "").strip()
            or IDENTIFICACION_DEFECTO
        )
        password = options["password"] or os.environ.get("SEED_SUPERADMIN_PASSWORD", "")
        try:
            user = seed_super_admin(
                identification=identification,
                password=password,
                first_name=options["first_name"],
                last_name=options["last_name"],
                correo=options["correo"],
                phone=options["phone"],
            )
        except SeedSuperAdminError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Super administrador listo: {user.identification}."
            )
        )
