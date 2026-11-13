"""Lote de engorde.

Propósito: abrir, consultar y cerrar un engorde sin imponer duraciones.
@author Cristian Deysdayr Jimenez
"""
from datetime import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.production.avicultura.models import (
    EventKind,
    House,
    HouseStatus,
    Lot,
    LotStage,
    LotStatus,
)
from apps.production.avicultura.services.access import (
    require_close,
    require_membership,
    require_write,
)
from apps.production.avicultura.services.events import isolation_balance
from apps.production.avicultura.services.houses import decimal_text

OPEN_HOUSE = {HouseStatus.DISPONIBLE, HouseStatus.OCUPADO}
BALANCE_KINDS = {EventKind.MORTALIDAD, EventKind.DESCARTE, EventKind.SALIDA}


def list_lots(*, user: User, farm_id: int, house_id: int | None = None):
    """Lotes de la finca, el más reciente primero."""
    require_membership(user=user, farm_id=farm_id)
    queryset = Lot.objects.filter(farm_id=farm_id).select_related("house")
    if house_id is not None:
        queryset = queryset.filter(house_id=house_id)
    return queryset


def open_lot(*, user: User, farm_id: int, **data) -> Lot:
    """Recibe un lote y ocupa el galpón."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_write(membership)

    received = _count(data["received"], "las aves recibidas")
    dead = int(data.get("dead_on_arrival") or 0)
    discarded = int(data.get("discarded_on_arrival") or 0)
    if dead < 0 or discarded < 0:
        raise AppError("las bajas iniciales no pueden ser negativas", 400)
    if dead + discarded > received:
        raise AppError("las bajas iniciales superan las aves recibidas", 400)
    stage = data.get("stage") or LotStage.INICIO
    if stage not in LotStage.values:
        raise AppError("etapa no válida", 400)
    arrived = data["arrived_at"]
    if timezone.is_naive(arrived):
        arrived = timezone.make_aware(arrived)

    try:
        with transaction.atomic():
            house = (
                House.objects.select_for_update()
                .filter(id=data["house_id"], farm_id=farm_id)
                .first()
            )
            if house is None:
                raise AppError("galpón no encontrado", 404)
            if house.status not in OPEN_HOUSE:
                raise AppError("el galpón no está disponible para un lote nuevo", 400)
            lot = Lot.objects.create(
                farm_id=farm_id,
                house=house,
                code=_code(data["code"]),
                arrived_at=arrived,
                received=received,
                dead_on_arrival=dead,
                discarded_on_arrival=discarded,
                live_count=received - dead - discarded,
                supplier=(data.get("supplier") or "").strip()[:120],
                documents=(data.get("documents") or "").strip()[:200],
                initial_age_days=int(data.get("initial_age_days") or 0),
                initial_weight=data.get("initial_weight"),
                stage=stage,
                levante_on=data.get("levante_on"),
                engorde_on=data.get("engorde_on"),
                feed_type=(data.get("feed_type") or "").strip()[:80],
                reception_notes=(data.get("reception_notes") or "").strip(),
                receiver_name=_required(data.get("receiver_name"), "quién recibió el lote"),
                created_by=user,
            )
            house.status = HouseStatus.OCUPADO
            house.save(update_fields=["status", "updated_at"])
    except IntegrityError as exc:
        raise AppError("ya existe un lote con ese código", 409) from exc
    return lot


def close_lot(*, user: User, farm_id: int, lot_id: int, note: str = "") -> Lot:
    """Cierra el lote, guarda el resumen y deja el galpón disponible."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_close(membership)
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
            raise AppError("el lote ya está cerrado", 400)
        lot.status = LotStatus.CERRADO
        lot.closed_at = timezone.now()
        lot.close_summary = summarize(lot)
        lot.close_summary["nota_cierre"] = (note or "").strip()[:500]
        lot.save(update_fields=["status", "closed_at", "close_summary"])
        house = lot.house
        quedan_abiertos = house.lots.exclude(id=lot.id).filter(status=LotStatus.ABIERTO).exists()
        if not quedan_abiertos:
            house.status = HouseStatus.DISPONIBLE
            house.save(update_fields=["status", "updated_at"])
    return lot


