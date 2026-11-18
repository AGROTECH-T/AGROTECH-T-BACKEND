"""GET/PATCH/DELETE detalle de un producto.

@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.inventario.api.products.serializers import (
    ErrorSerializer,
    ProductResponseSerializer,
    ProductUpdateSerializer,
)
from apps.inventario.services import products as product_services


class ProductDetailView(APIView):
    """Producto por id: detalle, edición y baja."""

    @extend_schema(
        tags=["inventario"],
        responses={200: ProductResponseSerializer},
    )
    def get(self, request: Request, farm_id: int, product_id: int) -> Response:
        product = product_services.get_product(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
            allow_inactive=True,
        )
        return Response(ProductResponseSerializer(product).data)

    @extend_schema(
        tags=["inventario"],
        request=ProductUpdateSerializer,
        responses={
            200: ProductResponseSerializer,
            400: OpenApiResponse(response=ErrorSerializer),
            403: OpenApiResponse(response=ErrorSerializer),
            404: OpenApiResponse(response=ErrorSerializer),
        },
    )
    def patch(self, request: Request, farm_id: int, product_id: int) -> Response:
        serializer = ProductUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        is_active = data.pop("is_active", None)
        product = None
        if data:
            product = product_services.update_product(
                user=request.user,
                farm_id=farm_id,
                product_id=product_id,
                **data,
            )
        if is_active is not None:
            product = product_services.set_product_active(
                user=request.user,
                farm_id=farm_id,
                product_id=product_id,
                is_active=is_active,
            )
        if product is None:
            product = product_services.get_product(
                user=request.user,
                farm_id=farm_id,
                product_id=product_id,
                allow_inactive=True,
            )
        return Response(ProductResponseSerializer(product).data)

    @extend_schema(
        tags=["inventario"],
        responses={
            204: OpenApiResponse(description="Eliminado"),
            400: OpenApiResponse(response=ErrorSerializer),
            403: OpenApiResponse(response=ErrorSerializer),
            404: OpenApiResponse(response=ErrorSerializer),
        },
    )
    def delete(self, request: Request, farm_id: int, product_id: int) -> Response:
        product_services.delete_product(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
