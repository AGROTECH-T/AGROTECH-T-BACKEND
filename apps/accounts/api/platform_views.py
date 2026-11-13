"""HTTP para que el superadmin nombre administradores.

Propósito: listar pendientes, crear administradores y promover cuentas.
Contexto: sin este permiso la cuenta solo ve Inicio.
@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.serializers import (
    AdministradorFichaSerializer,
    CrearAdministradorSerializer,
    ListaAdministradoresSerializer,
)
from apps.accounts.services import platform_admin


class AdministradoresView(APIView):
    """Lista pendientes y crea administradores de plataforma."""

    @extend_schema(responses={200: ListaAdministradoresSerializer}, tags=["Configuración"])
    def get(self, request: Request) -> Response:
        """Devuelve cuentas en espera y administradores ya nombrados."""
        return Response(platform_admin.listar_administradores(request.user))

    @extend_schema(
        request=CrearAdministradorSerializer,
        responses={201: AdministradorFichaSerializer},
        tags=["Configuración"],
    )
    def post(self, request: Request) -> Response:
        """Crea una cuenta nueva con todos los permisos de administrador."""
        payload = CrearAdministradorSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        persona = platform_admin.crear_administrador(
            actor=request.user,
            datos=payload.validated_data,
        )
        return Response(persona, status=201)


class PromoverAdministradorView(APIView):
    """Nombra administradora a una cuenta ya registrada."""

    @extend_schema(responses={200: AdministradorFichaSerializer}, tags=["Configuración"])
    def post(self, request: Request, user_id: int) -> Response:
        """Activa el permiso de administrador en esa cuenta."""
        persona = platform_admin.promover_administrador(
            actor=request.user,
            user_id=user_id,
        )
        return Response(persona)
