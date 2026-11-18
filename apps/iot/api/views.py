"""Vistas de consulta IoT delegadas a servicios de dominio."""
from drf_spectacular.utils import extend_schema
from django.db import transaction
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.errors import AppError
from apps.iot import services
from apps.iot.api.pagination import IoTPagination
from apps.iot.api.serializers import (
    AuthorizationHistoryQuerySerializer,
    CoordinatorGatewaySerializer,
    CoordinatorSerializer,
    GatewayAuthorizationCreateSerializer,
    GatewayAuthorizationRevokeSerializer,
    GatewaySerializer,
    MeasurementSerializer,
    MeasurementCorrectionCreateSerializer,
    MeasurementCorrectionSerializer,
    SensorCreateSerializer,
    SensorSerializer,
    SensorSourceCreateSerializer,
    SensorSourceDeactivateSerializer,
    SensorSourceSerializer,
    SensorSourceUpdateSerializer,
)
from apps.iot.services.access import lock_manage_membership, require_read_membership


class _FarmListView(APIView):
    """Comportamiento común: paginar resultados ya autorizados por el servicio."""

    pagination_class = IoTPagination
    serializer_class = None
    service_method = None

    def get(self, request: Request, farm_id: int) -> Response:
        """Obtiene y pagina la lista que el servicio limita a la finca autorizada."""
        results = self.service_method(user=request.user, farm_id=farm_id)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(results, request, view=self)
        return paginator.get_paginated_response(self.serializer_class(page, many=True).data)


@extend_schema(tags=["IoT"])
class GatewayListView(_FarmListView):
    """Lista los gateways de una finca."""

    serializer_class = GatewaySerializer
    service_method = staticmethod(services.list_gateways)


@extend_schema(tags=["IoT"])
class CoordinatorListView(_FarmListView):
    """Lista los coordinadores de una finca."""

    serializer_class = CoordinatorSerializer
    service_method = staticmethod(services.list_coordinators)


@extend_schema(tags=["IoT"])
class SensorListView(_FarmListView):
    """Lista los sensores de una finca y, para administradores, registra uno (C-4)."""

    serializer_class = SensorSerializer
    service_method = staticmethod(services.list_sensors)

    @extend_schema(request=SensorCreateSerializer, responses={201: SensorSerializer})
    def post(self, request: Request, farm_id: int) -> Response:
        denied = _authorize_manage(request=request, farm_id=farm_id)
        if denied is not None:
            return denied
        serializer = SensorCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            sensor = services.create_sensor(
                user=request.user, farm_id=farm_id, **serializer.validated_data
            )
        except AppError as exc:
            return _app_error_response(exc)
        return Response(SensorSerializer(sensor).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["IoT"])
class CoordinatorGatewayAuthorizationView(APIView):
    """Historial (C-2) y alta (C-1) de autorizaciones coordinador-gateway."""

    pagination_class = IoTPagination

    @extend_schema(
        parameters=[AuthorizationHistoryQuerySerializer],
        responses={200: CoordinatorGatewaySerializer(many=True)},
    )
    def get(self, request: Request, farm_id: int, coordinator_uid: str) -> Response:
        try:
            # Membresía antes de validar parámetros: un no miembro recibe 404.
            require_read_membership(user=request.user, farm_id=farm_id)
        except AppError as exc:
            return _app_error_response(exc)
        # ``dict()``: un QueryDict haría que DRF tome el booleano ausente como False.
        query = AuthorizationHistoryQuerySerializer(data=request.query_params.dict())
        query.is_valid(raise_exception=True)
        try:
            rows = services.list_coordinator_authorizations(
                user=request.user,
                farm_id=farm_id,
                coordinator_uid=coordinator_uid,
                include_revoked=query.validated_data["include_revoked"],
            )
        except AppError as exc:
            return _app_error_response(exc)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(CoordinatorGatewaySerializer(page, many=True).data)

    @extend_schema(
        request=GatewayAuthorizationCreateSerializer,
        responses={201: CoordinatorGatewaySerializer},
    )
    def post(self, request: Request, farm_id: int, coordinator_uid: str) -> Response:
        denied = _authorize_manage(request=request, farm_id=farm_id)
        if denied is not None:
            return denied
        serializer = GatewayAuthorizationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            authorization = services.authorize_gateway(
                user=request.user,
                farm_id=farm_id,
                coordinator_uid=coordinator_uid,
                gateway_uid=serializer.validated_data["gateway_uid"],
            )
        except AppError as exc:
            return _app_error_response(exc)
        return Response(
            CoordinatorGatewaySerializer(authorization).data, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["IoT"])
