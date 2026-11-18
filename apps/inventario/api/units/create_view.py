"""POST crea unidad/presentación custom.

@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.inventario.api.products.serializers import ErrorSerializer
from apps.inventario.api.units.serializers import (
    CustomUnitCreateSerializer,
    UnitResponseSerializer,
)
from apps.inventario.services import units as unit_services


class CustomUnitCreateView(APIView):
    """Alta de presentación comercial por el usuario."""

    @extend_schema(
        tags=["inventario"],
        request=CustomUnitCreateSerializer,
        responses={
            201: UnitResponseSerializer,
            400: OpenApiResponse(response=ErrorSerializer),
            403: OpenApiResponse(response=ErrorSerializer),
            404: OpenApiResponse(response=ErrorSerializer),
        },
    )
    def post(self, request: Request, farm_id: int) -> Response:
        serializer = CustomUnitCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        unit = unit_services.create_custom_unit(
            user=request.user,
            farm_id=farm_id,
            **serializer.validated_data,
        )
        return Response(
            UnitResponseSerializer(unit).data,
            status=status.HTTP_201_CREATED,
        )
