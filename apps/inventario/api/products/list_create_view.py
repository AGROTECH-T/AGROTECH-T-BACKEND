"""GET lista y POST crea productos.

@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.inventario.api.products.serializers import (
    ErrorSerializer,
    ProductCreateSerializer,
    ProductResponseSerializer,
)
from apps.inventario.services import products as product_services


class ProductListCreateView(APIView):
    """Productos de una finca: GetAll + Create."""

    @extend_schema(
        tags=["inventario"],
        responses={200: ProductResponseSerializer(many=True)},
    )
    def get(self, request: Request, farm_id: int) -> Response:
        products = product_services.list_products(
            user=request.user,
            farm_id=farm_id,
            include_inactive=request.query_params.get("include_inactive") == "1",
            category_code=request.query_params.get("category") or None,
            low_stock_only=request.query_params.get("low_stock") == "1",
            search=request.query_params.get("q") or None,
            status=request.query_params.get("status") or None,
            order=request.query_params.get("order") or "name",
        )
        return Response(ProductResponseSerializer(products, many=True).data)

    @extend_schema(
        tags=["inventario"],
        request=ProductCreateSerializer,
        responses={
            201: ProductResponseSerializer,
            400: OpenApiResponse(response=ErrorSerializer),
            404: OpenApiResponse(response=ErrorSerializer),
            409: OpenApiResponse(response=ErrorSerializer),
        },
    )
    def post(self, request: Request, farm_id: int) -> Response:
        serializer = ProductCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = product_services.create_product(
            user=request.user,
            farm_id=farm_id,
            **serializer.validated_data,
        )
        return Response(
            ProductResponseSerializer(product).data,
            status=status.HTTP_201_CREATED,
        )
