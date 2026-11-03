"""GET catálogo de unidades y categorías.

Propósito: alimentar los selects del cliente.
@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.inventario.catalog import catalog_payload


class InventoryCatalogView(APIView):
    """Devuelve el catálogo fijo del sistema."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request) -> Response:
        return Response(catalog_payload())
