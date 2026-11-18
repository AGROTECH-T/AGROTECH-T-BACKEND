"""Comando de gestión para el consumidor MQTT de ingesta IoT."""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand, CommandError

from apps.iot.broker_identity_config import load_broker_identity_config
from apps.iot.db_guard import require_read_committed
from apps.iot.mqtt.consumer import (
    MqttConfigurationError,
    MqttConsumer,
    MqttConsumerFatalError,
    MqttSettings,
)
from apps.iot.process_signals import graceful_stop

logger = logging.getLogger(__name__)


def _describe_identity(identity) -> str:
    """Modo y versiones de clave; nunca valores."""
    if identity.keyring is None:
        return f"modo={identity.mode.value}"
    return f"modo={identity.mode.value} {identity.keyring.describe()}"


class Command(BaseCommand):
    help = "Recibe y persiste de forma durable tramas IoT desde MQTT."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help=(
                "Valida la configuración MQTT sin abrir la conexión. Con "
                "DJANGO_DEBUG=false aplica las reglas de producción (TLS obligatorio)."
            ),
        )

    def handle(self, *args, **options):
        # BUILD-FLEPSI-02: enforce or an invalid identity key fails here with
        # ImproperlyConfigured (exit status 1) before any connection.
        identity = load_broker_identity_config()
        # BUILD-FLEPSI-02K (K-H1): la estrategia de bloqueo exige READ COMMITTED.
        require_read_committed()
        try:
            settings = MqttSettings.from_env()
        except MqttConfigurationError as exc:
            raise CommandError(str(exc)) from exc
        description = _describe_identity(identity)
        if options["check"]:
            self.stdout.write(f"Configuración MQTT válida. Identidad del broker: {description}.")
            return
        logger.info("identidad del broker: %s", description)
        consumer = MqttConsumer(settings, broker_identity=identity)
        try:
            with graceful_stop(consumer.stop, process="consumidor MQTT"):
                consumer.run()
        except MqttConsumerFatalError as exc:
            # CommandError -> exit status 1, visible to the process supervisor.
            raise CommandError(f"Consumidor MQTT detenido por fallo fatal: {exc}") from exc
