"""Vistas del dominio de fincas.

Propósito: listar, crear, consultar, actualizar y eliminar fincas.
Contexto: la autenticación y el permiso global ya exigen una cuenta activa.
"""
from django.http import Http404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.farms import services
from apps.farms.serializers import (
    FarmCreateSerializer,
    FarmResponseSerializer,
    FarmUpdateSerializer,
)


class FarmErrorResponseSerializer(serializers.Serializer):
    """Documenta el contrato uniforme de error de Farms."""

    error = serializers.CharField()


class FarmListCreateView(APIView):
    """Lista las fincas propias y permite crear una finca."""

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_list",
        responses={
            200: OpenApiResponse(
                response=FarmResponseSerializer(many=True),
                description="Listado de fincas del usuario autenticado.",
            ),
            401: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description="Autenticación ausente o inválida.",
            ),
        },
    )
    def get(self, request: Request) -> Response:
        """Retorna únicamente las fincas donde el usuario tiene membresía."""
        memberships = services.list_memberships(request.user)
        return Response(FarmResponseSerializer(memberships, many=True).data)

    @extend_schema(
        tags=["farms"],
        request=FarmCreateSerializer,
        responses={
            201: OpenApiResponse(
                response=FarmResponseSerializer,
                description=(
                    "Finca creada y membresía de administrador inicial creada."
                ),
            ),
            400: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description="Datos inválidos o campos adicionales no permitidos.",
            ),
            401: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description="Autenticación ausente o inválida.",
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Crea una finca y asigna al creador el rol administrador."""
        serializer = FarmCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.create_farm(
            user=request.user,
            name=serializer.validated_data["name"],
        )
        return Response(
            FarmResponseSerializer(membership).data,
            status=status.HTTP_201_CREATED,
        )


class FarmDetailView(APIView):
    """Consulta, actualiza o elimina una finca según membresía y rol."""

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_retrieve",
        responses={
            200: OpenApiResponse(
                response=FarmResponseSerializer,
                description="Detalle de una finca a la que pertenece el usuario.",
            ),
            401: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description="Autenticación ausente o inválida.",
            ),
            404: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description="La finca no existe o no pertenece al usuario.",
            ),
        },
    )
    def get(self, request: Request, farm_id: int) -> Response:
        """Retorna la finca y el rol del usuario, o 404 si no es miembro."""
        membership = services.get_membership(user=request.user, farm_id=farm_id)
        if membership is None:
            raise Http404
        return Response(FarmResponseSerializer(membership).data)

    @extend_schema(
        tags=["farms"],
        request=FarmUpdateSerializer,
        responses={
            200: OpenApiResponse(response=FarmResponseSerializer),
            400: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def put(self, request: Request, farm_id: int) -> Response:
        """Actualiza el nombre; solo el administrador de la finca."""
        serializer = FarmUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.update_farm(
            user=request.user,
            farm_id=farm_id,
            name=serializer.validated_data["name"],
        )
        return Response(FarmResponseSerializer(membership).data)

    @extend_schema(
        tags=["farms"],
        responses={
            204: OpenApiResponse(description="Finca eliminada."),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def delete(self, request: Request, farm_id: int) -> Response:
        """Elimina la finca; solo el administrador."""
        services.delete_farm(user=request.user, farm_id=farm_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
