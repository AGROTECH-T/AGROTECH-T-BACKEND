"""Rutas del submodulo de Configuracion.

Proposito: publicar el catalogo de roles y la asignacion de permisos bajo /api/v1/.
@author Cristian Deysdayr Jimenez
"""

from django.urls import path

from apps.farms.api.views import (
    RolesPermisosView,
    AsignacionRolesView,
    DetalleUsuarioView,
)

urlpatterns = [
    path(
        "configuracion/roles-permisos/",
        RolesPermisosView.as_view(),
        name="roles-permisos",
    ),
    path(
        "configuracion/usuarios/",
        AsignacionRolesView.as_view(),
        name="configuracion-usuarios",
    ),
    path(
        "configuracion/usuarios/<int:user_id>/",
        DetalleUsuarioView.as_view(),
        name="configuracion-usuario-detalle",
    ),
]
