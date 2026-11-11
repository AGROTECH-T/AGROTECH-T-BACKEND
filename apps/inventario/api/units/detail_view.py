"""PATCH y DELETE de una unidad del catálogo.

@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.inventario.catalog import catalog_payload
from apps.inventario.services.units import delete_unit, update_unit


class UnitUpdateSerializer(serializers.Serializer):
    """Nombre visible y categorías a las que pertenece."""

    label = serializers.CharField(max_length=80)
    categories = serializers.ListField(child=serializers.CharField(max_length=40))

    def validate(self, attrs):
        attrs["category_codes"] = attrs.pop("categories")
        return attrs


class UnitDetailView(APIView):
    """Cambio y borrado de una unidad."""

    @extend_schema(tags=["inventario"], request=UnitUpdateSerializer)
    def patch(self, request: Request, farm_id: int, code: str) -> Response:
        serializer = UnitUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        update_unit(user=request.user, farm_id=farm_id, code=code, **serializer.validated_data)
        return Response(catalog_payload())

    @extend_schema(tags=["inventario"])
    def delete(self, request: Request, farm_id: int, code: str) -> Response:
        delete_unit(user=request.user, farm_id=farm_id, code=code)
        return Response(status=status.HTTP_204_NO_CONTENT)
