"""Rutas del módulo inventario.

Prefijo: /api/v1/inventory/
@author Cristian Deysdayr Jimenez
"""
from django.urls import path

from apps.inventario.api.catalog_view import InventoryCatalogView
from apps.inventario.api.categories.create_view import CategoryCreateView
from apps.inventario.api.categories.detail_view import CategoryDetailView
from apps.inventario.api.loans.views import (
    ProductLoanHistoryFileView,
    ProductLoanListCreateView,
    ProductLoanReturnView,
)
from apps.inventario.api.movements.history_view import FarmMovementHistoryFileView
from apps.inventario.api.movements.list_create_view import (
    FarmMovementListView,
    MovementListCreateView,
)
from apps.inventario.api.products.detail_view import ProductDetailView
from apps.inventario.api.products.history_view import ProductHistoryFileView
from apps.inventario.api.products.image_view import ProductImageView
from apps.inventario.api.products.inventory_view import ProductInventoryFileView
from apps.inventario.api.products.list_create_view import ProductListCreateView
from apps.inventario.api.reports.files import (
    AdjustmentReportFileView,
    ConsumptionReportFileView,
    LoanReportFileView,
    OrderReportFileView,
)
from apps.inventario.api.reports.views import (
    AdjustmentReportView,
    ConsumptionReportView,
    LoanReportView,
    OrderReportView,
)
from apps.inventario.api.units.base_view import BaseUnitCreateView, BaseUnitDetailView
from apps.inventario.api.units.create_view import CustomUnitCreateView
from apps.inventario.api.units.detail_view import UnitDetailView

app_name = "inventario"

urlpatterns = [
    path("inventory/catalog/", InventoryCatalogView.as_view(), name="catalog"),
    path(
        "inventory/<int:farm_id>/categories/",
        CategoryCreateView.as_view(),
        name="category-create",
    ),
    path(
        "inventory/<int:farm_id>/categories/<str:code>/",
        CategoryDetailView.as_view(),
        name="category-detail",
    ),
    path(
        "inventory/<int:farm_id>/base-units/",
        BaseUnitCreateView.as_view(),
        name="base-unit-create",
    ),
    path(
        "inventory/<int:farm_id>/base-units/<str:code>/",
        BaseUnitDetailView.as_view(),
        name="base-unit-detail",
    ),
    path(
        "inventory/<int:farm_id>/units/",
        CustomUnitCreateView.as_view(),
        name="unit-create",
    ),
    path(
        "inventory/<int:farm_id>/units/<str:code>/",
        UnitDetailView.as_view(),
        name="unit-detail",
    ),
    path(
        "inventory/<int:farm_id>/products/",
        ProductListCreateView.as_view(),
        name="product-list-create",
    ),
    path(
        "inventory/<int:farm_id>/products/inventory.xlsx",
        ProductInventoryFileView.as_view(),
        name="product-inventory-file",
    ),
    path(
        "inventory/<int:farm_id>/products/<int:product_id>/",
        ProductDetailView.as_view(),
        name="product-detail",
    ),
    path(
        "inventory/<int:farm_id>/products/<int:product_id>/movements/",
        MovementListCreateView.as_view(),
        name="movement-list-create",
    ),
    path(
        "inventory/<int:farm_id>/products/<int:product_id>/image/",
        ProductImageView.as_view(),
        name="product-image",
    ),
    path(
        "inventory/<int:farm_id>/products/<int:product_id>/history.xlsx",
        ProductHistoryFileView.as_view(),
        name="product-history-file",
    ),
    path(
        "inventory/<int:farm_id>/products/<int:product_id>/loans/",
        ProductLoanListCreateView.as_view(),
        name="product-loan-list-create",
    ),
    path(
        "inventory/<int:farm_id>/products/<int:product_id>/loans/history.xlsx",
        ProductLoanHistoryFileView.as_view(),
        name="product-loan-history-file",
    ),
    path(
        "inventory/<int:farm_id>/products/<int:product_id>/loans/<int:loan_id>/return/",
        ProductLoanReturnView.as_view(),
        name="product-loan-return",
    ),
    path(
        "inventory/<int:farm_id>/movements/",
        FarmMovementListView.as_view(),
        name="farm-movement-list",
    ),
    path(
        "inventory/<int:farm_id>/movements/history.xlsx",
        FarmMovementHistoryFileView.as_view(),
        name="farm-movement-history-file",
    ),
    path(
        "inventory/<int:farm_id>/reports/adjustments/",
        AdjustmentReportView.as_view(),
        name="report-adjustments",
    ),
    path(
        "inventory/<int:farm_id>/reports/adjustments.xlsx",
        AdjustmentReportFileView.as_view(),
        name="report-adjustments-file",
    ),
    path(
        "inventory/<int:farm_id>/reports/consumption/",
        ConsumptionReportView.as_view(),
        name="report-consumption",
    ),
    path(
        "inventory/<int:farm_id>/reports/consumption.xlsx",
        ConsumptionReportFileView.as_view(),
        name="report-consumption-file",
    ),
    path(
        "inventory/<int:farm_id>/reports/loans/",
        LoanReportView.as_view(),
        name="report-loans",
    ),
    path(
        "inventory/<int:farm_id>/reports/loans.xlsx",
        LoanReportFileView.as_view(),
        name="report-loans-file",
    ),
    path(
        "inventory/<int:farm_id>/reports/order/",
        OrderReportView.as_view(),
        name="report-order",
    ),
    path(
        "inventory/<int:farm_id>/reports/order.xlsx",
        OrderReportFileView.as_view(),
        name="report-order-file",
    ),
]
