"""Gestiona las solicitudes HTTP del dominio de cuentas.

@author Cristian Deysdayr Jimenez
"""

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.api.serializers import (
    AccountResponseSerializer,
    LoginSerializer,
    RecoverSerializer,
    RecoveryResponseSerializer,
    RegisterSerializer,
    ResetSerializer,
    SuccessResponseSerializer,
    TokenResponseSerializer,
)
from apps.accounts.api.throttles import (
    LoginIdentifierThrottle,
    RecoveryIdentifierThrottle,
    ResetIdentifierThrottle,
)
from apps.accounts.services import auth_service


class RegisterView(APIView):
    """Crea cuentas nuevas."""

    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    @extend_schema(request=RegisterSerializer, responses={201: SuccessResponseSerializer})
    def post(self, request: Request) -> Response:
        """Registra una cuenta validada."""
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_service.register(serializer.validated_data.copy())
        return Response({"ok": True}, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    """Autentica cuentas."""

    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle, LoginIdentifierThrottle]
    throttle_scope = "login"

    @extend_schema(request=LoginSerializer, responses={200: TokenResponseSerializer})
    def post(self, request: Request) -> Response:
        """Entrega un JWT cifrado."""
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return _token_response(auth_service.login(**serializer.validated_data))


class RecoverView(APIView):
    """Inicia la recuperación de contraseña."""

    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle, RecoveryIdentifierThrottle]
    throttle_scope = "recovery"

    @extend_schema(request=RecoverSerializer, responses={200: RecoveryResponseSerializer})
    def post(self, request: Request) -> Response:
        """Envía un OTP por el canal solicitado."""
        serializer = RecoverSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wait = auth_service.recover(**serializer.validated_data)
        response = Response({"ok": True, "wait": wait})
        response["Cache-Control"] = "no-store"
        return response


class ResetView(APIView):
    """Finaliza la recuperación de contraseña."""

    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle, ResetIdentifierThrottle]
    throttle_scope = "reset"

    @extend_schema(request=ResetSerializer, responses={200: SuccessResponseSerializer})
    def post(self, request: Request) -> Response:
        """Cambia la contraseña después de verificar el OTP."""
        serializer = ResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_service.reset_password(**serializer.validated_data)
        return Response({"ok": True})


class RenewView(APIView):
    """Renueva una sesión activa."""

    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "renew"

    @extend_schema(request=None, responses={200: TokenResponseSerializer})
    def post(self, request: Request) -> Response:
        """Rota el token Bearer presentado."""
        token = auth_service.renew(request.headers.get("Authorization", ""))
        return _token_response(token)


class MeView(APIView):
    """Expone la cuenta autenticada sin incluir secretos."""

    @extend_schema(responses={200: AccountResponseSerializer})
    def get(self, request: Request) -> Response:
        """Retorna los datos visibles del usuario actual."""
        user = request.user
        return Response(
            {
                "id": user.pk,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "identification": user.identification,
                "phone": user.phone,
                "correo": user.correo,
            }
        )


class LogoutView(APIView):
    """Cierra la sesión autenticada."""

    @extend_schema(request=None, responses={200: SuccessResponseSerializer})
    def post(self, request: Request) -> Response:
        """Revoca inmediatamente el token vigente."""
        auth_service.logout(request.user)
        return Response({"ok": True})


def _token_response(token: str) -> Response:
    """Crea una respuesta privada con un token."""
    response = Response({"token": token})
    response["Cache-Control"] = "no-store"
    return response
