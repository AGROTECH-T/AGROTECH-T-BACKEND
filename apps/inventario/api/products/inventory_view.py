"""Excel con el inventario de la finca.

Propósito: descargar el stock actual de los productos que se están viendo,
con sus préstamos y su estado.
@author Cristian Deysdayr Jimenez
"""
from datetime import date
from decimal import Decimal

from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.accounts.errors import AppError
from apps.inventario.catalog import plain_amount
from apps.inventario.services import products as product_services
from apps.inventario.services.membership import require_membership
from apps.inventario.services.sheet import book

ZERO = Decimal("0")

HEADERS = [
    "Producto",
    "Código de barras",
    "Categoría",
    "Unidad",
    "Stock actual",
    "Equivalencia",
    "Prestado",
    "Disponible",
    "Stock mínimo",
    "Estado del stock",
    "Estado",
    "Último movimiento",
]


class ProductInventoryFileView(APIView):
    """Descarga el inventario de la finca en Excel."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int) -> HttpResponse:
        membership = require_membership(user=request.user, farm_id=farm_id)
        products = product_services.list_products(
            user=request.user,
            farm_id=farm_id,
            status="all",
            order="name",
        )
        wanted = _ids(request.query_params.get("ids"))
        if wanted:
            products = [item for item in products if item.id in wanted]
        items = sorted(products, key=lambda item: (item.category.name.lower(), item.name.lower()))

        rows = [_columns(item) for item in items]
        payload = book(
            [
                ("Inventario", HEADERS, rows),
                ("Resumen", ["Dato", "Valor"], _summary(membership.farm.name, items)),
            ]
        )
        response = HttpResponse(
            payload,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="inventario-{farm_id}.xlsx"'
        return response


def _ids(raw: str | None) -> set[int]:
    """Lee la lista de productos pedidos desde la pantalla."""
    if not raw:
        return set()
    values = set()
    for part in raw.split(","):
        text = part.strip()
        if not text:
            continue
        if not text.isdigit():
            raise AppError("la lista de productos no es válida", 400)
        values.add(int(text))
    return values


def _columns(product) -> list[str]:
    """Una fila del reporte."""
    return [
        product.name,
        product.barcode,
        product.category.name,
        _unit_text(product),
        plain_amount(product.quantity),
        _equivalent(product),
        plain_amount(product.lent_quantity),
        plain_amount(product.quantity - product.lent_quantity),
        plain_amount(product.min_stock),
        _stock_state(product),
        "Activo" if product.is_active else "Inactivo",
        _when(product.last_movement_at),
    ]


def _unit_text(product) -> str:
    """Nombre de la unidad con lo que trae, como Bulto (50 kg)."""
    label = product.unit.label
    if product.unit.factor == 1:
        return label
    content = f"{plain_amount(product.unit.factor)} {product.unit.base_unit.symbol}"
    if content.lower() in label.lower():
        return label
    return f"{label} ({content})"


def _equivalent(product) -> str:
    """Cantidad llevada a la medida base, cuando cambia la unidad."""
    unit = product.unit
    if unit.factor == 1 and unit.code == unit.base_unit.code:
        return ""
    return f"{plain_amount(product.quantity * unit.factor)} {unit.base_unit.symbol}"


def _stock_state(product) -> str:
    """Texto corto del estado del stock."""
    if product.quantity == ZERO:
        return "Sin stock"
    if product.quantity <= product.min_stock:
        return "Bajo mínimo"
    return "Normal"


def _when(value) -> str:
    """Fecha y hora del último movimiento."""
    return value.strftime("%d/%m/%Y %H:%M") if value else "Sin movimientos"


def _summary(farm_name: str, products: list) -> list[list[str]]:
    """Datos generales de la descarga."""
    active = sum(1 for item in products if item.is_active)
    empty = sum(1 for item in products if item.quantity == ZERO)
    low = sum(
        1 for item in products if ZERO < item.quantity <= item.min_stock
    )
    return [
        ["Finca", farm_name],
        ["Fecha de descarga", date.today().strftime("%d/%m/%Y")],
        ["Productos", str(len(products))],
        ["Activos", str(active)],
        ["Inactivos", str(len(products) - active)],
        ["Bajo mínimo", str(low)],
        ["Sin stock", str(empty)],
    ]
