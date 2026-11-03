"""Rutas del módulo inventario.

Prefijo: /api/v1/inventory/
@author Cristian Deysdayr Jimenez
"""
from django.urls import path

from apps.inventario.api.catalog_view import InventoryCatalogView
from apps.inventario.api.movements.list_create_view import MovementListCreateView
from apps.inventario.api.products.detail_view import ProductDetailView
from apps.inventario.api.products.list_create_view import ProductListCreateView

app_name = "inventario"

urlpatterns = [
    path("inventory/catalog/", InventoryCatalogView.as_view(), name="catalog"),
    path(
        "inventory/<int:farm_id>/products/",
        ProductListCreateView.as_view(),
        name="product-list-create",
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
]
