"""Comando para sembrar las cuentas del archivo seeders.

Propósito: dejar listas al arranque las cuentas iniciales del sistema.
Contexto: se ejecuta después de migrate en el contenedor.
@author Cristian Deysdayr Jimenez
"""
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.super_admin_seed import SeedSuperAdminError, seed_usuarios_iniciales
from apps.farms.seed_demo_farm import DEMO_FARM_NAME


class Command(BaseCommand):
    """Crea o actualiza los usuarios definidos en seeders.py."""

    help = "Siembra las cuentas iniciales y la finca demo K11."

    def handle(self, *args, **options):
        try:
            usuarios = seed_usuarios_iniciales()
        except (SeedSuperAdminError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        for user in usuarios:
            self.stdout.write(
                self.style.SUCCESS(f"Usuario listo: {user.identification}.")
            )
        self.stdout.write(self.style.SUCCESS(f"Finca demo lista: {DEMO_FARM_NAME}."))
