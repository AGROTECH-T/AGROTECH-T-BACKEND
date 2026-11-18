"""Foto del producto.

@author Cristian Deysdayr Jimenez
"""
from django.core.files.base import ContentFile
from django.http import FileResponse
from drf_spectacular.utils import extend_schema
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.errors import AppError
from apps.inventario.api.products.serializers import ProductResponseSerializer
from apps.inventario.services.membership import require_membership
from apps.inventario.services.permissions import READ, WRITE_STOCK, require_action
from apps.inventario.services.products import get_product

MAX_BYTES = 2 * 1024 * 1024


class ProductImageView(APIView):
    """Sube y muestra la foto ya comprimida."""

    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int, product_id: int) -> FileResponse:
        membership = require_membership(user=request.user, farm_id=farm_id)
        require_action(membership, READ)
        product = get_product(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
            allow_inactive=True,
        )
        if not product.image:
            raise AppError("este producto no tiene foto", 404)
        return FileResponse(product.image.open("rb"), content_type="image/jpeg")

    @extend_schema(tags=["inventario"])
    def post(self, request: Request, farm_id: int, product_id: int) -> Response:
        membership = require_membership(user=request.user, farm_id=farm_id)
        require_action(membership, WRITE_STOCK)
        upload = request.FILES.get("image")
        if upload is None:
            raise AppError("elige una foto", 400)
        if upload.size > MAX_BYTES:
            raise AppError("la foto debe pesar 2 MB o menos", 400)
        if not (upload.content_type or "").startswith("image/"):
            raise AppError("el archivo tiene que ser una imagen", 400)
        product = get_product(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
            allow_inactive=True,
        )
        if product.image:
            product.image.delete(save=False)
        product.image.save(f"{product.id}.jpg", ContentFile(upload.read()), save=True)
        return Response(ProductResponseSerializer(product).data)

    @extend_schema(tags=["inventario"])
    def delete(self, request: Request, farm_id: int, product_id: int) -> Response:
        membership = require_membership(user=request.user, farm_id=farm_id)
        require_action(membership, WRITE_STOCK)
        product = get_product(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
            allow_inactive=True,
        )
        if product.image:
            product.image.delete(save=True)
        return Response(ProductResponseSerializer(product).data)
