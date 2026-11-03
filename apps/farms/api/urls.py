"""Rutas del submódulo de Configuración.

Propósito: publicar el catálogo de roles y permisos bajo /api/v1/.
"""

from django.urls import path

from apps.farms.api.views import RolesPermisosView

urlpatterns = [
    path(
        "configuracion/roles-permisos/",
        RolesPermisosView.as_view(),
        name="roles-permisos",
    ),
]
