"""Galpones de la finca.

Propósito: crear, consultar, editar y eliminar cada galpón.
Contexto: al eliminar un galpón se van también sus lotes y su bitácora de cada lote.
@author Cristian Deysdayr Jimenez
"""
from decimal import Decimal

from django.db import IntegrityError, transaction

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.production.avicultura.models import FeedMode, House, HouseStatus, LotStatus
from apps.production.avicultura.services.access import require_membership, require_write

ZERO = Decimal("0")


def list_houses(*, user: User, farm_id: int):
    """Galpones de la finca con el lote abierto, si lo hay."""
    require_membership(user=user, farm_id=farm_id)
    return House.objects.filter(farm_id=farm_id).prefetch_related("lots")


def create_house(*, user: User, farm_id: int, **data) -> House:
    """Crea un galpón con los datos reales que registre la finca.

    Los datos que no se conozcan quedan vacíos; el sistema no los inventa.
    """
    membership = require_membership(user=user, farm_id=farm_id)
    require_write(membership)
    name = _name(data["name"])
    status = data.get("status") or HouseStatus.DISPONIBLE
    if status not in HouseStatus.values:
        raise AppError("estado de galpón no válido", 400)
    feed_mode = (data.get("feed_mode") or "").strip()
    if feed_mode and feed_mode not in FeedMode.values:
        raise AppError("forma de suministro no válida", 400)
    try:
        return House.objects.create(
            farm_id=membership.farm_id,
            name=name,
            location=(data.get("location") or "").strip()[:120],
            length_m=_optional_positive(data.get("length_m"), "el largo"),
            width_m=_optional_positive(data.get("width_m"), "el ancho"),
            capacity=_optional_birds(data.get("capacity"), "la capacidad"),
            usual_birds=_optional_birds(data.get("usual_birds"), "el aforo habitual"),
            equipment=(data.get("equipment") or "").strip(),
            status=status,
            bale_kg=_optional_positive(data.get("bale_kg"), "el peso del bulto"),
            bales_per_drum=_optional_positive(
                data.get("bales_per_drum"), "los bultos por caneca",
            ),
            feed_mode=feed_mode,
            cycle_days=_optional_birds(data.get("cycle_days"), "los días del lote"),
            mortality_alert=_optional_birds(data.get("mortality_alert"), "la alerta"),
        )
    except IntegrityError as exc:
        raise AppError("ya existe un galpón con ese nombre", 409) from exc


def update_house(*, user: User, farm_id: int, house_id: int, **data) -> House:
    """Cambia datos. Con lote abierto el galpón sigue ocupado.

    Los campos opcionales se pueden vaciar enviando None o una cadena vacía.
    """
    membership = require_membership(user=user, farm_id=farm_id)
    require_write(membership)
    house = _house(farm_id, house_id)
    if "status" in data and data["status"]:
        if data["status"] not in HouseStatus.values:
            raise AppError("estado de galpón no válido", 400)
        open_lot = house.lots.filter(status=LotStatus.ABIERTO).exists()
        if open_lot and data["status"] != HouseStatus.OCUPADO:
            raise AppError("hay un lote abierto: cierra el lote antes de cambiar el estado", 400)
        house.status = data["status"]
    if "name" in data and data["name"] is not None:
        house.name = _name(data["name"])
    if "location" in data and data["location"] is not None:
        house.location = data["location"].strip()[:120]
    if "equipment" in data and data["equipment"] is not None:
        house.equipment = data["equipment"].strip()
    for field, label in (
        ("length_m", "el largo"),
        ("width_m", "el ancho"),
        ("bale_kg", "el peso del bulto"),
        ("bales_per_drum", "los bultos por caneca"),
    ):
        if field in data:
            setattr(house, field, _optional_positive(data[field], label))
    for field, label in (
        ("capacity", "la capacidad"),
        ("usual_birds", "el aforo habitual"),
        ("cycle_days", "los días del lote"),
        ("mortality_alert", "la alerta"),
    ):
        if field in data:
            setattr(house, field, _optional_birds(data[field], label))
    if "feed_mode" in data:
        feed_mode = (data["feed_mode"] or "").strip()
        if feed_mode and feed_mode not in FeedMode.values:
            raise AppError("forma de suministro no válida", 400)
        house.feed_mode = feed_mode
    try:
        house.save()
    except IntegrityError as exc:
        raise AppError("ya existe un galpón con ese nombre", 409) from exc
    return house


def delete_house(*, user: User, farm_id: int, house_id: int) -> None:
    """Elimina un galpón con sus lotes y la bitácora de cada lote."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_write(membership)
    with transaction.atomic():
        house = _house(farm_id, house_id)
        house.lots.all().delete()
        house.delete()


def house_payload(house: House) -> dict:
    """Ficha lista para la API, con área y lote abierto."""
    open_lot = next((lot for lot in house.lots.all() if lot.status == LotStatus.ABIERTO), None)
    area = house.area_m2
    density = None
    if open_lot is not None and area:
        density = (Decimal(open_lot.live_count) / area).quantize(Decimal("0.01"))
    return {
        "id": house.id,
        "name": house.name,
        "location": house.location,
        "length_m": decimal_text(house.length_m),
        "width_m": decimal_text(house.width_m),
        "area_m2": decimal_text(area),
        "capacity": house.capacity,
        "usual_birds": house.usual_birds,
        "equipment": house.equipment,
        "status": house.status,
        "bale_kg": decimal_text(house.bale_kg),
        "bales_per_drum": decimal_text(house.bales_per_drum),
        "feed_mode": house.feed_mode,
        "cycle_days": house.cycle_days,
        "mortality_alert": house.mortality_alert,
        "open_lot_id": open_lot.id if open_lot else None,
        "open_lot_code": open_lot.code if open_lot else "",
        "live_count": open_lot.live_count if open_lot else None,
        "birds_per_m2": decimal_text(density),
        "lots_count": len(house.lots.all()),
    }


def decimal_text(value) -> str | None:
    """Decimal en texto, sin ceros a la derecha: 2, no 2.00."""
    if value is None:
        return None
    text = format(Decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _house(farm_id: int, house_id: int) -> House:
    house = House.objects.filter(id=house_id, farm_id=farm_id).first()
    if house is None:
        raise AppError("galpón no encontrado", 404)
    return house


def _name(value: str) -> str:
    clean = (value or "").strip()
    if not clean:
        raise AppError("el nombre del galpón es obligatorio", 400)
    return clean[:80]


def _positive(value, label: str) -> Decimal:
    amount = Decimal(value)
    if amount <= ZERO:
        raise AppError(f"{label} debe ser mayor que cero", 400)
    return amount


def _optional_positive(value, label: str) -> Decimal | None:
    """Valida un decimal solo cuando la finca lo registra."""
    if value is None or value == "":
        return None
    return _positive(value, label)


def _birds(value, label: str) -> int:
    count = int(value)
    if count <= 0:
        raise AppError(f"{label} debe ser mayor que cero", 400)
    return count


def _optional_birds(value, label: str) -> int | None:
    """Valida un entero solo cuando la finca lo registra."""
    if value is None or value == "":
        return None
    return _birds(value, label)