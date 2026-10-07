"""Comprueba el código antes de la contraseña nueva.

Propósito: validar el OTP sin consumirlo.
Contexto: la clave se pide solo después de esta respuesta.
@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.api.serializers import ConfirmSerializer, SuccessResponseSerializer
from apps.accounts.api.throttles import ConfirmIdentifierThrottle
from apps.accounts.services import auth_service


class ConfirmCodeView(APIView):
    """Valida el código de recuperación."""

    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle, ConfirmIdentifierThrottle]
    throttle_scope = "confirm"

    @extend_schema(request=ConfirmSerializer, responses={200: SuccessResponseSerializer})
    def post(self, request: Request) -> Response:
        """Responde éxito si el código vigente coincide."""
        serializer = ConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_service.confirm_code(**serializer.validated_data)
        return Response({"ok": True})
