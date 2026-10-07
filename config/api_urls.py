"""Rutas de plataforma y de la API versionada.

Propósito: agrupar salud, OpenAPI y los recursos bajo /api/v1/.
Contexto: cada dominio expone su propio urls.py e ingresa aquí.
@author Cristian Deysdayr Jimenez
"""
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from config.health import health

urlpatterns = [
    path("health/", health, name="health"),
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api-schema"), name="api-docs"),
    path("v1/", include("apps.accounts.api.urls")),
]
