"""GET detalle de un producto.

@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.inventario.api.products.serializers import ProductResponseSerializer
from apps.inventario.services import products as product_services


class ProductDetailView(APIView):
    """Producto por id: GetByID."""

    @extend_schema(
        tags=["inventario"],
        responses={200: ProductResponseSerializer},
    )
    def get(self, request: Request, farm_id: int, product_id: int) -> Response:
        product = product_services.get_product(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
        )
        return Response(ProductResponseSerializer(product).data)
