"""Reportes del inventario en Excel.

Propósito: descargar cada reporte con los filtros de la pantalla.
@author Cristian Deysdayr Jimenez
"""
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.views import APIView
from apps.inventario.api.movements.query import optional_date
from apps.inventario.api.reports.views import flag, read_product
from apps.inventario.services import reports as report_services
from apps.inventario.services.sheet import book
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
class AdjustmentReportFileView(APIView):
    """Excel de los reconteos del rango."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int) -> HttpResponse:
        data = report_services.adjustment_report(
            user=request.user,
            farm_id=farm_id,
            date_from=optional_date(request.query_params.get("from"), "la fecha inicial"),
            date_to=optional_date(request.query_params.get("to"), "la fecha final"),
            product_id=read_product(request.query_params.get("product")),
            reason=request.query_params.get("reason") or None,
        )
        rows = [
            [
                row["when"].strftime("%d/%m/%Y"),
                row["when"].strftime("%H:%M"),
                row["product"],
                row["reason_label"],
                row["note"],
                row["before"],
                row["after"],
                row["difference"],
                row["unit"],
                row["user"],
            ]
            for row in data["rows"]
        ]
        payload = book(
            [
                (
                    "Ajustes",
                    [
                        "Fecha",
                        "Hora",
                        "Producto",
                        "Motivo",
                        "Observación",
                        "Stock registrado",
                        "Conteo",
                        "Diferencia",
                        "Unidad",
                        "Usuario",
                    ],
                    rows,
                ),
                (
                    "Resumen",
                    ["Dato", "Valor"],
                    [
                        ["Reconteos", str(data["count"])],
                        ["Subieron el stock", str(data["up"])],
                        ["Bajaron el stock", str(data["down"])],
                    ],
                ),
            ]
        )
        return download(payload, f"ajustes-{farm_id}.xlsx")
def download(payload: bytes, filename: str) -> HttpResponse:
    """Respuesta de descarga del libro armado."""
    response = HttpResponse(payload, content_type=XLSX)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
def trend_amount(month: dict, symbol: str) -> str:
    """Consumo del símbolo en el mes, o vacío si ese mes no tuvo salidas."""
    for entry in month["items"]:
        if entry["symbol"] == symbol:
            return f"{entry['base']} {symbol}"
    return ""
