"""Bitácora del lote.

Propósito: registrar hechos, corregirlos sin borrar el dato anterior
y mover el saldo solo con bajas, salidas o ajustes justificados.
@author Cristian Deysdayr Jimenez
"""
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.production.avicultura.models import (
    AlertState,
    AttentionLevel,
    AttentionState,
    EventKind,
    EventOrigin,
    Lot,
    LotAlert,
    LotEvent,
    LotStatus,
)
from apps.production.avicultura.services.access import (
    require_close,
    require_membership,
    require_write,
)
from apps.production.avicultura.services.houses import decimal_text
BALANCE = {EventKind.MORTALIDAD, EventKind.DESCARTE, EventKind.SALIDA}
NEEDS_AMOUNT = BALANCE | {
    EventKind.AISLAMIENTO,
    EventKind.AJUSTE,
    EventKind.PESO,
    EventKind.AGUA,
    EventKind.AMBIENTE,
    EventKind.ALIMENTO,
}
COUNT_KINDS = BALANCE | {EventKind.AISLAMIENTO, EventKind.AJUSTE}
def register_event(*, user: User, farm_id: int, lot_id: int, **data) -> LotEvent:
    """Anota un hecho y, si corresponde, actualiza el saldo."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_write(membership)
    kind = data["kind"]
    if kind not in EventKind.values:
        raise AppError("tipo de evento no válido", 400)
    with transaction.atomic():
        lot = (
            Lot.objects.select_for_update()
            .select_related("house")
            .filter(id=lot_id, farm_id=farm_id)
            .first()
        )
        if lot is None:
            raise AppError("lote no encontrado", 404)
        if lot.status != LotStatus.ABIERTO:
            raise AppError("el lote está cerrado", 400)
        value = data.get("observed_value")
        notes = (data.get("notes") or "").strip()
        payload = data.get("payload") or {}
        _check_payload(kind, value, notes, payload, bool(data.get("confirmed")))
        if kind == EventKind.AISLAMIENTO:
            _check_isolation(lot, value, payload)
        _move_stock(lot, kind, value, payload, notes)
        event = LotEvent.objects.create(
            lot=lot,
            kind=kind,
            occurred_at=_aware(data["occurred_at"]),
            responsible=_required(data.get("responsible"), "el responsable"),
            created_by=user,
            observed_value=value,
            unit=(data.get("unit") or "")[:20],
            place=(data.get("place") or "")[:80],
            notes=notes,
            origin=_choice(data.get("origin"), EventOrigin.values, EventOrigin.MANUAL, "origen"),
            attention=_choice(
                data.get("attention"),
                AttentionLevel.values,
                AttentionLevel.INFO,
                "nivel de atención",
            ),
            state=_choice(
                data.get("state"),
                AttentionState.values,
                AttentionState.ABIERTO,
                "estado",
            ),
            action=(data.get("action") or "")[:200],
            assignee=(data.get("assignee") or "")[:80],
            follow_up=data.get("follow_up"),
            estimated=bool(data.get("estimated")),
            confirmed=bool(data.get("confirmed")),
            payload=payload,
        )
        lot.save(update_fields=["live_count"])
        _maybe_alert(lot, event)
    return event
def event_payload(event: LotEvent) -> dict:
    """Evento listo para la API."""
    return {
        "id": event.id,
        "lot_id": event.lot_id,
        "kind": event.kind,
        "occurred_at": event.occurred_at.isoformat(),
        "recorded_at": event.recorded_at.isoformat(),
        "responsible": event.responsible,
        "observed_value": decimal_text(event.observed_value),
        "unit": event.unit,
        "place": event.place,
        "notes": event.notes,
        "origin": event.origin,
        "attention": event.attention,
        "state": event.state,
        "action": event.action,
        "assignee": event.assignee,
        "follow_up": event.follow_up.isoformat() if event.follow_up else None,
        "estimated": event.estimated,
        "confirmed": event.confirmed,
        "payload": event.payload,
        "corrections": [
            {
                "reason": item.reason,
                "at": item.created_at.isoformat(),
                "previous": item.previous,
            }
            for item in event.corrections.all()
        ],
    }
def alert_payload(alert: LotAlert) -> dict:
    """Aviso listo para la API."""
    return {
        "id": alert.id,
        "lot_id": alert.lot_id,
        "lot_code": alert.lot.code,
        "house_name": alert.house.name,
        "motive": alert.motive,
        "assignee": alert.assignee,
        "state": alert.state,
        "follow_up": alert.follow_up,
        "created_at": alert.created_at.isoformat(),
        "closed_at": alert.closed_at.isoformat() if alert.closed_at else None,
    }
def _move_stock(lot: Lot, kind: str, value, payload: dict, notes: str) -> None:
    if kind in BALANCE:
        qty = _birds(value)
        if qty > lot.live_count:
            raise AppError(f"solo hay {lot.live_count} aves vivas", 400)
        lot.live_count -= qty
        return
    if kind != EventKind.AJUSTE:
        return
    if not notes:
        raise AppError("el ajuste requiere una nota", 400)
    qty = _birds(value)
    direction = payload.get("direccion")
    if direction == "sumar":
        lot.live_count += qty
        return
    if direction == "restar":
        if qty > lot.live_count:
            raise AppError(f"solo hay {lot.live_count} aves vivas", 400)
        lot.live_count -= qty
        return
    raise AppError("el ajuste debe sumar o restar", 400)
def _undo(lot: Lot, event: LotEvent) -> None:
    if event.kind in BALANCE:
        lot.live_count += int(event.observed_value or 0)
        return
    if event.kind != EventKind.AJUSTE:
        return
    qty = int(event.observed_value or 0)
    if event.payload.get("direccion") == "sumar":
        if qty > lot.live_count:
            raise AppError("la corrección dejaría el saldo en negativo", 400)
        lot.live_count -= qty
        return
    lot.live_count += qty
def isolation_balance(events) -> int:
    """Aves apartadas menos las que ya volvieron al espacio normal."""
    isolated = 0
    ordered = sorted(events, key=lambda item: item.occurred_at)
    for event in ordered:
        if event.kind != EventKind.AISLAMIENTO:
            continue
        amount = int(event.observed_value or 0)
        if (event.payload or {}).get("direccion") == "devolver":
            isolated -= amount
        else:
            isolated += amount
    return max(isolated, 0)
def _check_isolation(lot: Lot, value, payload: dict, exclude_id: int | None = None) -> None:
    """No se puede devolver más de las aisladas ni apartar más de las que están en el lote."""
    direction = (payload or {}).get("direccion") or "apartar"
    if direction not in {"apartar", "devolver"}:
        raise AppError("el aislamiento debe apartar o devolver", 400)
    qty = _birds(value)
    others = lot.events.all()
    if exclude_id is not None:
        others = others.exclude(id=exclude_id)
    current = isolation_balance(others)
    if direction == "devolver" and qty > current:
        raise AppError(
            f"solo hay {current} aves aisladas para devolver al espacio normal",
            400,
        )
    available = lot.live_count - current
    if direction == "apartar" and qty > available:
        raise AppError(f"solo hay {available} aves en el espacio normal", 400)
def _check_payload(kind, value, notes, payload, confirmed: bool) -> None:
    if kind == EventKind.SANIDAD and confirmed and not payload.get("prescriptor"):
        raise AppError("un dato confirmado de sanidad necesita quién lo indicó", 400)
    smell = kind == EventKind.AMBIENTE and payload.get("amoniaco")
    if smell and not payload.get("amoniaco_medido"):
        if confirmed:
            raise AppError("un olor no es una lectura de amoníaco", 400)
    if kind in NEEDS_AMOUNT and value is None:
        if kind in COUNT_KINDS:
            raise AppError("indica cuántas aves", 400)
        raise AppError("indica la cantidad de este registro", 400)
    if kind == EventKind.AJUSTE and not notes:
        raise AppError("el ajuste requiere una nota", 400)
def _maybe_alert(lot: Lot, event: LotEvent) -> None:
    motives = []
    if event.attention == AttentionLevel.URGENTE:
        motives.append(f"Evento urgente de {event.get_kind_display()}. No es un diagnóstico.")
    threshold = lot.house.mortality_alert
    deaths = int(event.observed_value or 0)
    if event.kind == EventKind.MORTALIDAD and threshold and deaths >= threshold:
        motives.append(
            f"Mortalidad de {int(event.observed_value)} aves, en o sobre el aviso configurado."
        )
    for motive in motives:
        LotAlert.objects.create(
            lot=lot,
            house=lot.house,
            motive=motive[:200],
            assignee=event.assignee,
        )
def _lot(*, user: User, farm_id: int, lot_id: int, write: bool) -> Lot:
    membership = require_membership(user=user, farm_id=farm_id)
    if write:
        require_write(membership)
    lot = Lot.objects.filter(id=lot_id, farm_id=farm_id).first()
    if lot is None:
        raise AppError("lote no encontrado", 404)
    return lot
def _snapshot(event: LotEvent) -> dict:
    return {
        "observed_value": decimal_text(event.observed_value),
        "notes": event.notes,
        "payload": event.payload,
        "estimated": event.estimated,
        "confirmed": event.confirmed,
    }
def _birds(value) -> int:
    if value is None:
        raise AppError("indica cuántas aves", 400)
    amount = Decimal(value)
    if amount != amount.to_integral_value() or amount <= 0:
        raise AppError("la cantidad de aves debe ser un entero mayor que cero", 400)
    return int(amount)
def _required(value: str | None, label: str) -> str:
    clean = (value or "").strip()
    if not clean:
        raise AppError(f"{label} es obligatorio", 400)
    return clean[:80]
def _choice(value, allowed, default, label: str) -> str:
    if not value:
        return default
    if value not in allowed:
        raise AppError(f"{label} no válido", 400)
    return value
def _aware(value):
    if timezone.is_naive(value):
        return timezone.make_aware(value)
    return value
