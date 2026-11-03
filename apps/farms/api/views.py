"""Expone el catálogo de roles y permisos del submódulo de Configuración.

Propósito: entregar la matriz funcional acordada para el módulo Farms.
Contexto: requiere sesión válida; todavía no valida membresías ni fincas.
"""

from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.farms import roles
from apps.farms.api.serializers import RolesPermisosSerializer


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
