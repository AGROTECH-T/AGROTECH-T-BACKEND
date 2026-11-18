"""POST crea una medida base.

@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.inventario.catalog import catalog_payload
from apps.inventario.services.units import create_base_unit, delete_base_unit, update_base_unit


class BaseUnitCreateSerializer(serializers.Serializer):
    """Nombre, símbolo y si admite decimales."""

    name = serializers.CharField(max_length=60)
    symbol = serializers.CharField(max_length=10)
    decimals = serializers.BooleanField()


class BaseUnitCreateView(APIView):
    """Alta de una medida que no viene en el sistema."""

    @extend_schema(tags=["inventario"], request=BaseUnitCreateSerializer)
    def post(self, request: Request, farm_id: int) -> Response:
        serializer = BaseUnitCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        base = create_base_unit(
            user=request.user,
            farm_id=farm_id,
            **serializer.validated_data,
        )
        payload = catalog_payload()
        payload["created"] = base.code
        return Response(payload, status=status.HTTP_201_CREATED)


class BaseUnitDetailView(APIView):
    """Cambio y borrado de una medida."""

    @extend_schema(tags=["inventario"], request=BaseUnitCreateSerializer)
    def patch(self, request: Request, farm_id: int, code: str) -> Response:
        serializer = BaseUnitCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        update_base_unit(user=request.user, farm_id=farm_id, code=code, **serializer.validated_data)
        return Response(catalog_payload())

    @extend_schema(tags=["inventario"])
    def delete(self, request: Request, farm_id: int, code: str) -> Response:
        delete_base_unit(user=request.user, farm_id=farm_id, code=code)
        return Response(status=status.HTTP_204_NO_CONTENT)
