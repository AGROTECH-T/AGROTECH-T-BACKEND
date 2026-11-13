"""Gestiona las solicitudes HTTP del dominio de cuentas.

@author Cristian Deysdayr Jimenez
"""

from django.middleware.csrf import rotate_token
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.api.serializers import (
    AccountResponseSerializer,
    LoginResponseSerializer,
    LoginSerializer,
    ProfileUpdateSerializer,
    RecoverSerializer,
    RecoveryResponseSerializer,
    RegisterSerializer,
    ResetSerializer,
    SuccessResponseSerializer,
    TokenResponseSerializer,
    VerificationConfirmSerializer,
)
from apps.accounts.api.throttles import (
    LoginIdentifierThrottle,
    RecoveryIdentifierThrottle,
    ResetIdentifierThrottle,
)
from apps.accounts.services import auth_service
from apps.farms.capabilities import (
    can_create_farms,
    can_create_first_farm,
    can_open_inactive_farms,
)
from apps.trazabilidad.models import EventType
from apps.trazabilidad.services import record_audit_event


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

    @extend_schema(request=LoginSerializer, responses={200: LoginResponseSerializer})
    def post(self, request: Request) -> Response:
        """Entrega un token y abre la sesión Django."""
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, token = auth_service.login(**serializer.validated_data)
        request.session["user_id"] = user.pk
        request.session.cycle_key()
        rotate_token(request)
        record_audit_event(
            actor=user,
            event_type=EventType.SESSION_OPENED,
            entity_type="Session",
            entity_id=user.pk,
        )
        response = Response({"token": token, "ok": True})
        response["Cache-Control"] = "no-store"
        return response


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
    """Expone y actualiza la cuenta autenticada sin incluir secretos."""

    @extend_schema(responses={200: AccountResponseSerializer})
    def get(self, request: Request) -> Response:
        """Retorna los datos visibles del usuario actual."""
        return Response(_account_payload(request.user))

    @extend_schema(request=ProfileUpdateSerializer, responses={200: AccountResponseSerializer})
    def patch(self, request: Request) -> Response:
        """Actualiza el nombre, el correo y el celular del perfil."""
        serializer = ProfileUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = auth_service.update_profile(request.user, serializer.validated_data)
        return Response(_account_payload(user))


class LogoutView(APIView):
    """Cierra la sesión autenticada."""

    @extend_schema(request=None, responses={200: SuccessResponseSerializer})
    def post(self, request: Request) -> Response:
        """Revoca el token vigente y destruye la sesión Django."""
        session_user_id = request.session.get("user_id")
        had_own_django_session = session_user_id is not None and str(session_user_id) == str(
            request.user.pk
        )
        request.session.flush()
        auth_service.logout(request.user)
        if had_own_django_session:
            record_audit_event(
                actor=request.user,
                event_type=EventType.SESSION_CLOSED,
                entity_type="Session",
                entity_id=request.user.pk,
            )
        return Response({"ok": True})


class EmailVerificationRequestView(APIView):
    """Solicita un código de verificación de correo."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "verification_request"

    @extend_schema(request=None, responses={200: RecoveryResponseSerializer})
    def post(self, request: Request) -> Response:
        """Envía un OTP de verificación y devuelve su vigencia."""
        wait = auth_service.request_email_verification(request.user)
        response = Response({"ok": True, "wait": wait})
        response["Cache-Control"] = "no-store"
        return response


class EmailVerificationConfirmView(APIView):
    """Confirma el código de verificación de correo."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "verification_confirm"

    @extend_schema(request=VerificationConfirmSerializer, responses={200: SuccessResponseSerializer})
    def post(self, request: Request) -> Response:
        """Verifica el correo consumiendo el código de verificación."""
        serializer = VerificationConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_service.verify_email(request.user, serializer.validated_data["code"])
        return Response({"ok": True})


def _account_payload(user) -> dict:
    """Arma los datos públicos de una cuenta autenticada."""
    return {
        "id": user.pk,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "identification": user.identification,
        "phone": user.phone,
        "correo": user.correo,
        "is_active": user.is_active,
        "email_verified": user.email_verified,
        "can_create_farms": can_create_farms(user),
        "can_create_first_farm": can_create_first_farm(user),
        "can_open_inactive_farms": can_open_inactive_farms(user),
    }


def _token_response(token: str) -> Response:
    """Crea una respuesta privada con un token."""
    response = Response({"token": token})
    response["Cache-Control"] = "no-store"
    return response
