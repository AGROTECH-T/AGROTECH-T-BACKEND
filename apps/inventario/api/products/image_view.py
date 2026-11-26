"""Foto del producto.

@author Cristian Deysdayr Jimenez
"""
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.errors import AppError
from apps.inventario.api.products.serializers import ProductResponseSerializer
from apps.inventario.services.image_storage import (
    ALLOWED_TYPES,
    ImageMissing,
    configured_store,
    product_image_id,
)
from apps.inventario.services.membership import require_membership
from apps.inventario.services.permissions import READ, WRITE_STOCK, require_action
from apps.inventario.services.products import get_product

MAX_BYTES = 2 * 1024 * 1024


class ProductImageView(APIView):
    """Sube y muestra la foto ya comprimida, guardada en Cloudflare."""

    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int, product_id: int) -> HttpResponse:
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
        legacy = _legacy_file(product.image)
        if legacy is not None:
            return HttpResponse(legacy.read_bytes(), content_type="image/jpeg")
        try:
            content, content_type = configured_store().get(product.image)
        except ImageMissing:
            product.image = ""
            product.save(update_fields=["image", "updated_at"])
            raise
        return HttpResponse(content, content_type=content_type)

    @extend_schema(tags=["inventario"])
    def post(self, request: Request, farm_id: int, product_id: int) -> Response:
        membership = require_membership(user=request.user, farm_id=farm_id)
        require_action(membership, WRITE_STOCK)
        upload = request.FILES.get("image")
        if upload is None:
            raise AppError("elige una foto", 400)
        if upload.size > MAX_BYTES:
            raise AppError("la foto debe pesar 2 MB o menos", 400)
        content_type = (upload.content_type or "").split(";")[0].strip().lower()
        if content_type not in ALLOWED_TYPES:
            raise AppError("el archivo tiene que ser una imagen", 400)
        product = get_product(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
            allow_inactive=True,
        )
        image_id = product_image_id(product.id)
        configured_store().put(image_id, upload.read(), content_type)
        product.image = image_id
        product.save(update_fields=["image", "updated_at"])
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
            legacy = _legacy_file(product.image)
            if legacy is not None:
                legacy.unlink(missing_ok=True)
            else:
                configured_store().remove(product.image)
            product.image = ""
            product.save(update_fields=["image", "updated_at"])
        return Response(ProductResponseSerializer(product).data)


def _legacy_file(value: str) -> Path | None:
    """Archivo viejo en el disco, si todavía existe. Las fotos nuevas no pasan por aquí."""
    if not value or value.startswith("product-"):
        return None
    root = Path(settings.MEDIA_ROOT).resolve()
    path = (root / value).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path