class CoordinatorGatewayRevokeView(APIView):
    """Revoca la autorización vigente (C-3); el historial se conserva."""

    @extend_schema(
        request=GatewayAuthorizationRevokeSerializer,
        responses={200: CoordinatorGatewaySerializer},
    )
    def post(
        self, request: Request, farm_id: int, coordinator_uid: str, gateway_uid: str
    ) -> Response:
        denied = _authorize_manage(request=request, farm_id=farm_id)
        if denied is not None:
            return denied
        serializer = GatewayAuthorizationRevokeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            authorization = services.revoke_gateway_authorization(
                user=request.user,
                farm_id=farm_id,
                coordinator_uid=coordinator_uid,
                gateway_uid=gateway_uid,
                reason=serializer.validated_data.get("reason"),
            )
        except AppError as exc:
            return _app_error_response(exc)
        return Response(CoordinatorGatewaySerializer(authorization).data)


@extend_schema(tags=["IoT"])
class SensorSourceListView(APIView):
    """Read and administratively create physical sensor sources."""

    pagination_class = IoTPagination

    def get(self, request: Request, farm_id: int) -> Response:
        try:
            results = services.list_sensor_sources(
                user=request.user,
                farm_id=farm_id,
                coordinator_uid=request.query_params.get("coordinator_uid"),
            )
        except AppError as exc:
            return _app_error_response(exc)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(results, request, view=self)
        return paginator.get_paginated_response(SensorSourceSerializer(page, many=True).data)

    @extend_schema(request=SensorSourceCreateSerializer, responses={201: SensorSourceSerializer})
    def post(self, request: Request, farm_id: int) -> Response:
        denied = _authorize_manage(request=request, farm_id=farm_id)
        if denied is not None:
            return denied
        serializer = SensorSourceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            source = services.create_sensor_source(
                user=request.user,
                farm_id=farm_id,
                **serializer.validated_data,
            )
        except AppError as exc:
            return _app_error_response(exc)
        return Response(SensorSourceSerializer(source).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["IoT"])
class SensorSourceDetailView(APIView):
    """Update permitted metadata or logically deactivate one source."""

    @extend_schema(request=SensorSourceUpdateSerializer, responses={200: SensorSourceSerializer})
    def patch(self, request: Request, farm_id: int, source_uid) -> Response:
        denied = _authorize_manage(request=request, farm_id=farm_id)
        if denied is not None:
            return denied
        serializer = SensorSourceUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            source = services.update_sensor_source(
                user=request.user,
                farm_id=farm_id,
                source_uid=source_uid,
                **serializer.validated_data,
            )
        except AppError as exc:
            return _app_error_response(exc)
        return Response(SensorSourceSerializer(source).data)

    @extend_schema(request=SensorSourceDeactivateSerializer, responses={200: SensorSourceSerializer})
    def post(
        self, request: Request, farm_id: int, source_uid, action: str | None = None
    ) -> Response:
        if action != "deactivate":
            return Response({"error": "operación no encontrada"}, status=status.HTTP_404_NOT_FOUND)
        denied = _authorize_manage(request=request, farm_id=farm_id)
        if denied is not None:
            return denied
        serializer = SensorSourceDeactivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            source = services.deactivate_sensor_source(
                user=request.user,
                farm_id=farm_id,
                source_uid=source_uid,
                **serializer.validated_data,
            )
        except AppError as exc:
            return _app_error_response(exc)
        return Response(SensorSourceSerializer(source).data)


@extend_schema(tags=["IoT"])
class DeviceDeactivateView(APIView):
    """Desactiva un gateway o un coordinador (baja lógica)."""

    device_kind = "gateway"

    @extend_schema(
        request=SensorSourceDeactivateSerializer,
        responses={200: GatewaySerializer},
    )
    def post(self, request: Request, farm_id: int, uid: str) -> Response:
        denied = _authorize_manage(request=request, farm_id=farm_id)
        if denied is not None:
            return denied
        serializer = SensorSourceDeactivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            if self.device_kind == "coordinator":
                device = services.deactivate_coordinator(
                    user=request.user,
                    farm_id=farm_id,
                    uid=uid,
                    **serializer.validated_data,
                )
                body = CoordinatorSerializer(device).data
            else:
                device = services.deactivate_gateway(
                    user=request.user,
                    farm_id=farm_id,
                    uid=uid,
                    **serializer.validated_data,
                )
                body = GatewaySerializer(device).data
        except AppError as exc:
            return _app_error_response(exc)
        return Response(body)


