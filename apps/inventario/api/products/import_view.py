"""Importación de inventario desde la plantilla de Excel.

@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.errors import AppError
from apps.inventario.services.import_products import import_inventory
from apps.inventario.services.xlsx_reader import MAX_BYTES


class ProductImportView(APIView):
    """Crea o actualiza productos con el archivo de la plantilla."""

    @extend_schema(tags=["inventario"])
    def post(self, request: Request, farm_id: int) -> Response:
        upload = request.FILES.get("file")
        if upload is None:
            raise AppError("adjunta la plantilla en Excel", 400)
        if not upload.name.lower().endswith(".xlsx"):
            raise AppError("el archivo debe ser .xlsx", 400)
        if upload.size > MAX_BYTES:
            raise AppError("el archivo supera los 2 MB", 400)
        report = import_inventory(
            user=request.user,
            farm_id=farm_id,
            payload=upload.read(),
            filename=upload.name,
        )
        return Response(report, status=status.HTTP_200_OK)
