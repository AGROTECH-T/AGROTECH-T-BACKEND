"""PATCH y DELETE de una categoría del catálogo.

@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.inventario.catalog import catalog_payload
from apps.inventario.services.categories import delete_category, rename_category


class CategoryRenameSerializer(serializers.Serializer):
    """Nuevo nombre y, si vienen, las unidades que quedan en la categoría."""

    name = serializers.CharField(max_length=80)
    unit_codes = serializers.ListField(
        child=serializers.CharField(max_length=40),
        required=False,
    )


class CategoryDetailView(APIView):
    """Cambio de nombre y borrado de una categoría."""

    @extend_schema(tags=["inventario"], request=CategoryRenameSerializer)
    def patch(self, request: Request, farm_id: int, code: str) -> Response:
        serializer = CategoryRenameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rename_category(user=request.user, farm_id=farm_id, code=code, **serializer.validated_data)
        return Response(catalog_payload())

    @extend_schema(tags=["inventario"])
    def delete(self, request: Request, farm_id: int, code: str) -> Response:
        delete_category(user=request.user, farm_id=farm_id, code=code)
        return Response(status=status.HTTP_204_NO_CONTENT)
