"""Paginación de listas de inventario.

Propósito: devolver páginas de hasta 25 filas con el mismo sobre.
Contexto: productos, movimientos, préstamos e historial de finca.
@author Cristian Deysdayr Jimenez
"""

from django.db.models import QuerySet
from rest_framework.request import Request

from apps.inventario.api.movements.query import positive_int

PAGE_SIZE = 25


def read_page(request: Request) -> tuple[int, int]:
    """Lee página y tamaño, con tope de 25."""
    page = positive_int(request.query_params.get("page"), "página", default=1)
    page_size = positive_int(
        request.query_params.get("page_size"),
        "tamaño de página",
        default=PAGE_SIZE,
        limit=PAGE_SIZE,
    )
    return page, page_size


def paginate(queryset: QuerySet, request: Request) -> tuple[int, int, int, QuerySet]:
    """Corta el queryset en una página.

    @returns page, page_size, count, página.
    """
    page, page_size = read_page(request)
    total = queryset.count()
    start = (page - 1) * page_size
    return page, page_size, total, queryset[start : start + page_size]


def page_body(results, *, count: int, page: int, page_size: int) -> dict:
    """Sobre JSON de una lista paginada."""
    return {
        "count": count,
        "page": page,
        "page_size": page_size,
        "results": results,
    }
