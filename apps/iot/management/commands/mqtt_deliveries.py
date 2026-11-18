"""Operación segura de la bandeja MQTT durable (HARDEN-01).

Subcomandos de solo lectura: ``status``, ``list``, ``pauses``.
Subcomandos con escritura auditada y alcance único: ``pause``, ``resume``,
``resolve``. Ninguno muestra payloads, ciphertext ni claves y ninguno actúa
sobre varias entregas a la vez. No hay endpoint HTTP equivalente.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from uuid import UUID

from django.core.management.base import BaseCommand, CommandError

from apps.iot.models import IotMqttDeliveryState as State
from apps.iot.services.mqtt_delivery_ops import (
    DeadLetterResolutionError,
    ResolutionAction,
    delivery_events,
    delivery_summary,
    list_deliveries,
    list_pauses,
    resolve_dead_letter,
)
from apps.iot.services.mqtt_delivery_pauses import (
    pause_mqtt_deliveries,
    resume_mqtt_deliveries,
)
from apps.iot.services.mqtt_delivery_processing import DeliveryNotFoundError

_LIST_STATES = {
    "retry_pending": State.RETRY_PENDING,
    "dead_letter": State.DEAD_LETTER,
    "processing": State.PROCESSING,
    "received": State.RECEIVED,
}


class Command(BaseCommand):
    help = "Inspecciona y opera la bandeja MQTT durable (sin endpoints públicos)."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)

        status = sub.add_parser("status", help="Estado de una entrega (solo lectura).")
        status.add_argument("delivery_id")
        status.add_argument("--events", action="store_true", help="Incluye el historial.")

        listing = sub.add_parser("list", help="Lista entregas por estado (solo lectura).")
        listing.add_argument("--state", required=True, choices=sorted(_LIST_STATES))
        listing.add_argument("--limit", type=int, default=50)

        pauses = sub.add_parser("pauses", help="Lista pausas (solo lectura).")
        pauses.add_argument("--all", action="store_true", help="Incluye pausas cerradas.")
        pauses.add_argument("--limit", type=int, default=50)

        scoped_actions = (
            ("pause", "Pausa el procesamiento."),
            ("resume", "Reanuda el procesamiento."),
        )
        for name, text in scoped_actions:
            scoped = sub.add_parser(name, help=text)
            scope = scoped.add_mutually_exclusive_group(required=True)
            scope.add_argument("--global", dest="global_scope", action="store_true")
            scope.add_argument("--coordinator-id", type=int)
            scoped.add_argument("--reason", required=True)
            scoped.add_argument("--operator", required=True)

        resolve = sub.add_parser("resolve", help="Resuelve una entrega DEAD_LETTER.")
        resolve.add_argument("delivery_id")
        resolve.add_argument("--action", required=True, choices=[a.value for a in ResolutionAction])
        resolve.add_argument("--reason", required=True)
        resolve.add_argument("--operator", required=True)
        resolve.add_argument(
            "--confirm",
            required=True,
            help="Debe repetir exactamente el identificador de la entrega.",
        )

    def handle(self, *args, **options):
        action = options["action"]
        try:
            if action == "status":
                return self._status(options)
            if action == "list":
                return self._list(options)
            if action == "pauses":
                return self._pauses(options)
            if action in ("pause", "resume"):
                return self._pause_or_resume(action, options)
            return self._resolve(options)
        except (DeliveryNotFoundError, DeadLetterResolutionError, ValueError) as exc:
            raise CommandError(str(exc)) from None

    def _status(self, options):
        delivery_id = _uuid(options["delivery_id"])
        data = asdict(delivery_summary(delivery_id))
        if options["events"]:
            data["events"] = [
                {
                    "occurred_at": event.occurred_at,
                    "event_type": event.event_type,
                    "from_state": event.from_state,
                    "to_state": event.to_state,
                    "attempt_number": event.attempt_number,
                    "worker_id": event.worker_id,
                    "result_code": event.result_code,
                    "safe_detail": event.safe_detail,
                }
                for event in delivery_events(delivery_id)
            ]
        self._emit(data)

    def _list(self, options):
        rows = list_deliveries(state=_LIST_STATES[options["state"]], limit=options["limit"])
        self._emit({"count": len(rows), "deliveries": [asdict(row) for row in rows]})

    def _pauses(self, options):
        rows = list_pauses(include_inactive=options["all"], limit=options["limit"])
        self._emit(
            {
                "count": len(rows),
                "pauses": [
                    {
                        "id": pause.id,
                        "scope": pause.active_scope_key
                        or _closed_scope(pause.coordinator_id),
                        "is_active": pause.is_active,
                        "reason_code": pause.reason_code,
                        "started_at": pause.started_at,
                        "started_process_id": pause.started_process_id,
                        "resumed_at": pause.resumed_at,
                        "resumed_process_id": pause.resumed_process_id,
                    }
                    for pause in rows
                ],
            }
        )

    def _pause_or_resume(self, action, options):
        coordinator_id = None if options["global_scope"] else options["coordinator_id"]
        process_id = _operator_process(options["operator"])
        if action == "pause":
            pause = pause_mqtt_deliveries(
                reason_code=options["reason"],
                coordinator_id=coordinator_id,
                process_id=process_id,
            )
            self._emit({"action": "pause", "pause_id": pause.id, "is_active": pause.is_active})
            return
        pause = resume_mqtt_deliveries(
            reason_code=options["reason"],
            coordinator_id=coordinator_id,
            process_id=process_id,
        )
        self._emit(
            {
                "action": "resume",
                "pause_id": pause.id if pause else None,
                "resumed": pause is not None,
            }
        )

    def _resolve(self, options):
        delivery_id = _uuid(options["delivery_id"])
        if options["confirm"].strip().lower() != str(delivery_id):
            raise CommandError("--confirm debe repetir exactamente el identificador de la entrega.")
        row = resolve_dead_letter(
            delivery_id=delivery_id,
            action=ResolutionAction(options["action"]),
            reason_code=options["reason"],
            operator=options["operator"],
        )
        self._emit({"action": options["action"], "delivery_id": row.id, "state": row.state})

    def _emit(self, data) -> None:
        self.stdout.write(json.dumps(data, default=str, ensure_ascii=False, indent=2))


def _closed_scope(coordinator_id: int | None) -> str:
    return f"COORDINATOR:{coordinator_id}" if coordinator_id else "GLOBAL"


def _uuid(value: str) -> UUID:
    try:
        return UUID(str(value).strip())
    except ValueError:
        raise CommandError("identificador de entrega no válido") from None


def _operator_process(operator: str) -> str:
    if not isinstance(operator, str) or not operator.strip() or len(operator) > 80:
        raise CommandError("--operator no es válido.")
    if not operator.isascii() or not operator.isprintable():
        raise CommandError("--operator no es válido.")
    return f"ops:{operator.strip()}"
