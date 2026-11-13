"""Expone el catálogo de roles y la asignación de roles y permisos.

Propósito: entregar la matriz funcional y administrar personas de la finca.
Contexto: todo exige ser administrador de la finca y no permite tocarse a sí mismo.
@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.farms import asignacion, roles
from apps.farms.api.serializers import (
    AsignacionSerializer,
    CambiarRolSerializer,
    CrearUsuarioSerializer,
    EstadoUsuarioSerializer,
    PermisoDirectoSerializer,
    RolesPermisosSerializer,
    UsuarioAsignadoSerializer,
)
class RolesPermisosView(APIView):
    """Consulta los roles, la matriz de permisos y las reglas asociadas."""

    @extend_schema(
        responses={200: RolesPermisosSerializer},
        tags=["Configuración"],
        description="Catálogo de roles, actores, matriz de permisos y reglas.",
    )
    def get(self, request: Request) -> Response:
        """Retorna el catálogo funcional de roles y permisos."""
        return Response(roles.catalogo())
class AsignacionRolesView(APIView):
    """Lista y crea las personas de las fincas que administra la sesión."""
    @extend_schema(responses={200: AsignacionSerializer}, tags=["Configuración"])
    def get(self, request: Request) -> Response:
        """Devuelve personas y roles solo de las fincas administradas."""
        search = (request.query_params.get("search") or "").strip()[:60]
        return Response(asignacion.listar_personas(request.user, search=search))
class DetalleUsuarioView(APIView):
    """Muestra el rol y los permisos de una persona."""

    @extend_schema(responses={200: UsuarioAsignadoSerializer}, tags=["Configuración"])
    def get(self, request: Request, user_id: int) -> Response:
        """Devuelve la ficha con permisos del rol y permisos directos."""
        return Response(asignacion.detalle_persona(actor=request.user, user_id=user_id))
