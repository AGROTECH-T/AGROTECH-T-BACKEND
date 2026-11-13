"""Reportes del inventario.

Propósito: ajustes, consumo, préstamos y pedido sugerido con sus filtros.
Contexto: solo lectura; reutiliza los permisos del inventario y el stock
que ya está en los movimientos, sin guardar cifras aparte.
@author Cristian Deysdayr Jimenez
"""
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import F, Sum

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.inventario.catalog import plain_amount
from apps.inventario.models import (
    AdjustmentReason,
    Movement,
    MovementKind,
    Product,
    ProductLoan,
)
from apps.inventario.services import products as product_services
from apps.inventario.services.membership import require_membership
from apps.inventario.services.permissions import READ, require_action

ZERO = Decimal("0")
MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _guard(
    *,
    user: User,
    farm_id: int,
    date_from: date | None,
    date_to: date | None,
):
    """Exige lectura de la finca y un rango de fechas coherente."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, READ)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise AppError("el rango de fechas no es válido", 400)
    return membership


def _in_range(queryset, date_from: date | None, date_to: date | None):
    """Recorta el queryset al rango de fechas, si lo hay."""
    if date_from is not None:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to is not None:
        queryset = queryset.filter(created_at__date__lte=date_to)
    return queryset


def _user_name(user: User) -> str:
    """Nombre visible de quien registró el movimiento."""
    return f"{user.first_name} {user.last_name}".strip() or user.identification


def _reason_label(reason: str) -> str:
    """Texto del motivo, o aviso cuando el ajuste es viejo."""
    if not reason:
        return "Sin motivo"
    return AdjustmentReason(reason).label


def adjustment_report(
    *,
    user: User,
    farm_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    product_id: int | None = None,
    reason: str | None = None,
) -> dict:
    """Reconteos hechos en el rango, con su motivo y su diferencia."""
    _guard(user=user, farm_id=farm_id, date_from=date_from, date_to=date_to)
    if reason and reason not in AdjustmentReason.values:
        raise AppError("motivo de ajuste inválido", 400)
    queryset = Movement.objects.filter(
        product__farm_id=farm_id,
        kind=MovementKind.AJUSTE,
    ).select_related("product", "created_by", "unit")
    queryset = _in_range(queryset, date_from, date_to)
    if product_id is not None:
        queryset = queryset.filter(product_id=product_id)
    if reason:
        queryset = queryset.filter(reason=reason)

    rows = []
    up = 0
    down = 0
    for move in queryset:
        difference = move.stock_after - move.stock_before
        if difference > ZERO:
            up += 1
        elif difference < ZERO:
            down += 1
        rows.append(
            {
                "id": move.id,
                "when": move.created_at,
                "product_id": move.product_id,
                "product": move.product.name,
                "reason": move.reason,
                "reason_label": _reason_label(move.reason),
                "note": move.note,
                "before": plain_amount(move.stock_before),
                "after": plain_amount(move.stock_after),
                "difference": plain_amount(difference),
                "unit": move.unit.label,
                "user": _user_name(move.created_by),
            }
        )
    return {"rows": rows, "count": len(rows), "up": up, "down": down}


def consumption_report(
    *,
    user: User,
    farm_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    category_code: str | None = None,
    product_id: int | None = None,
) -> dict:
    """Qué producto se gastó más en el rango, con comparación y tendencia."""
    _guard(user=user, farm_id=farm_id, date_from=date_from, date_to=date_to)
    queryset = _consumption_query(
        farm_id=farm_id,
        date_from=date_from,
        date_to=date_to,
        category_code=category_code,
        product_id=product_id,
    )
    grouped: dict[int, dict] = {}
    for move in queryset:
        item = grouped.setdefault(
            move.product_id,
            {
                "product_id": move.product_id,
                "product": move.product.name,
                "category": move.product.category.name,
                "unit": move.unit.label,
                "base_symbol": move.unit.base_unit.symbol,
                "quantity": ZERO,
                "base": ZERO,
                "moves": 0,
            },
        )
        item["quantity"] += move.quantity
        item["base"] += move.quantity_base
        item["moves"] += 1

    total = sum((item["base"] for item in grouped.values()), ZERO)
    by_symbol: dict[str, Decimal] = {}
    for item in grouped.values():
        by_symbol[item["base_symbol"]] = by_symbol.get(item["base_symbol"], ZERO) + item["base"]

    previous = _previous_consumption(
        farm_id=farm_id,
        date_from=date_from,
        date_to=date_to,
        category_code=category_code,
        product_id=product_id,
    )
    rows = []
    has_previous = previous["available"]
    for item in grouped.values():
        before = previous["by_product"].get(item["product_id"], ZERO)
        rows.append(
            {
                "product_id": item["product_id"],
                "product": item["product"],
                "category": item["category"],
                "unit": item["unit"],
                "base_symbol": item["base_symbol"],
                "quantity": plain_amount(item["quantity"]),
                "base": plain_amount(item["base"]),
                "moves": item["moves"],
                "share": _share(item["base"], total),
                "previous": plain_amount(before) if has_previous else None,
                "variation": _variation(item["base"], before) if has_previous else None,
            }
        )
    rows.sort(key=lambda row: row["share"], reverse=True)
    return {
        "rows": rows,
        "count": len(rows),
        "total": plain_amount(total),
        "by_symbol": [
            {"symbol": symbol, "base": plain_amount(value)}
            for symbol, value in sorted(by_symbol.items())
        ],
        "previous": {
            "available": has_previous,
            "from": previous["from"],
            "to": previous["to"],
            "total": plain_amount(previous["total"]) if has_previous else None,
        },
        "trend": _trend(
            farm_id=farm_id,
            category_code=category_code,
            product_id=product_id,
        ),
        "flow": _flow(
            farm_id=farm_id,
            date_from=date_from,
            date_to=date_to,
            category_code=category_code,
            product_id=product_id,
        ),
    }


def loan_report(
    *,
    user: User,
    farm_id: int,
    status: str = "all",
    date_from: date | None = None,
    date_to: date | None = None,
    person: str | None = None,
    product_id: int | None = None,
) -> dict:
    """Préstamos de toda la finca, con lo que sigue pendiente."""
    _guard(user=user, farm_id=farm_id, date_from=date_from, date_to=date_to)
    if status not in ("all", "open", "closed"):
        raise AppError("estado inválido", 400)
    queryset = ProductLoan.objects.filter(product__farm_id=farm_id).select_related(
        "product",
        "product__unit",
    )
    queryset = _in_range(queryset, date_from, date_to)
    if status == "open":
        queryset = queryset.filter(returned_quantity__lt=F("quantity"))
    elif status == "closed":
        queryset = queryset.filter(returned_quantity__gte=F("quantity"))
    clean_person = (person or "").strip()
    if clean_person:
        queryset = queryset.filter(borrower_name__icontains=clean_person)
    if product_id is not None:
        queryset = queryset.filter(product_id=product_id)

    rows = []
    people: dict[str, dict] = {}
    open_count = 0
    for loan in queryset:
        pending = loan.quantity - loan.returned_quantity
        if pending > ZERO:
            open_count += 1
            key = loan.document_number
            person_row = people.setdefault(
                key,
                {
                    "person": loan.borrower_name,
                    "document": loan.document_number,
                    "pending": {},
                    "loans": 0,
                },
            )
            unit = loan.product.unit.label
            person_row["pending"][unit] = person_row["pending"].get(unit, ZERO) + pending
            person_row["loans"] += 1
        rows.append(
            {
                "id": loan.id,
                "when": loan.created_at,
                "person": loan.borrower_name,
                "document": loan.document_number,
                "product": loan.product.name,
                "quantity": plain_amount(loan.quantity),
                "returned": plain_amount(loan.returned_quantity),
                "pending": plain_amount(pending),
                "unit": loan.product.unit.label,
                "state": "Pendiente" if pending > ZERO else "Completo",
            }
        )
    return {
        "rows": rows,
        "count": len(rows),
        "open": open_count,
        "people": [
            {
                "person": item["person"],
                "document": item["document"],
                "pending": " · ".join(
                    f"{plain_amount(amount)} {unit}"
                    for unit, amount in sorted(item["pending"].items())
                ),
                "loans": item["loans"],
            }
            for item in sorted(
                people.values(),
                key=lambda row: (-row["loans"], row["person"]),
            )
        ],
    }


def order_report(
    *,
    user: User,
    farm_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    category_code: str | None = None,
    only_low: bool = False,
    target: str = "min",
) -> dict:
    """Inicial contra actual por producto y cuánto conviene pedir.

    El inicial solo se informa cuando sale de un movimiento registrado o
    cuando el producto ya existía y no se movió en el rango. Si no hay
    registro, queda vacío en vez de calcularlo a la inversa.
    """
    _guard(user=user, farm_id=farm_id, date_from=date_from, date_to=date_to)
    if target not in ("min", "doble"):
        raise AppError("objetivo inválido", 400)

    products = product_services.list_products(
        user=user,
        farm_id=farm_id,
        status="all",
        category_code=category_code,
        order="name",
    )
    entries = _movement_totals(
        farm_id=farm_id,
        kind=MovementKind.ENTRADA,
        date_from=date_from,
        date_to=date_to,
    )
    exits = _movement_totals(
        farm_id=farm_id,
        kind=MovementKind.SALIDA,
        date_from=date_from,
        date_to=date_to,
    )
    adjusts = _adjustment_totals(
        farm_id=farm_id,
        date_from=date_from,
        date_to=date_to,
    )
    opening = _opening_stock(
        farm_id=farm_id,
        date_from=date_from,
        date_to=date_to,
    )

    rows = []
    low = 0
    to_order = 0
    for product in products:
        entered = entries.get(product.id, ZERO)
        exited = exits.get(product.id, ZERO)
        adjusted = adjusts.get(product.id, ZERO)
        current = product.quantity
        initial = _initial_stock(
            product=product,
            opening=opening.get(product.id),
            date_from=date_from,
        )
        missing = max(ZERO, product.min_stock - current)
        goal = product.min_stock * 2 if target == "doble" else product.min_stock
        suggested = max(ZERO, goal - current)
        is_low = current <= product.min_stock
        if is_low:
            low += 1
        if suggested > ZERO:
            to_order += 1
        if only_low and not is_low:
            continue
        rows.append(
            {
                "product_id": product.id,
                "product": product.name,
                "category": product.category.name,
                "unit": product.unit.label,
                "initial": None if initial is None else plain_amount(initial),
                "initial_known": initial is not None,
                "moves": opening.get(product.id, {}).get("moves", 0),
                "entries": plain_amount(entered),
                "exits": plain_amount(exited),
                "adjustments": plain_amount(adjusted),
                "current": plain_amount(current),
                "min": plain_amount(product.min_stock),
                "missing": plain_amount(missing),
                "suggested": plain_amount(suggested),
                "lent": plain_amount(product.lent_quantity),
                "is_low": is_low,
            }
        )
    return {
        "rows": rows,
        "count": len(rows),
        "low": low,
        "to_order": to_order,
    }


def _flow(
    *,
    farm_id: int,
    date_from: date | None,
    date_to: date | None,
    category_code: str | None,
    product_id: int | None,
) -> list[dict]:
    """Cuenta entradas y salidas por día, con los mismos filtros del reporte."""
    queryset = Movement.objects.filter(
        product__farm_id=farm_id,
        kind__in=(MovementKind.ENTRADA, MovementKind.SALIDA),
    )
    queryset = _in_range(queryset, date_from, date_to)
    if category_code:
        queryset = queryset.filter(product__category__code=category_code)
    if product_id is not None:
        queryset = queryset.filter(product_id=product_id)

    counts: dict[date, dict[str, int]] = {}
    for created_at, kind in queryset.values_list("created_at", "kind"):
        bucket = counts.setdefault(created_at.date(), {"entradas": 0, "salidas": 0})
        if kind == MovementKind.ENTRADA:
            bucket["entradas"] += 1
        else:
            bucket["salidas"] += 1

    if not counts and (date_from is None or date_to is None):
        return []
    start = date_from or min(counts)
    end = date_to or max(counts)
    rows = []
    day = start
    while day <= end:
        bucket = counts.get(day, {"entradas": 0, "salidas": 0})
        rows.append(
            {
                "day": day.isoformat(),
                "label": day.strftime("%d/%m"),
                "entradas": bucket["entradas"],
                "salidas": bucket["salidas"],
            }
        )
        day += timedelta(days=1)
        if len(rows) > 370:
            break
    return rows


def _consumption_query(
    *,
    farm_id: int,
    date_from: date | None,
    date_to: date | None,
    category_code: str | None,
    product_id: int | None,
):
    """Salidas del rango con sus datos de producto y unidad."""
    queryset = Movement.objects.filter(
        product__farm_id=farm_id,
        kind=MovementKind.SALIDA,
    ).select_related("product", "product__category", "unit", "unit__base_unit")
    queryset = _in_range(queryset, date_from, date_to)
    if category_code:
        queryset = queryset.filter(product__category__code=category_code)
    if product_id is not None:
        queryset = queryset.filter(product_id=product_id)
    return queryset


def _previous_consumption(
    *,
    farm_id: int,
    date_from: date | None,
    date_to: date | None,
    category_code: str | None,
    product_id: int | None,
) -> dict:
    """Consumo del periodo anterior de igual duración."""
    if date_from is None or date_to is None:
        return {
            "available": False,
            "from": None,
            "to": None,
            "total": ZERO,
            "by_product": {},
        }
    days = (date_to - date_from).days + 1
    previous_to = date_from - timedelta(days=1)
    previous_from = previous_to - timedelta(days=days - 1)
    queryset = _consumption_query(
        farm_id=farm_id,
        date_from=previous_from,
        date_to=previous_to,
        category_code=category_code,
        product_id=product_id,
    )
    by_product: dict[int, Decimal] = {}
    total = ZERO
    for move in queryset:
        by_product[move.product_id] = by_product.get(move.product_id, ZERO) + move.quantity_base
        total += move.quantity_base
    return {
        "available": True,
        "from": previous_from.isoformat(),
        "to": previous_to.isoformat(),
        "total": total,
        "by_product": by_product,
    }


def _trend(
    *,
    farm_id: int,
    category_code: str | None,
    product_id: int | None,
) -> list[dict]:
    """Consumo de los últimos seis meses, mes a mes."""
    today = date.today()
    months = []
    year, month = today.year, today.month
    for _ in range(6):
        months.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    months.reverse()

    start_year, start_month = months[0]
    start = date(start_year, start_month, 1)
    queryset = _consumption_query(
        farm_id=farm_id,
        date_from=start,
        date_to=today,
        category_code=category_code,
        product_id=product_id,
    )
    buckets: dict[tuple[int, int], dict[str, Decimal]] = {}
    for move in queryset:
        key = (move.created_at.year, move.created_at.month)
        symbol = move.unit.base_unit.symbol
        bucket = buckets.setdefault(key, {})
        bucket[symbol] = bucket.get(symbol, ZERO) + move.quantity_base

    rows = []
    for year, month in months:
        bucket = buckets.get((year, month), {})
        rows.append(
            {
                "month": f"{year}-{month:02d}",
                "label": f"{MONTHS[month - 1]} {year}",
                "current": (year, month) == (today.year, today.month),
                "items": [
                    {"symbol": symbol, "base": plain_amount(value)}
                    for symbol, value in sorted(bucket.items())
                ],
            }
        )
    return rows


def _movement_totals(
    *,
    farm_id: int,
    kind: str,
    date_from: date | None,
    date_to: date | None,
) -> dict[int, Decimal]:
    """Suma las cantidades de un tipo de movimiento por producto."""
    queryset = Movement.objects.filter(product__farm_id=farm_id, kind=kind)
    queryset = _in_range(queryset, date_from, date_to)
    totals = queryset.values("product_id").annotate(total=Sum("quantity"))
    return {row["product_id"]: row["total"] or ZERO for row in totals}


def _opening_stock(
    *,
    farm_id: int,
    date_from: date | None,
    date_to: date | None,
) -> dict[int, dict]:
    """Stock real al abrir el rango, según el primer movimiento registrado."""
    queryset = _in_range(
        Movement.objects.filter(product__farm_id=farm_id),
        date_from,
        date_to,
    ).order_by("created_at", "id")
    bounds: dict[int, dict] = {}
    for row in queryset.values("product_id", "stock_before", "stock_after"):
        item = bounds.get(row["product_id"])
        if item is None:
            bounds[row["product_id"]] = {
                "opening": row["stock_before"],
                "moves": 1,
            }
        else:
            item["moves"] += 1
    return bounds


def _initial_stock(
    *,
    product: Product,
    opening: dict | None,
    date_from: date | None,
) -> Decimal | None:
    """Stock inicial comprobable, o nada cuando no hay registro.

    Con movimientos en el rango se toma el stock previo al primero. Sin
    movimientos, el stock no cambió: sirve el actual, siempre que el
    producto ya existiera al abrir el rango. Sin fecha inicial no hay
    apertura que informar.
    """
    if date_from is None:
        return None
    if opening is not None:
        return opening["opening"]
    if product.created_at.date() <= date_from:
        return product.quantity
    return None


def _adjustment_totals(
    *,
    farm_id: int,
    date_from: date | None,
    date_to: date | None,
) -> dict[int, Decimal]:
    """Suma la diferencia de los ajustes por producto."""
    queryset = Movement.objects.filter(
        product__farm_id=farm_id,
        kind=MovementKind.AJUSTE,
    )
    queryset = _in_range(queryset, date_from, date_to)
    totals = queryset.values("product_id").annotate(total=Sum(F("stock_after") - F("stock_before")))
    return {row["product_id"]: row["total"] or ZERO for row in totals}


def _share(value: Decimal, total: Decimal) -> float:
    """Porcentaje del total, con un decimal."""
    if total <= ZERO:
        return 0.0
    return round(float(value / total * 100), 1)


def _variation(current: Decimal, previous: Decimal) -> float | None:
    """Variación contra el periodo anterior, en porcentaje."""
    if previous <= ZERO:
        return None
    return round(float((current - previous) / previous * 100), 1)


def products_for_filter(*, user: User, farm_id: int) -> list[Product]:
    """Productos de la finca para los selectores de los reportes."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, READ)
    return list(
        Product.objects.filter(farm_id=farm_id, is_removed=False)
        .select_related("unit")
        .order_by("name")
    )