def update_lot(*, user: User, farm_id: int, lot_id: int, **data) -> Lot:
    """Corrige los datos de recepción del lote sin tocar el saldo de aves."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_write(membership)
    lot = Lot.objects.select_related("house").filter(id=lot_id, farm_id=farm_id).first()
    if lot is None:
        raise AppError("lote no encontrado", 404)

    if data.get("code") is not None:
        lot.code = _code(data["code"])
    if data.get("arrived_at") is not None:
        arrived = data["arrived_at"]
        lot.arrived_at = timezone.make_aware(arrived) if timezone.is_naive(arrived) else arrived
    if data.get("supplier") is not None:
        lot.supplier = data["supplier"].strip()[:120]
    if data.get("documents") is not None:
        lot.documents = data["documents"].strip()[:200]
    if data.get("initial_age_days") is not None:
        lot.initial_age_days = max(int(data["initial_age_days"]), 0)
    if "initial_weight" in data:
        lot.initial_weight = _positive(data["initial_weight"], "el peso inicial")
    if data.get("stage"):
        if data["stage"] not in LotStage.values:
            raise AppError("etapa no válida", 400)
        lot.stage = data["stage"]
    if "levante_on" in data:
        lot.levante_on = data["levante_on"]
    if "engorde_on" in data:
        lot.engorde_on = data["engorde_on"]
    if data.get("feed_type") is not None:
        lot.feed_type = data["feed_type"].strip()[:80]
    if data.get("reception_notes") is not None:
        lot.reception_notes = data["reception_notes"].strip()
    if data.get("receiver_name") is not None:
        lot.receiver_name = _required(data["receiver_name"], "quién recibió el lote")

    try:
        lot.save()
    except IntegrityError as exc:
        raise AppError("ya existe un lote con ese código", 409) from exc
    return lot


def delete_lot(*, user: User, farm_id: int, lot_id: int) -> None:
    """Elimina el lote con su bitácora. El galpón vuelve a quedar disponible."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_write(membership)
    with transaction.atomic():
        lot = (
            Lot.objects.select_for_update()
            .select_related("house")
            .filter(id=lot_id, farm_id=farm_id)
            .first()
        )
        if lot is None:
            raise AppError("lote no encontrado", 404)
        house = lot.house
        was_open = lot.status == LotStatus.ABIERTO
        lot.delete()
        if was_open and house.status == HouseStatus.OCUPADO:
            if not house.lots.filter(status=LotStatus.ABIERTO).exists():
                house.status = HouseStatus.DISPONIBLE
                house.save(update_fields=["status", "updated_at"])


def reopen_lot(*, user: User, farm_id: int, lot_id: int) -> Lot:
    """Reabre un lote que se cerró por error.

    Es la forma de volver atrás: el lote sigue abierto y el galpón vuelve
    a estar ocupado.
    """
    membership = require_membership(user=user, farm_id=farm_id)
    require_close(membership)
    with transaction.atomic():
        lot = (
            Lot.objects.select_for_update()
            .select_related("house")
            .filter(id=lot_id, farm_id=farm_id)
            .first()
        )
        if lot is None:
            raise AppError("lote no encontrado", 404)
        if lot.status != LotStatus.CERRADO:
            raise AppError("ese lote ya está abierto", 400)
        house = lot.house
        lot.status = LotStatus.ABIERTO
        lot.closed_at = None
        lot.save(update_fields=["status", "closed_at"])
        house.status = HouseStatus.OCUPADO
        house.save(update_fields=["status", "updated_at"])
    return lot


def get_lot(*, user: User, farm_id: int, lot_id: int) -> Lot:
    """Un lote de la finca."""
    require_membership(user=user, farm_id=farm_id)
    lot = Lot.objects.select_related("house").filter(id=lot_id, farm_id=farm_id).first()
    if lot is None:
        raise AppError("lote no encontrado", 404)
    return lot


def summarize(lot: Lot) -> dict:
    """Indicadores del lote. Lo estimado no se presenta como exacto."""
    events = list(lot.events.all())
    totals = {kind: 0 for kind in BALANCE_KINDS}
    isolated = min(isolation_balance(events), lot.live_count)
    for event in events:
        amount = int(event.observed_value or 0)
        if event.kind in totals:
            totals[event.kind] += amount
    area = lot.house.area_m2
    density = None
    if area:
        density = (Decimal(lot.live_count) / area).quantize(Decimal("0.01"))
    weights = [event for event in events if event.kind == EventKind.PESO]
    weights.sort(key=lambda item: item.occurred_at)
    last_weight = weights[-1].observed_value if weights else lot.initial_weight
    previous_weight = weights[-2].observed_value if len(weights) > 1 else lot.initial_weight
    weight_change = None
    if last_weight is not None and previous_weight is not None and len(weights) > 0:
        weight_change = last_weight - previous_weight
    feed = _feed(events, lot.house)
    limitation = ""
    conversion = None
    if feed["estimated"]:
        limitation = (
            "Hay alimento estimado. La conversión no se muestra como cifra exacta."
        )
    measured = Decimal(feed["measured_kg"])
    gained_ok = weight_change and weight_change > 0 and lot.live_count
    if not feed["estimated"] and measured > 0 and gained_ok:
        gained = weight_change * Decimal(lot.live_count)
        measured_kg = Decimal(feed["measured_kg"])
        conversion = (measured_kg / gained).quantize(Decimal("0.001"))
    today = timezone.localdate()
    ronda_today = any(
        event.kind == EventKind.RONDA and timezone.localtime(event.occurred_at).date() == today
        for event in events
    )
    pending = []
    if lot.status == LotStatus.ABIERTO and not ronda_today:
        pending.append("No hay ronda registrada hoy.")
    open_alerts = lot.alerts.filter(state="abierta").count()
    return {
        "received": lot.received,
        "live_count": lot.live_count,
        "dead_on_arrival": lot.dead_on_arrival,
        "discarded_on_arrival": lot.discarded_on_arrival,
        "mortality": totals[EventKind.MORTALIDAD],
        "discards": totals[EventKind.DESCARTE],
        "exits": totals[EventKind.SALIDA],
        "isolated": isolated,
        "birds_per_m2": decimal_text(density),
        "age_days": _age(lot),
        "stage": _stage_today(lot),
        "last_weight": decimal_text(last_weight),
        "weight_change": decimal_text(weight_change) if weights else None,
        "weight_is_sample": bool(weights and weights[-1].payload.get("muestra")),
        "feed": feed,
        "conversion": decimal_text(conversion),
        "conversion_note": limitation,
        "water_points": _series(events, EventKind.AGUA),
        "bedding_notes": sum(1 for event in events if event.kind == EventKind.CAMA),
        "open_alerts": open_alerts,
        "pending": pending,
    }


