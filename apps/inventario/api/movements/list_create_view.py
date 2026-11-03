"""GET lista y POST registra movimientos.

@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.inventario.api.movements.serializers import (
    MovementCreateSerializer,
    MovementResponseSerializer,
)
from apps.inventario.services import movements as movement_services


class MovementListCreateView(APIView):
    """Movimientos de un producto: GetAll + Create."""

    @extend_schema(
        tags=["inventario"],
        responses={200: MovementResponseSerializer(many=True)},
    )
    def get(self, request: Request, farm_id: int, product_id: int) -> Response:
        movements = movement_services.list_movements(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
        )
        return Response(MovementResponseSerializer(movements, many=True).data)

    @extend_schema(
        tags=["inventario"],
        request=MovementCreateSerializer,
        responses={201: MovementResponseSerializer},
    )
    def post(self, request: Request, farm_id: int, product_id: int) -> Response:
        serializer = MovementCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        movement = movement_services.register_movement(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
            **serializer.validated_data,
        )
        return Response(
            MovementResponseSerializer(movement).data,
            status=status.HTTP_201_CREATED,
        )
