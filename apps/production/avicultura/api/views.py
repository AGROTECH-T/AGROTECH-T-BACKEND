"""API del módulo de galpón.

@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from apps.production.avicultura.services import events as event_services
from apps.production.avicultura.services import houses as house_services
from apps.production.avicultura.services import lots as lot_services
class HouseWriteSerializer(serializers.Serializer):
    name = serializers.CharField()
    location = serializers.CharField(required=False, allow_blank=True)
    length_m = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False, allow_null=True
    )
    width_m = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False, allow_null=True
    )
    capacity = serializers.IntegerField(required=False, allow_null=True)
    usual_birds = serializers.IntegerField(required=False, allow_null=True)
    equipment = serializers.CharField(required=False, allow_blank=True)
    status = serializers.CharField(required=False, allow_blank=True)
    bale_kg = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False, allow_null=True
    )
    bales_per_drum = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False, allow_null=True
    )
    feed_mode = serializers.CharField(required=False, allow_blank=True)
    cycle_days = serializers.IntegerField(required=False, allow_null=True)
    mortality_alert = serializers.IntegerField(required=False, allow_null=True)
class HousePatchSerializer(HouseWriteSerializer):
    name = serializers.CharField(required=False)
class LotWriteSerializer(serializers.Serializer):
    house_id = serializers.IntegerField()
    code = serializers.CharField()
    arrived_at = serializers.DateTimeField()
    received = serializers.IntegerField()
    dead_on_arrival = serializers.IntegerField(required=False)
    discarded_on_arrival = serializers.IntegerField(required=False)
    supplier = serializers.CharField(required=False, allow_blank=True)
    documents = serializers.CharField(required=False, allow_blank=True)
    initial_age_days = serializers.IntegerField(required=False)
    initial_weight = serializers.DecimalField(
        max_digits=8,
        decimal_places=3,
        required=False,
        allow_null=True,
    )
    stage = serializers.CharField(required=False, allow_blank=True)
    levante_on = serializers.DateField(required=False, allow_null=True)
    engorde_on = serializers.DateField(required=False, allow_null=True)
    feed_type = serializers.CharField(required=False, allow_blank=True)
    reception_notes = serializers.CharField(required=False, allow_blank=True)
    receiver_name = serializers.CharField()
class LotPatchSerializer(serializers.Serializer):
    code = serializers.CharField(required=False)
    arrived_at = serializers.DateTimeField(required=False)
    supplier = serializers.CharField(required=False, allow_blank=True)
    documents = serializers.CharField(required=False, allow_blank=True)
    initial_age_days = serializers.IntegerField(required=False)
    initial_weight = serializers.DecimalField(
        max_digits=8,
        decimal_places=3,
        required=False,
        allow_null=True,
    )
    stage = serializers.CharField(required=False, allow_blank=True)
    levante_on = serializers.DateField(required=False, allow_null=True)
    engorde_on = serializers.DateField(required=False, allow_null=True)
    feed_type = serializers.CharField(required=False, allow_blank=True)
    reception_notes = serializers.CharField(required=False, allow_blank=True)
    receiver_name = serializers.CharField(required=False, allow_blank=True)
class HouseListCreateView(APIView):
    """Galpones de la finca."""
    @extend_schema(tags=["avicultura"])
    def get(self, request: Request, farm_id: int) -> Response:
        houses = house_services.list_houses(user=request.user, farm_id=farm_id)
        return Response([house_services.house_payload(house) for house in houses])
    @extend_schema(
        request=HouseWriteSerializer,
        responses={201: HouseWriteSerializer},
        tags=["avicultura"],
    )
    def post(self, request: Request, farm_id: int) -> Response:
        serializer = HouseWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        house = house_services.create_house(
            user=request.user,
            farm_id=farm_id,
            **serializer.validated_data,
        )
        house = house_services.list_houses(user=request.user, farm_id=farm_id).get(id=house.id)
        return Response(house_services.house_payload(house), status=status.HTTP_201_CREATED)
class HouseDetailView(APIView):
    """Consulta, cambia o elimina un galpón."""
    @extend_schema(tags=["avicultura"])
    def patch(self, request: Request, farm_id: int, house_id: int) -> Response:
        serializer = HousePatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        house_services.update_house(
            user=request.user,
            farm_id=farm_id,
            house_id=house_id,
            **serializer.validated_data,
        )
        house = house_services.list_houses(user=request.user, farm_id=farm_id).get(id=house_id)
        return Response(house_services.house_payload(house))
    @extend_schema(tags=["avicultura"])
    def delete(self, request: Request, farm_id: int, house_id: int) -> Response:
        house_services.delete_house(
            user=request.user,
            farm_id=farm_id,
            house_id=house_id,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
class LotListCreateView(APIView):
    """Lotes de la finca."""
    @extend_schema(tags=["avicultura"])
    def get(self, request: Request, farm_id: int) -> Response:
        house_id = request.query_params.get("house")
        lots = lot_services.list_lots(
            user=request.user,
            farm_id=farm_id,
            house_id=int(house_id) if house_id else None,
        )
        return Response([lot_services.lot_payload(lot) for lot in lots])
    @extend_schema(request=LotWriteSerializer, tags=["avicultura"])
    def post(self, request: Request, farm_id: int) -> Response:
        serializer = LotWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        lot = lot_services.open_lot(
            user=request.user,
            farm_id=farm_id,
            **serializer.validated_data,
        )
        lot = lot_services.get_lot(user=request.user, farm_id=farm_id, lot_id=lot.id)
        return Response(lot_services.lot_payload(lot), status=status.HTTP_201_CREATED)
class LotDetailView(APIView):
    """Consulta, corrige o elimina un lote."""
    @extend_schema(tags=["avicultura"])
    def get(self, request: Request, farm_id: int, lot_id: int) -> Response:
        lot = lot_services.get_lot(user=request.user, farm_id=farm_id, lot_id=lot_id)
        return Response(lot_services.lot_payload(lot, with_summary=True))
    @extend_schema(request=LotPatchSerializer, tags=["avicultura"])
    def patch(self, request: Request, farm_id: int, lot_id: int) -> Response:
        serializer = LotPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        lot_services.update_lot(
            user=request.user,
            farm_id=farm_id,
            lot_id=lot_id,
            **serializer.validated_data,
        )
        lot = lot_services.get_lot(user=request.user, farm_id=farm_id, lot_id=lot_id)
        return Response(lot_services.lot_payload(lot, with_summary=True))
    @extend_schema(tags=["avicultura"])
    def delete(self, request: Request, farm_id: int, lot_id: int) -> Response:
        lot_services.delete_lot(
            user=request.user,
            farm_id=farm_id,
            lot_id=lot_id,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