def lot_payload(lot: Lot, *, with_summary: bool = False) -> dict:
    """Ficha del lote."""
    body = {
        "id": lot.id,
        "house_id": lot.house_id,
        "house_name": lot.house.name,
        "code": lot.code,
        "arrived_at": lot.arrived_at.isoformat(),
        "received": lot.received,
        "dead_on_arrival": lot.dead_on_arrival,
        "discarded_on_arrival": lot.discarded_on_arrival,
        "live_count": lot.live_count,
        "supplier": lot.supplier,
        "documents": lot.documents,
        "initial_age_days": lot.initial_age_days,
        "initial_weight": decimal_text(lot.initial_weight),
        "stage": lot.stage,
        "levante_on": _date(lot.levante_on),
        "engorde_on": _date(lot.engorde_on),
        "feed_type": lot.feed_type,
        "reception_notes": lot.reception_notes,
        "receiver_name": lot.receiver_name,
        "status": lot.status,
        "closed_at": lot.closed_at.isoformat() if lot.closed_at else None,
        "close_summary": lot.close_summary,
        "age_days": _age(lot),
    }
    if with_summary:
        body["summary"] = summarize(lot)
    return body


def _feed(events, house) -> dict:
    by_stage = {}
    measured = Decimal("0")
    estimated = False
    for event in events:
        if event.kind != EventKind.ALIMENTO:
            continue
        stage = str(event.payload.get("etapa") or "sin etapa")
        amount = event.observed_value or Decimal("0")
        slot = by_stage.setdefault(stage, {"medido": "0", "estimado": "0"})
        key = "estimado" if event.estimated else "medido"
        slot[key] = decimal_text(Decimal(slot[key]) + amount) or "0"
        if event.estimated:
            estimated = True
        else:
            measured += _to_kg(event, house)
    return {
        "by_stage": by_stage,
        "measured_kg": decimal_text(measured) or "0",
        "estimated": estimated,
    }


def _to_kg(event, house) -> Decimal:
    unit = (event.unit or "").lower()
    amount = event.observed_value or Decimal("0")
    if unit in {"kg", "kilo", "kilos"}:
        return amount
    if house.bale_kg is None:
        return Decimal("0")
    if unit in {"bulto", "bultos"}:
        return amount * house.bale_kg
    if unit in {"caneca", "canecas"}:
        if house.bales_per_drum is None:
            return Decimal("0")
        return amount * house.bales_per_drum * house.bale_kg
    return Decimal("0")


def _series(events, kind: str) -> list[dict]:
    points = [event for event in events if event.kind == kind and event.observed_value is not None]
    points.sort(key=lambda item: item.occurred_at)
    return [
        {
            "at": item.occurred_at.isoformat(),
            "value": decimal_text(item.observed_value),
            "estimated": item.estimated,
        }
        for item in points[-8:]
    ]


def _stage_today(lot: Lot) -> str:
    today = timezone.localdate()
    if lot.engorde_on and today >= lot.engorde_on:
        return LotStage.ENGORDE
    if lot.levante_on and today >= lot.levante_on:
        return LotStage.LEVANTE
    return lot.stage


def _age(lot: Lot) -> int:
    arrived = timezone.localtime(lot.arrived_at).date()
    return lot.initial_age_days + max((timezone.localdate() - arrived).days, 0)


def _count(value, label: str) -> int:
    count = int(value)
    if count <= 0:
        raise AppError(f"{label} deben ser mayores que cero", 400)
    return count


def _positive(value, label: str):
    """Convierte a decimal y rechaza valores negativos."""
    if value is None:
        return None
    amount = Decimal(value)
    if amount < 0:
        raise AppError(f"{label} no puede ser negativo", 400)
    return amount


def _code(value: str) -> str:
    clean = (value or "").strip()
    if not clean:
        raise AppError("el código del lote es obligatorio", 400)
    return clean[:40]


def _required(value: str | None, label: str) -> str:
    clean = (value or "").strip()
    if not clean:
        raise AppError(f"{label} es obligatorio", 400)
    return clean[:80]


def _date(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()
