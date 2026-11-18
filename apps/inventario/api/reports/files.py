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


class ConsumptionReportFileView(APIView):
    """Excel del consumo del rango y su tendencia."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int) -> HttpResponse:
        data = report_services.consumption_report(
            user=request.user,
            farm_id=farm_id,
            date_from=optional_date(request.query_params.get("from"), "la fecha inicial"),
            date_to=optional_date(request.query_params.get("to"), "la fecha final"),
            category_code=request.query_params.get("category") or None,
            product_id=read_product(request.query_params.get("product")),
        )
        rows = [
            [
                row["product"],
                row["category"],
                row["unit"],
                row["quantity"],
                f"{row['base']} {row['base_symbol']}",
                str(row["moves"]),
                f"{row['share']}%",
                "" if row["previous"] is None else row["previous"],
                "" if row["variation"] is None else f"{row['variation']}%",
            ]
            for row in data["rows"]
        ]
        symbols = sorted(
            {
                entry["symbol"]
                for month in data["trend"]
                for entry in month["items"]
            }
        )
        months = [
            [
                f"{month['label']} (mes en curso)" if month["current"] else month["label"],
                *[trend_amount(month, symbol) for symbol in symbols],
            ]
            for month in data["trend"]
        ]
        payload = book(
            [
                (
                    "Consumo",
                    [
                        "Producto",
                        "Categoría",
                        "Unidad",
                        "Consumido",
                        "Equivalencia",
                        "Movimientos",
                        "Parte del total",
                        "Periodo anterior",
                        "Variación",
                    ],
                    rows,
                ),
                (
                    "Meses",
                    ["Mes", *[f"Consumo ({symbol})" for symbol in symbols]],
                    months,
                ),
            ]
        )
        return download(payload, f"consumo-{farm_id}.xlsx")


class LoanReportFileView(APIView):
    """Excel de los préstamos de la finca."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int) -> HttpResponse:
        data = report_services.loan_report(
            user=request.user,
            farm_id=farm_id,
            status=request.query_params.get("status") or "all",
            date_from=optional_date(request.query_params.get("from"), "la fecha inicial"),
            date_to=optional_date(request.query_params.get("to"), "la fecha final"),
            person=request.query_params.get("person") or None,
            product_id=read_product(request.query_params.get("product")),
        )
        rows = [
            [
                row["person"],
                row["document"],
                row["product"],
                row["quantity"],
                row["returned"],
                row["pending"],
                row["unit"],
                row["state"],
                row["when"].strftime("%d/%m/%Y"),
            ]
            for row in data["rows"]
        ]
        people = [
            [item["person"], item["document"], item["pending"]] for item in data["people"]
        ]
        payload = book(
            [
                (
                    "Préstamos",
                    [
                        "Persona",
                        "Documento",
                        "Producto",
                        "Prestado",
                        "Devuelto",
                        "Pendiente",
                        "Unidad",
                        "Estado",
                        "Fecha",
                    ],
                    rows,
                ),
                ("Pendientes", ["Persona", "Documento", "Pendiente"], people),
            ]
        )
        return download(payload, f"prestamos-{farm_id}.xlsx")


class OrderReportFileView(APIView):
    """Excel de lo que hay que pedir."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int) -> HttpResponse:
        data = report_services.order_report(
            user=request.user,
            farm_id=farm_id,
            date_from=optional_date(request.query_params.get("from"), "la fecha inicial"),
            date_to=optional_date(request.query_params.get("to"), "la fecha final"),
            category_code=request.query_params.get("category") or None,
            only_low=flag(request.query_params.get("low")),
            target=request.query_params.get("target") or "min",
        )
        rows = [
            [
                row["product"],
                row["category"],
                "" if row["initial"] is None else row["initial"],
                row["entries"],
                row["exits"],
                row["adjustments"],
                row["current"],
                row["min"],
                row["missing"],
                row["suggested"],
                row["lent"],
                row["unit"],
                str(row["moves"]),
            ]
            for row in data["rows"]
        ]
        payload = book(
            [
                (
                    "Pedido",
                    [
                        "Producto",
                        "Categoría",
                        "Inicial",
                        "Entradas",
                        "Salidas",
                        "Ajustes",
                        "Actual",
                        "Mínimo",
                        "Falta",
                        "Pedir",
                        "Prestado",
                        "Unidad",
                        "Movimientos",
                    ],
                    rows,
                ),
                (
                    "Resumen",
                    ["Dato", "Valor"],
                    [
                        ["Productos", str(data["count"])],
                        ["En o bajo el mínimo", str(data["low"])],
                        ["Con algo por pedir", str(data["to_order"])],
                    ],
                ),
            ]
        )
        return download(payload, f"pedido-{farm_id}.xlsx")


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
