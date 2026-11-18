"""Plantilla vacía de Excel para crear productos.

Propósito: entregar solo las siete columnas; la persona las llena y luego
importa. No incluye productos actuales.
@author Cristian Deysdayr Jimenez
"""
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.inventario.services.import_products import COLUMNS
from apps.inventario.services.membership import require_membership
from apps.inventario.services.sheet import workbook


class ProductTemplateFileView(APIView):
    """Descarga la plantilla limpia de importación de la finca."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int) -> HttpResponse:
        require_membership(user=request.user, farm_id=farm_id)
        payload = workbook(list(COLUMNS), [])
        response = HttpResponse(
            payload,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="plantilla-inventario-{farm_id}.xlsx"'
        )
        return response
