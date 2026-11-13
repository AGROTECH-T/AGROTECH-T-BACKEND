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
