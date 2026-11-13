"""Concede a una cuenta la capacidad global de crear fincas (DEC-11)."""
from apps.accounts.management.commands._farm_creation import FarmCreationCommand


class Command(FarmCreationCommand):
    """manage.py grant_farm_creation --identification <documento> [--reason ...]."""

    help = "Concede a la cuenta indicada la capacidad de crear fincas."
    allowed = True
