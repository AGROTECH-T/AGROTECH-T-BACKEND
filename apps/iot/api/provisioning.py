"""Authenticated device registration and one-time credential provisioning."""
from django.db import transaction
from django.utils.cache import patch_cache_control
from django.views.decorators.debug import sensitive_variables
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.errors import AppError
from apps.iot import services
from apps.iot.api.pagination import IoTPagination
from apps.iot.api.serializers import CoordinatorSerializer, GatewaySerializer
from apps.iot.api.views import _app_error_response, _authorize_manage


class _ClosedInputSerializer(serializers.Serializer):
    """Reject unknown fields so callers cannot accidentally submit secrets."""

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {field: "campo no permitido" for field in sorted(unknown)}
            )
        return attrs


class DeviceCreateSerializer(_ClosedInputSerializer):
    """Allow only operator supplied display fields; IDs are server generated."""

    name = serializers.CharField(max_length=100)
    location_label = serializers.CharField(max_length=100, required=False, allow_blank=True)


class CredentialActionSerializer(_ClosedInputSerializer):
    """Accept only an action and an optional audit reason."""

    action = serializers.ChoiceField(choices=("rotate", "revoke"))
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class DeviceProvisioningView(APIView):
    """Create an individually identified device and issue its first credential."""

    device_kind = None

    def get(self, request: Request, farm_id: int) -> Response:
        rows = (
            services.list_coordinators(user=request.user, farm_id=farm_id)
            if self.device_kind == "coordinator"
            else services.list_gateways(user=request.user, farm_id=farm_id)
        )
        serializer = (
            CoordinatorSerializer
            if self.device_kind == "coordinator"
            else GatewaySerializer
        )
        paginator = IoTPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(serializer(page, many=True).data)

    @extend_schema(request=DeviceCreateSerializer, responses={201: dict})
    @sensitive_variables("body", "issued", "serializer")
    def post(self, request: Request, farm_id: int) -> Response:
        denied = _authorize_manage(request=request, farm_id=farm_id)
        if denied is not None:
            return denied
        serializer = DeviceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                if self.device_kind == "coordinator":
                    device = services.create_coordinator(
                        user=request.user, farm_id=farm_id, **serializer.validated_data
                    )
                    issued = services.issue_coordinator_credential(
                        user=request.user, farm_id=farm_id, coordinator_uid=device.uid
                    )
                    body = {
                        "uid": device.uid,
                        "name": device.name,
                        "key_version": issued.credential.key_version,
                        "provisioning_key": issued.provisioning_key(),
                    }
                elif self.device_kind == "gateway":
                    device = services.create_gateway(
                        user=request.user, farm_id=farm_id, **serializer.validated_data
                    )
                    issued = services.issue_gateway_credential(
                        user=request.user, farm_id=farm_id, gateway_uid=device.uid
                    )
                    body = {
                        "uid": device.uid,
                        "name": device.name,
                        "username": device.uid,
                        "key_version": issued.credential.key_version,
                        "password": issued.secret,
                    }
                else:
                    return Response({"error": "tipo de dispositivo no válido"}, status=500)
        except AppError as exc:
            return _app_error_response(exc)
        response = Response(body, status=status.HTTP_201_CREATED)
        patch_cache_control(response, no_store=True, no_cache=True, must_revalidate=True)
        response["Pragma"] = "no-cache"
        return response


class CoordinatorProvisioningView(DeviceProvisioningView):
    device_kind = "coordinator"


class GatewayProvisioningView(DeviceProvisioningView):
    device_kind = "gateway"


class CredentialHistoryView(APIView):
    """Return credential metadata without any secret or stored fingerprint."""

    def get(self, request: Request, farm_id: int, uid: str, device_kind: str) -> Response:
        try:
            rows = (
                services.list_coordinator_credentials(
                    user=request.user, farm_id=farm_id, coordinator_uid=uid
                )
                if device_kind == "coordinator"
                else services.list_gateway_credentials(
                    user=request.user, farm_id=farm_id, gateway_uid=uid
                )
            )
        except AppError as exc:
            return _app_error_response(exc)
        return Response([_credential_metadata(row) for row in rows])

    @sensitive_variables("body", "issued", "request")
    def post(self, request: Request, farm_id: int, uid: str, device_kind: str) -> Response:
        denied = _authorize_manage(request=request, farm_id=farm_id)
        if denied is not None:
            return denied
        serializer = CredentialActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        action = serializer.validated_data["action"]
        reason = serializer.validated_data.get("reason", "")
        try:
            if action == "rotate":
                with transaction.atomic():
                    issued = (
                        services.rotate_coordinator_credential(
                            user=request.user, farm_id=farm_id, coordinator_uid=uid, reason=reason
                        )
                        if device_kind == "coordinator"
                        else services.rotate_gateway_credential(
                            user=request.user, farm_id=farm_id, gateway_uid=uid, reason=reason
                        )
                    )
                body = (
                    {"uid": uid, "key_version": issued.credential.key_version,
                     "provisioning_key": issued.provisioning_key()}
                    if device_kind == "coordinator"
                    else {
                        "uid": uid,
                        "username": uid,
                        "key_version": issued.credential.key_version,
                        "password": issued.secret,
                    }
                )
                response = Response(body)
                patch_cache_control(response, no_store=True, no_cache=True, must_revalidate=True)
                response["Pragma"] = "no-cache"
                return response
            if action == "revoke":
                revoked = (
                    services.revoke_coordinator_credential(
                        user=request.user, farm_id=farm_id, coordinator_uid=uid, reason=reason
                    )
                    if device_kind == "coordinator"
                    else services.revoke_gateway_credential(
                        user=request.user, farm_id=farm_id, gateway_uid=uid, reason=reason
                    )
                )
                return Response(_credential_metadata(revoked))
        except AppError as exc:
            return _app_error_response(exc)
        return Response({"error": "acción no válida"}, status=status.HTTP_400_BAD_REQUEST)


def _credential_metadata(row):
    return {
        "key_version": row.key_version,
        "issued_at": row.issued_at,
        "revoked_at": row.revoked_at,
        "revocation_cause": row.revocation_cause or None,
        "active": row.active_slot is not None,
    }


__all__ = ["CoordinatorProvisioningView", "GatewayProvisioningView", "CredentialHistoryView"]
