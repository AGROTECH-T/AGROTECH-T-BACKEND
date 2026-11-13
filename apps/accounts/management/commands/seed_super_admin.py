"""Comando para sembrar el super administrador.

Propósito: conservar el atajo que siembra las cuentas iniciales.
Contexto: los datos salen de apps.accounts.seeders, no del entorno.
@author Cristian Deysdayr Jimenez
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Crea o actualiza el super administrador de la plataforma."""

    help = "Siembra las cuentas iniciales del archivo seeders."

    def handle(self, *args, **options):
        call_command("seed_usuarios", stdout=self.stdout, stderr=self.stderr)