@extend_schema(tags=["IoT"])
class GatewayDeactivateView(DeviceDeactivateView):
    """Baja lógica de un gateway."""

    device_kind = "gateway"


@extend_schema(tags=["IoT"])
class CoordinatorDeactivateView(DeviceDeactivateView):
    """Baja lógica de un coordinador."""

    device_kind = "coordinator"


@extend_schema(tags=["IoT"])
class SensorDeactivateView(APIView):
    """Desactiva un sensor (baja lógica) y libera su canal."""

    @extend_schema(
        request=SensorSourceDeactivateSerializer,
        responses={200: SensorSerializer},
    )
    def post(self, request: Request, farm_id: int, uid: str) -> Response:
        denied = _authorize_manage(request=request, farm_id=farm_id)
        if denied is not None:
            return denied
        serializer = SensorSourceDeactivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            sensor = services.deactivate_sensor(
                user=request.user,
                farm_id=farm_id,
                uid=uid,
                **serializer.validated_data,
            )
        except AppError as exc:
            return _app_error_response(exc)
        return Response(SensorSerializer(sensor).data)


@extend_schema(tags=["IoT"])
class ReadingListView(APIView):
    """Lista las mediciones de una finca con filtros validados por el servicio."""

    pagination_class = IoTPagination
    serializer_class = MeasurementSerializer

    def get(self, request: Request, farm_id: int) -> Response:
        """Consulta y pagina las mediciones autorizadas de la finca."""
        results = services.list_readings(
            user=request.user,
            farm_id=farm_id,
            sensor_uid=request.query_params.get("sensor_uid"),
            date_from=request.query_params.get("from"),
            date_to=request.query_params.get("to"),
            ordering=request.query_params.get("ordering"),
        )
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(results, request, view=self)
        return paginator.get_paginated_response(self.serializer_class(page, many=True).data)


@extend_schema(tags=["IoT"])
class MeasurementCorrectionListCreateView(APIView):
    """Read correction history or append an administrative correction."""

    def get(self, request: Request, farm_id: int, measurement_uid) -> Response:
        try:
            measurement, corrections = services.list_measurement_corrections(
                user=request.user, farm_id=farm_id, measurement_uid=measurement_uid
            )
        except AppError as exc:
            return _app_error_response(exc)
        return Response({
            "measurement_uid": str(measurement.uid),
            "original_value": _decimal_text(measurement.value),
            "latest_correction": MeasurementCorrectionSerializer(corrections[-1]).data if corrections else None,
            "effective_value": _decimal_text(_effective_value(measurement, corrections)),
            "corrections": MeasurementCorrectionSerializer(corrections, many=True).data,
        })

    @extend_schema(request=MeasurementCorrectionCreateSerializer, responses={201: MeasurementCorrectionSerializer})
    def post(self, request: Request, farm_id: int, measurement_uid) -> Response:
        denied = _authorize_manage(request=request, farm_id=farm_id)
        if denied is not None:
            return denied
        serializer = MeasurementCorrectionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            correction = services.create_measurement_correction(
                user=request.user,
                farm_id=farm_id,
                measurement_uid=measurement_uid,
                **serializer.validated_data,
            )
        except AppError as exc:
            return _app_error_response(exc)
        effective = None if correction.correction_type == "invalidation" else correction.corrected_value
        return Response({
            "correction": MeasurementCorrectionSerializer(correction).data,
            "measurement_uid": str(correction.measurement.uid),
            "effective_value": _decimal_text(effective),
        }, status=status.HTTP_201_CREATED)


# VALIDATION-02: same representation as MeasurementSerializer.original_value
# (DecimalField 5,2 -> string). Raw Decimals were rendered as JSON floats.
_MEASUREMENT_DECIMAL = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)


def _decimal_text(value):
    return None if value is None else _MEASUREMENT_DECIMAL.to_representation(value)


def _effective_value(measurement, corrections):
    if not corrections:
        return measurement.value
    latest = corrections[-1]
    return None if latest.correction_type == "invalidation" else latest.corrected_value


def _app_error_response(error: AppError) -> Response:
    """Translate an expected domain error without exposing internal details."""
    return Response({"error": error.message, **error.extra}, status=error.status_code)


def _authorize_manage(*, request: Request, farm_id: int) -> Response | None:
    """Apply the shared domain gate before validating administrative input."""
    try:
        with transaction.atomic():
            lock_manage_membership(user=request.user, farm_id=farm_id)
    except AppError as exc:
        return _app_error_response(exc)
    return None
