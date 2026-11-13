"""Vistas del dominio de fincas.

Propósito: listar, crear, consultar, actualizar y desactivar fincas.
Contexto: la autenticación y el permiso global ya exigen una cuenta activa.
"""
from django.http import Http404
from django.views.decorators.debug import sensitive_variables
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.errors import AppError
from apps.farms import services
from apps.farms.code_attempts import CodeAttemptGuard
from apps.farms.invitation_services import (
    accept_invitation,
    accept_invitation_code,
    cancel_invitation,
    change_invitation_role,
    create_invitation,
    ensure_code_eligible,
    list_invitations,
    preview_invitation_code,
    resend_invitation,
)
from apps.farms.serializers import (
    AuditEventSerializer,
    ChangeMemberRoleSerializer,
    FarmCreateSerializer,
    FarmMemberSerializer,
    FarmResponseSerializer,
    FarmUpdateSerializer,
    HistoryPageSerializer,
    InvitationAcceptResponseSerializer,
    InvitationAcceptSerializer,
    InvitationCodePreviewSerializer,
    InvitationCodeSerializer,
    InvitationCreateSerializer,
    InvitationResponseSerializer,
    InvitationRoleSerializer,
    MemberPageSerializer,
    RevokeMemberSerializer,
    TransferResponseSerializer,
)
from apps.farms.throttles import InvitationAcceptAccountThrottle, InvitationAcceptIpThrottle


class FarmErrorResponseSerializer(serializers.Serializer):
    """Documenta el contrato uniforme de error de Farms."""

    error = serializers.CharField()


class InvitationDeliveryErrorSerializer(serializers.Serializer):
    """Documenta el error controlado de entrega de una invitación."""

    error = serializers.CharField()
    invitation_id = serializers.IntegerField()


class FarmListCreateView(APIView):
    """Lista las fincas propias y permite crear una finca."""

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_list",
        responses={
            200: OpenApiResponse(
                response=FarmResponseSerializer(many=True),
                description="Listado de fincas del usuario autenticado.",
            ),
            401: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description="Autenticación ausente o inválida.",
            ),
        },
    )
    def get(self, request: Request) -> Response:
        """Retorna únicamente las fincas donde el usuario tiene membresía."""
        memberships = services.list_memberships(request.user)
        return Response(FarmResponseSerializer(memberships, many=True).data)

    @extend_schema(
        tags=["farms"],
        request=FarmCreateSerializer,
        responses={
            201: OpenApiResponse(
                response=FarmResponseSerializer,
                description=(
                    "Finca creada y membresía de administrador inicial creada."
                ),
            ),
            400: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description="Datos inválidos o campos adicionales no permitidos.",
            ),
            401: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description="Autenticación ausente o inválida.",
            ),
            403: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description=(
                    "La cuenta no tiene la capacidad global can_create_farms "
                    "(DEC-11) ni conserva la excepción de primera finca (F10.2): "
                    '{"error": "tu cuenta no tiene permiso para crear fincas"}; o el '
                    'correo no está verificado: {"error": "verifica tu correo para '
                    'crear fincas"}. '
                    "Ser administrador de otra finca no la concede."
                ),
            ),
        },
    )
    def post(self, request: Request) -> Response:
        """Crea una finca y asigna al creador el rol administrador."""
        serializer = FarmCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.create_farm(
            user=request.user,
            name=serializer.validated_data["name"],
        )
        return Response(
            FarmResponseSerializer(membership).data,
            status=status.HTTP_201_CREATED,
        )


