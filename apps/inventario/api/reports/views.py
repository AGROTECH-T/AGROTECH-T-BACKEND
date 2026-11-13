"""Reportes del inventario en JSON.

Propósito: servir ajustes, consumo, préstamos y pedido sugerido.
@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.accounts.errors import AppError
from apps.inventario.api.movements.query import optional_date
from apps.inventario.services import reports as report_services
class AdjustmentReportView(APIView):
    """Reconteos del rango, con motivo y diferencia."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int) -> Response:
        data = report_services.adjustment_report(
            user=request.user,
            farm_id=farm_id,
            date_from=optional_date(request.query_params.get("from"), "la fecha inicial"),
            date_to=optional_date(request.query_params.get("to"), "la fecha final"),
            product_id=read_product(request.query_params.get("product")),
            reason=request.query_params.get("reason") or None,
        )
        return Response(with_iso_dates(data))
class ConsumptionReportView(APIView):
    """Productos con más consumo, comparación y tendencia."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int) -> Response:
        data = report_services.consumption_report(
            user=request.user,
            farm_id=farm_id,
            date_from=optional_date(request.query_params.get("from"), "la fecha inicial"),
            date_to=optional_date(request.query_params.get("to"), "la fecha final"),
            category_code=request.query_params.get("category") or None,
            product_id=read_product(request.query_params.get("product")),
        )
        return Response(data)
class LoanReportView(APIView):
    """Préstamos de la finca y lo que sigue pendiente."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int) -> Response:
        data = report_services.loan_report(
            user=request.user,
            farm_id=farm_id,
            status=request.query_params.get("status") or "all",
            date_from=optional_date(request.query_params.get("from"), "la fecha inicial"),
            date_to=optional_date(request.query_params.get("to"), "la fecha final"),
            person=request.query_params.get("person") or None,
            product_id=read_product(request.query_params.get("product")),
        )
        return Response(with_iso_dates(data))
def read_product(raw: str | None) -> int | None:
    """Lee el identificador del producto o nada."""
    if not raw:
        return None
    if not raw.isdigit():
        raise AppError("el producto no es válido", 400)
    return int(raw)
def flag(raw: str | None) -> bool:
    """Lee un sí/no de la dirección."""
    return (raw or "").strip().lower() in ("1", "true", "si", "sí")
def with_iso_dates(data: dict) -> dict:
    """Convierte las fechas de las filas a texto ISO."""
    for row in data.get("rows", []):
        when = row.get("when")
        if when is not None:
            row["when"] = when.isoformat()
    return data
