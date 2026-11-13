"""Retira a una cuenta la capacidad global de crear fincas (DEC-11)."""
from apps.accounts.management.commands._farm_creation import FarmCreationCommand


class Command(FarmCreationCommand):
    """manage.py revoke_farm_creation --identification <documento> [--reason ...]."""

    help = "Retira a la cuenta indicada la capacidad de crear fincas."
    allowed = False
