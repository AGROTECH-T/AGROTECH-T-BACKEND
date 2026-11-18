"""Rutas del submódulo de Configuración.

Propósito: publicar el catálogo de roles y la asignación de permisos bajo /api/v1/.
@author Cristian Deysdayr Jimenez
"""

from django.urls import path

from apps.farms.api.views import (
    AsignacionRolesView,
    CambiarRolView,
    DetalleUsuarioView,
    EstadoUsuarioView,
    PermisoDirectoView,
    RolesPermisosView,
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
    path(
        "configuracion/usuarios/<int:user_id>/rol/",
        CambiarRolView.as_view(),
        name="configuracion-cambiar-rol",
    ),
    path(
        "configuracion/usuarios/<int:user_id>/permisos/",
        PermisoDirectoView.as_view(),
        name="configuracion-permisos-directos",
    ),
    path(
        "configuracion/usuarios/<int:user_id>/estado/",
        EstadoUsuarioView.as_view(),
        name="configuracion-estado-usuario",
    ),
]
