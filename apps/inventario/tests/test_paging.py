# ruff: noqa: S101
"""Paginación de listas de inventario.

Propósito: el tamaño de página no pasa de 25.
@author Cristian Deysdayr Jimenez
"""

from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from apps.inventario.api.paging import PAGE_SIZE, read_page


def test_page_size_no_pasa_de_25():
    """Aunque pidan 99, la página queda en 25."""
    factory = APIRequestFactory()
    request = Request(factory.get("/inventario/", {"page": "2", "page_size": "99"}))
    page, size = read_page(request)
    assert page == 2
    assert size == PAGE_SIZE
    assert size == 25


def test_pagina_por_defecto():
    """Sin query, arranca en la primera página de 25."""
    request = Request(APIRequestFactory().get("/inventario/"))
    page, size = read_page(request)
    assert page == 1
    assert size == 25
