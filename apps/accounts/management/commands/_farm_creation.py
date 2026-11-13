"""Base común de los comandos de capacidad de creación de fincas."""
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.errors import AppError
from apps.accounts.services.capability_service import local_executor, set_farm_creation


class FarmCreationCommand(BaseCommand):
    """Concede o retira ``can_create_farms`` a una cuenta por su documento.

    ``--reason`` es obligatorio y no puede quedar vacío. El evento registra el
    usuario del sistema operativo que ejecuta el comando (referencia local,
    no identidad autenticada de AGROTECH).
    """

    allowed: bool = True

    def add_arguments(self, parser):
        """Declara el documento y el motivo, ambos obligatorios."""
        parser.add_argument("--identification", required=True, help="Documento de la cuenta.")
        parser.add_argument(
            "--reason", required=True, help="Motivo registrado en la auditoría (obligatorio)."
        )

    def handle(self, *args, **options):
        """Aplica el cambio e informa si hubo modificación."""
        if not (options["reason"] or "").strip():
            raise CommandError("--reason no puede estar vacío.")
        try:
            changed = set_farm_creation(
                identification=options["identification"],
                allowed=self.allowed,
                reason=options["reason"],
                executed_by=local_executor(),
            )
        except AppError as exc:
            raise CommandError(exc.message) from exc
        state = "concedida" if self.allowed else "retirada"
        if changed:
            self.stdout.write(self.style.SUCCESS(f"Capacidad de crear fincas {state}."))
        else:
            self.stdout.write(f"Sin cambios: la capacidad ya estaba {state}.")
