"""Health check usado por Render para el servicio web.

Propósito: confirmar que /api/health/ responde sin autenticación ni base de datos.
"""
import json

from django.test import RequestFactory
from django.urls import resolve

from config.health import health


def test_health_route_resolves_to_public_view():
    """La ruta configurada en Render apunta a la vista de salud."""
    assert resolve("/api/health/").func is health


def test_health_returns_ok_without_database():
    """Responde 200 con JSON fijo; sin marca django_db, cualquier consulta fallaría."""
    response = health(RequestFactory().get("/api/health/"))
    assert response.status_code == 200
    assert json.loads(response.content) == {"status": "ok", "project": "agrotech-t-backend"}
