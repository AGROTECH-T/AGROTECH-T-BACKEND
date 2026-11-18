"""Excel con el historial de movimientos de la finca.

Propósito: descargar lo mismo que se ve en pantalla, con los filtros aplicados.
@author Cristian Deysdayr Jimenez
"""
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.inventario.api.movements.query import optional_date
from apps.inventario.services import movements as movement_services
from apps.inventario.services.history import amount_text, kind_label, loan_columns
from apps.inventario.services.sheet import workbook


class FarmMovementHistoryFileView(APIView):
    """Descarga el historial filtrado por tipo y fechas."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int) -> HttpResponse:
        rows = movement_services.list_farm_movements(
            user=request.user,
            farm_id=farm_id,
            kind=request.query_params.get("kind") or None,
            date_from=optional_date(request.query_params.get("from"), "la fecha inicial"),
            date_to=optional_date(request.query_params.get("to"), "la fecha final"),
        )
        table = [
            [
                move.product.name,
                move.created_at.strftime("%d/%m/%Y"),
                move.created_at.strftime("%H:%M"),
                kind_label(move.kind),
                amount_text(move),
                move.unit.label,
                *loan_columns(move.note),
            ]
            for move in rows
        ]
        payload = workbook(
            [
                "Producto",
                "Fecha",
                "Hora",
                "Tipo",
                "Cantidad",
                "Unidad",
                "Estado",
                "Nombre completo",
                "Documento",
                "Observación",
            ],
            table,
        )
        response = HttpResponse(
            payload,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="historial-finca-{farm_id}.xlsx"'
        return response
