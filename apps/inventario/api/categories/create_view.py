"""POST crea una categoría del catálogo.

@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.inventario.catalog import catalog_payload
from apps.inventario.services.categories import create_user_category


class CategoryCreateSerializer(serializers.Serializer):
    """Nombre y, si se quiere, unidades nuevas o ya usadas en otra categoría."""

    name = serializers.CharField(max_length=80)
    copy_from = serializers.CharField(max_length=40, required=False, allow_blank=True, default="")
    unit_codes = serializers.ListField(
        child=serializers.CharField(max_length=40),
        required=False,
        default=list,
    )
    unit_label = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    base_unit = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    factor = serializers.DecimalField(max_digits=14, decimal_places=6, required=False, default=1)

    def validate(self, attrs):
        attrs["base_unit_code"] = attrs.pop("base_unit", "")
        return attrs


class CategoryCreateView(APIView):
    """Alta de categoría para la finca."""

    @extend_schema(tags=["inventario"], request=CategoryCreateSerializer)
    def post(self, request: Request, farm_id: int) -> Response:
        serializer = CategoryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        category = create_user_category(
            user=request.user,
            farm_id=farm_id,
            **serializer.validated_data,
        )
        payload = catalog_payload()
        payload["created"] = category.code
        return Response(payload, status=status.HTTP_201_CREATED)