class FarmDetailView(APIView):
    """Consulta, actualiza o desactiva una finca según membresía y rol."""

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_retrieve",
        responses={
            200: OpenApiResponse(
                response=FarmResponseSerializer,
                description="Detalle de una finca a la que pertenece el usuario.",
            ),
            401: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description="Autenticación ausente o inválida.",
            ),
            404: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description="La finca no existe o no pertenece al usuario.",
            ),
        },
    )
    def get(self, request: Request, farm_id: int) -> Response:
        """Retorna la finca y el rol del usuario, o 404 si no es visible."""
        membership = services.get_readable_membership(
            user=request.user,
            farm_id=farm_id,
        )
        if membership is None:
            raise Http404
        return Response(FarmResponseSerializer(membership).data)

    @extend_schema(
        tags=["farms"],
        request=FarmUpdateSerializer,
        responses={
            200: OpenApiResponse(response=FarmResponseSerializer),
            400: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def put(self, request: Request, farm_id: int) -> Response:
        """Actualiza el nombre; solo el administrador de la finca."""
        serializer = FarmUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = services.update_farm(
            user=request.user,
            farm_id=farm_id,
            name=serializer.validated_data["name"],
        )
        return Response(FarmResponseSerializer(membership).data)

    @extend_schema(
        tags=["farms"],
        responses={
            204: OpenApiResponse(description="Finca desactivada."),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def delete(self, request: Request, farm_id: int) -> Response:
        """Desactiva la finca; solo el administrador actual."""
        services.deactivate_farm(user=request.user, farm_id=farm_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class FarmReactivateView(APIView):
    """Reactiva una finca desactivada (solo administrador actual)."""

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_reactivate",
        responses={
            204: OpenApiResponse(description="Finca reactivada."),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def post(self, request: Request, farm_id: int) -> Response:
        """Reactiva la finca; solo el administrador activo actual."""
        services.reactivate_farm(user=request.user, farm_id=farm_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class HistoryPagination(PageNumberPagination):
    """Paginación limitada al endpoint de historial de auditoría."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class FarmHistoryView(APIView):
    """Consulta el historial de auditoría de una finca (solo administrador)."""

    pagination_class = HistoryPagination

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_history",
        responses={
            200: OpenApiResponse(
                response=HistoryPageSerializer,
                description="Historial paginado de la finca.",
            ),
            401: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description="Autenticación ausente o inválida.",
            ),
            404: OpenApiResponse(
                response=FarmErrorResponseSerializer,
                description="La finca no existe o el usuario no es administrador.",
            ),
        },
    )
    def get(self, request: Request, farm_id: int) -> Response:
        """Retorna los eventos de auditoría de la finca solicitada."""
        events = services.list_farm_events(user=request.user, farm_id=farm_id)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(events, request, view=self)
        serializer = AuditEventSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class FarmMemberListView(APIView):
    """Lista los integrantes de una finca (solo administrador)."""

    pagination_class = HistoryPagination

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_members_list",
        responses={
            200: OpenApiResponse(
                response=MemberPageSerializer,
                description="Listado paginado de integrantes.",
            ),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def get(self, request: Request, farm_id: int) -> Response:
        """Retorna los integrantes de la finca según filtro y búsqueda."""
        status_filter = request.query_params.get("status", "active")
        search = request.query_params.get("search", "")
        members = services.list_farm_members(
            user=request.user,
            farm_id=farm_id,
            status_filter=status_filter,
            search=search,
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(members, request, view=self)
        serializer = FarmMemberSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class FarmMemberDetailView(APIView):
    """Consulta, cambia de rol o revoca una membresía."""

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_member_detail",
        responses={
            200: OpenApiResponse(response=FarmMemberSerializer),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def get(self, request: Request, farm_id: int, membership_id: int) -> Response:
        member = services.get_farm_member(
            user=request.user,
            farm_id=farm_id,
            membership_id=membership_id,
        )
        return Response(FarmMemberSerializer(member).data)

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_member_change_role",
        request=ChangeMemberRoleSerializer,
        responses={
            200: OpenApiResponse(response=FarmMemberSerializer),
            400: OpenApiResponse(response=FarmErrorResponseSerializer),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
            409: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def patch(self, request: Request, farm_id: int, membership_id: int) -> Response:
        serializer = ChangeMemberRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        member = services.change_member_role(
            user=request.user,
            farm_id=farm_id,
            membership_id=membership_id,
            role=serializer.validated_data["role"],
        )
        return Response(FarmMemberSerializer(member).data)

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_member_revoke",
        request=RevokeMemberSerializer,
        responses={
            204: OpenApiResponse(description="Integrante revocado."),
            400: OpenApiResponse(response=FarmErrorResponseSerializer),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
            409: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def delete(self, request: Request, farm_id: int, membership_id: int) -> Response:
        serializer = RevokeMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.revoke_member(
            user=request.user,
            farm_id=farm_id,
            membership_id=membership_id,
            reason=serializer.validated_data.get("reason", ""),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class FarmLeaveView(APIView):
    """Permite que el usuario autenticado abandone la finca."""

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_leave",
        responses={
            204: OpenApiResponse(description="Membresía abandonada."),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
            409: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def post(self, request: Request, farm_id: int) -> Response:
        services.leave_farm(user=request.user, farm_id=farm_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class FarmTransferView(APIView):
    """Transfiere la administración a otra membresía activa."""

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_member_transfer",
        responses={
            200: OpenApiResponse(response=TransferResponseSerializer),
            400: OpenApiResponse(response=FarmErrorResponseSerializer),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def post(self, request: Request, farm_id: int, membership_id: int) -> Response:
        new_admin = services.transfer_admin(
            user=request.user,
            farm_id=farm_id,
            membership_id=membership_id,
        )
        previous_admin = services.get_membership(user=request.user, farm_id=farm_id)
        return Response(
            TransferResponseSerializer(
                {"previous_admin": previous_admin, "new_admin": new_admin}
            ).data
        )


class FarmMemberHistoryView(APIView):
    """Consulta el historial de auditoría de una membresía (solo administrador)."""

    pagination_class = HistoryPagination

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_member_history",
        responses={
            200: OpenApiResponse(
                response=HistoryPageSerializer,
                description="Historial paginado de la membresía.",
            ),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def get(self, request: Request, farm_id: int, membership_id: int) -> Response:
        events = services.list_member_history(
            user=request.user,
            farm_id=farm_id,
            membership_id=membership_id,
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(events, request, view=self)
        serializer = AuditEventSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class FarmInvitationListCreateView(APIView):
    """Lista y crea invitaciones de una finca (solo administrador)."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "invitation_create"

    def get_throttles(self):
        """Aplica la cuota de creación únicamente al método POST."""
        if self.request.method == "POST":
            return super().get_throttles()
        return []

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_invitations_list",
        responses={
            200: OpenApiResponse(response=InvitationResponseSerializer(many=True)),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def get(self, request: Request, farm_id: int) -> Response:
        invitations = list_invitations(user=request.user, farm_id=farm_id)
        return Response(InvitationResponseSerializer(invitations, many=True).data)

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_invitations_create",
        request=InvitationCreateSerializer,
        responses={
            201: OpenApiResponse(response=InvitationResponseSerializer),
            400: OpenApiResponse(response=FarmErrorResponseSerializer),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
            409: OpenApiResponse(response=FarmErrorResponseSerializer),
            429: OpenApiResponse(response=FarmErrorResponseSerializer),
            502: OpenApiResponse(
                response=InvitationDeliveryErrorSerializer,
                description=(
                    "La invitación quedó pendiente, pero el proveedor rechazó "
                    "la entrega del correo."
                ),
            ),
        },
    )
    def post(self, request: Request, farm_id: int) -> Response:
        serializer = InvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = create_invitation(
            user=request.user,
            farm_id=farm_id,
            email=serializer.validated_data["email"],
            role=serializer.validated_data["role"],
        )
        return Response(
            InvitationResponseSerializer(invitation).data,
            status=status.HTTP_201_CREATED,
        )


class FarmInvitationDetailView(APIView):
    """Cambia el rol o cancela una invitación pendiente."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "invitation_resend"

    def get_throttles(self):
        """Aplica la cuota de reenvío únicamente al cambio de rol."""
        if self.request.method == "PATCH":
            return super().get_throttles()
        return []

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_invitation_change_role",
        request=InvitationRoleSerializer,
        responses={
            200: OpenApiResponse(response=InvitationResponseSerializer),
            400: OpenApiResponse(response=FarmErrorResponseSerializer),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
            409: OpenApiResponse(response=FarmErrorResponseSerializer),
            429: OpenApiResponse(response=FarmErrorResponseSerializer),
            502: OpenApiResponse(
                response=InvitationDeliveryErrorSerializer,
                description=(
                    "El rol cambió y la invitación quedó pendiente, pero el "
                    "proveedor rechazó la entrega del correo."
                ),
            ),
        },
    )
    def patch(self, request: Request, farm_id: int, invitation_id: int) -> Response:
        serializer = InvitationRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        invitation = change_invitation_role(
            user=request.user,
            farm_id=farm_id,
            invitation_id=invitation_id,
            role=serializer.validated_data["role"],
        )
        return Response(InvitationResponseSerializer(invitation).data)

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_invitation_cancel",
        responses={
            204: OpenApiResponse(description="Invitación cancelada."),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
            409: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def delete(self, request: Request, farm_id: int, invitation_id: int) -> Response:
        cancel_invitation(
            user=request.user,
            farm_id=farm_id,
            invitation_id=invitation_id,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class FarmInvitationResendView(APIView):
    """Reenvía una invitación pendiente (solo administrador)."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "invitation_resend"

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_invitation_resend",
        responses={
            200: OpenApiResponse(response=InvitationResponseSerializer),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            403: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
            409: OpenApiResponse(response=FarmErrorResponseSerializer),
            429: OpenApiResponse(response=FarmErrorResponseSerializer),
            502: OpenApiResponse(
                response=InvitationDeliveryErrorSerializer,
                description=(
                    "La invitación quedó pendiente y el token se renovó, pero "
                    "el proveedor rechazó la entrega del correo."
                ),
            ),
        },
    )
    def post(self, request: Request, farm_id: int, invitation_id: int) -> Response:
        invitation = resend_invitation(
            user=request.user,
            farm_id=farm_id,
            invitation_id=invitation_id,
        )
        return Response(InvitationResponseSerializer(invitation).data)


class FarmInvitationAcceptView(APIView):
    """Acepta una invitación mediante su token de un solo uso."""

    throttle_classes = [InvitationAcceptAccountThrottle, InvitationAcceptIpThrottle]
    throttle_scope = "invitation_accept"

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_invitation_accept",
        request=InvitationAcceptSerializer,
        responses={
            200: OpenApiResponse(response=InvitationAcceptResponseSerializer),
            400: OpenApiResponse(response=FarmErrorResponseSerializer),
            401: OpenApiResponse(response=FarmErrorResponseSerializer),
            404: OpenApiResponse(response=FarmErrorResponseSerializer),
            409: OpenApiResponse(response=FarmErrorResponseSerializer),
            429: OpenApiResponse(response=FarmErrorResponseSerializer),
        },
    )
    def post(self, request: Request) -> Response:
        serializer = InvitationAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        membership = accept_invitation(
            user=request.user,
            token=serializer.validated_data["token"],
        )
        return Response(
            InvitationAcceptResponseSerializer(
                {
                    "farm_id": membership.farm_id,
                    "membership_id": membership.pk,
                    "role": membership.role,
                    "status": membership.status,
                }
            ).data
        )


class _InvitationCodeView(APIView):
    """Base de previsualizar y aceptar por código (F10.3.3-B).

    Limitación en tres capas, compartidas con la aceptación por enlace:
    - ``invitation_accept``: cuota por cuenta, atómica en Redis (cierre F10.3,
      D-2).
    - ``invitation_accept_ip``: cuota por IP con ``REMOTE_ADDR`` en hash; no
      confía en ``X-Forwarded-For``; atómica en Redis.
    - ``CodeAttemptGuard``: 5 intentos fallidos por cuenta en 15 minutos,
      común a previsualizar y aceptar.
    """

    throttle_classes = [InvitationAcceptAccountThrottle, InvitationAcceptIpThrottle]
    throttle_scope = "invitation_accept"

    @sensitive_variables()
    def run_guarded(self, request: Request, operation):
        """Valida el cuerpo y ejecuta ``operation(code)`` contando los fallos.

        ``sensitive_variables()`` oculta el código en los informes de error de
        este método y de lo que llama sin decorador propio (las lambdas).
        """
        serializer = InvitationCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ensure_code_eligible(request.user)
        guard = CodeAttemptGuard(request.user.pk)
        guard.begin()
        try:
            result = operation(serializer.validated_data["code"])
        except AppError as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                guard.failed()
            else:
                guard.not_failed()
            raise
        guard.not_failed()
        return result


CODE_ERROR_RESPONSES = {
    400: OpenApiResponse(response=FarmErrorResponseSerializer),
    401: OpenApiResponse(response=FarmErrorResponseSerializer),
    403: OpenApiResponse(
        response=FarmErrorResponseSerializer,
        description="Cuenta inactiva o correo sin verificar.",
    ),
    404: OpenApiResponse(
        response=FarmErrorResponseSerializer,
        description=(
            "Código incorrecto, inexistente, vencido, cancelado, ya usado o de "
            "otro correo (respuesta genérica)."
        ),
    ),
    409: OpenApiResponse(
        response=FarmErrorResponseSerializer,
        description="La cuenta ya pertenece a la finca.",
    ),
    429: OpenApiResponse(response=FarmErrorResponseSerializer),
}


class FarmInvitationCodePreviewView(_InvitationCodeView):
    """Muestra la finca, el rol y la expiración de un código, sin consumirlo."""

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_invitation_code_preview",
        request=InvitationCodeSerializer,
        responses={
            200: OpenApiResponse(response=InvitationCodePreviewSerializer),
            **CODE_ERROR_RESPONSES,
        },
    )
    def post(self, request: Request) -> Response:
        preview = self.run_guarded(
            request, lambda code: preview_invitation_code(user=request.user, code=code)
        )
        return Response(InvitationCodePreviewSerializer(preview).data)


class FarmInvitationCodeAcceptView(_InvitationCodeView):
    """Acepta una invitación por código y crea o reactiva la membresía."""

    @extend_schema(
        tags=["farms"],
        operation_id="api_v1_farms_invitation_code_accept",
        request=InvitationCodeSerializer,
        responses={
            200: OpenApiResponse(response=InvitationAcceptResponseSerializer),
            **CODE_ERROR_RESPONSES,
        },
    )
    def post(self, request: Request) -> Response:
        membership = self.run_guarded(
            request, lambda code: accept_invitation_code(user=request.user, code=code)
        )
        return Response(
            InvitationAcceptResponseSerializer(
                {
                    "farm_id": membership.farm_id,
                    "membership_id": membership.pk,
                    "role": membership.role,
                    "status": membership.status,
                }
            ).data
        )
