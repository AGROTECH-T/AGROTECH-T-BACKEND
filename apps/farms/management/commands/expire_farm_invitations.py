"""Comando periódico para expirar invitaciones pendientes vencidas."""
from django.core.management.base import BaseCommand

from apps.farms.invitation_services import expire_pending_invitations


class Command(BaseCommand):
    """Expira invitaciones pendientes vencidas por finca."""

    help = "Expira las invitaciones pendientes cuya vigencia haya finalizado."

    def handle(self, *args, **options):
        """Ejecuta la expiración y reporta el total procesado."""
        expired = expire_pending_invitations()
        self.stdout.write(self.style.SUCCESS(f"{expired} invitaciones expiradas"))
