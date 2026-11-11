"""Excel con todo el historial de un producto.

@author Cristian Deysdayr Jimenez
"""
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.inventario.services import movements as movement_services
from apps.inventario.services.history import amount_text, kind_label, loan_columns
from apps.inventario.services.membership import require_membership
from apps.inventario.services.permissions import READ, require_action
from apps.inventario.services.products import get_product
from apps.inventario.services.sheet import workbook


class ProductHistoryFileView(APIView):
    """Descarga todos los movimientos, no solo los que se ven."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int, product_id: int) -> HttpResponse:
        membership = require_membership(user=request.user, farm_id=farm_id)
        require_action(membership, READ)
        product = get_product(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
            allow_inactive=True,
        )
        rows = movement_services.list_movements(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
        )
        table = [
            [
                product.name,
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
        response["Content-Disposition"] = f'attachment; filename="historial-{product.id}.xlsx"'
        return response
