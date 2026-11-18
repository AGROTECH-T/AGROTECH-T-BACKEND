"""Run the durable IoT MQTT delivery worker."""

from __future__ import annotations

import logging
import threading
import time
from uuid import uuid4

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError

from apps.iot.broker_identity_config import load_broker_identity_config
from apps.iot.db_guard import require_read_committed
from apps.iot.process_signals import graceful_stop
from apps.iot.services.mqtt_delivery_failures import (
    RetryPolicy,
    RetryPolicyError,
)
from apps.iot.services.mqtt_delivery_worker import run_mqtt_delivery_worker_once

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Procesa entregas MQTT IoT ya persistidas de forma durable."

    def add_arguments(self, parser):
        parser.add_argument("--poll-seconds", type=float, default=1.0)
        parser.add_argument("--batch-size", type=int, default=10)
        parser.add_argument("--worker-id", default=None)
        parser.add_argument(
            "--max-attempts",
            type=int,
            default=None,
            help="Intentos antes de DEAD_LETTER (por defecto IOT_MQTT_DELIVERY_MAX_ATTEMPTS u 8).",
        )
        parser.add_argument(
            "--retry-base-seconds",
            type=int,
            default=None,
            help="Espera base del backoff exponencial (por defecto 5).",
        )
        parser.add_argument(
            "--retry-max-seconds",
            type=int,
            default=None,
            help="Espera máxima entre intentos (por defecto 900).",
        )

    def handle(self, *args, **options):
        poll_seconds = options["poll_seconds"]
        batch_size = options["batch_size"]
        if not 0.1 <= poll_seconds <= 60:
            raise CommandError("--poll-seconds debe estar entre 0.1 y 60.")
        if not 1 <= batch_size <= 100:
            raise CommandError("--batch-size debe estar entre 1 y 100.")
        worker_id = options["worker_id"] or f"mqtt-worker:{uuid4()}"
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 100:
            raise CommandError("--worker-id no es válido.")
        policy = _policy(options)
        # BUILD-FLEPSI-02: enforce or an invalid identity key fails with
        # ImproperlyConfigured (exit status 1). The worker itself never needs
        # the key: it compares stored fingerprints.
        identity = load_broker_identity_config()
        # BUILD-FLEPSI-02K (K-H1): la estrategia de bloqueo exige READ COMMITTED.
        require_read_committed()
        logger.info("identidad del broker: modo=%s", identity.mode.value)

        self.stdout.write(
            f"Worker MQTT durable activo: {worker_id} "
            f"(max_attempts={policy.max_attempts}, backoff={policy.base_delay_seconds}s"
            f"..{policy.max_delay_seconds}s)"
        )
        stopping = threading.Event()
        with graceful_stop(stopping.set, process="worker MQTT"):
            # The current batch always finishes (it is bounded); the stop
            # request is honored between batches and interrupts the idle wait.
            while not stopping.is_set():
                try:
                    result = run_mqtt_delivery_worker_once(
                        worker_id=worker_id,
                        batch_size=batch_size,
                        policy=policy,
                    )
                    if result.discovered == 0 or result.processed == 0:
                        _idle(stopping, poll_seconds)
                except KeyboardInterrupt:
                    stopping.set()
                except ImproperlyConfigured:
                    # BUILD-FLEPSI-02H: configuration errors (e.g. enforce)
                    # stop the process with exit status 1; never retried.
                    raise
                except Exception as exc:
                    logger.error("worker MQTT detenido en error operativo (%s)", type(exc).__name__)
                    _idle(stopping, poll_seconds)
        self.stdout.write("Worker MQTT durable detenido.")


def _idle(stopping: threading.Event, seconds: float) -> None:
    """Interruptible wait: a stop request ends it within ~0.5 s.

    Waiting in short slices lets the main thread run Python signal handlers
    on every platform (a long ``Event.wait`` is not interrupted by signals on
    Windows).
    """
    deadline = time.monotonic() + seconds
    while not stopping.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        stopping.wait(min(remaining, 0.5))


def _policy(options) -> RetryPolicy:
    try:
        base = RetryPolicy.from_env()
        return RetryPolicy(
            max_attempts=_pick(options["max_attempts"], base.max_attempts),
            base_delay_seconds=_pick(options["retry_base_seconds"], base.base_delay_seconds),
            max_delay_seconds=_pick(options["retry_max_seconds"], base.max_delay_seconds),
        )
    except RetryPolicyError as exc:
        raise CommandError(f"Política de reintentos no válida: {exc}") from None


def _pick(value, default):
    return default if value is None else value
