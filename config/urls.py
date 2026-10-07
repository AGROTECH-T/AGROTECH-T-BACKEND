"""Rutas raíz de AGROTECH-T-BACKEND.

Propósito: montar el admin, la API y los alias heredados de autenticación.
Contexto: las rutas oficiales viven en config.api_urls.
@author Cristian Deysdayr Jimenez
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("config.api_urls")),
    path("", include("apps.accounts.api.legacy_urls")),
]
