"""Préstamos de un producto.

@author Cristian Deysdayr Jimenez
"""
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.inventario.api.products.serializers import ProductResponseSerializer
from apps.inventario.catalog import plain_amount
from apps.inventario.services import loans as loan_services
from apps.inventario.services.products import get_product
from apps.inventario.services.sheet import workbook


class LoanSerializer(serializers.Serializer):
    """Préstamo abierto o recién movido."""

    id = serializers.IntegerField()
    borrower_name = serializers.CharField()
    document_number = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    returned_quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    pending = serializers.SerializerMethodField()

    def get_pending(self, obj) -> str:
        return str(obj.quantity - obj.returned_quantity)


class LoanCreateSerializer(serializers.Serializer):
    """Datos de quien recibe el producto."""

    borrower_name = serializers.CharField(max_length=120)
    document_number = serializers.CharField(max_length=32)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)


class LoanReturnSerializer(serializers.Serializer):
    """Cantidad que regresa y una observación opcional."""

    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    note = serializers.CharField(max_length=160, required=False, allow_blank=True, default="")


class ProductLoanListCreateView(APIView):
    """Lista préstamos abiertos y registra uno nuevo."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int, product_id: int) -> Response:
        rows = loan_services.list_open_loans(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
        )
        return Response(LoanSerializer(rows, many=True).data)

    @extend_schema(tags=["inventario"], request=LoanCreateSerializer)
    def post(self, request: Request, farm_id: int, product_id: int) -> Response:
        serializer = LoanCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        loan, product = loan_services.lend_product(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
            **serializer.validated_data,
        )
        return Response(_payload(loan, product), status=status.HTTP_201_CREATED)


class ProductLoanReturnView(APIView):
    """Devuelve cantidad de un préstamo."""

    @extend_schema(tags=["inventario"], request=LoanReturnSerializer)
    def post(self, request: Request, farm_id: int, product_id: int, loan_id: int) -> Response:
        serializer = LoanReturnSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        loan, product = loan_services.return_loan(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
            loan_id=loan_id,
            quantity=serializer.validated_data["quantity"],
            note=serializer.validated_data.get("note", ""),
        )
        return Response(_payload(loan, product))


def _payload(loan, product) -> dict:
    """Préstamo y stock ya actualizado."""
    return {
        "loan": LoanSerializer(loan).data,
        "product": ProductResponseSerializer(product).data,
    }


class ProductLoanHistoryFileView(APIView):
    """Excel con todos los préstamos del producto y su estado."""

    @extend_schema(tags=["inventario"])
    def get(self, request: Request, farm_id: int, product_id: int) -> HttpResponse:
        product = get_product(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
            allow_inactive=True,
        )
        rows = loan_services.list_loans(
            user=request.user,
            farm_id=farm_id,
            product_id=product_id,
        )
        table = [
            [
                product.name,
                loan.created_at.strftime("%d/%m/%Y"),
                loan.created_at.strftime("%H:%M"),
                loan.borrower_name,
                loan.document_number,
                plain_amount(loan.quantity),
                product.unit.label,
                plain_amount(loan.returned_quantity),
                plain_amount(loan.quantity - loan.returned_quantity),
                _status(loan),
            ]
            for loan in rows
        ]
        payload = workbook(
            [
                "Producto",
                "Fecha",
                "Hora",
                "Nombre completo",
                "Documento",
                "Cantidad prestada",
                "Unidad",
                "Devuelto",
                "Pendiente",
                "Estado",
            ],
            table,
        )
        response = HttpResponse(
            payload,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="prestamos-{product.id}.xlsx"'
        return response


def _status(loan) -> str:
    """Estado legible del préstamo."""
    if loan.returned_quantity >= loan.quantity:
        return "Devuelto"
    return "Pendiente"
