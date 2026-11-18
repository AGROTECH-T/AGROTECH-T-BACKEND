"""Paginación acotada para listados IoT."""
from rest_framework.pagination import PageNumberPagination


class IoTPagination(PageNumberPagination):
    """Paginación estándar con tamaño predeterminado 20 y máximo 100."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
