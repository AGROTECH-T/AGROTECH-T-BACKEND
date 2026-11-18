"""Avisos HTTP del sistema.

@author Cristian Deysdayr Jimenez
"""
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.alertas import services as alert_services


class AlertListView(APIView):
    """Avisos del usuario: stock en o bajo el mínimo."""

    @extend_schema(tags=["alertas"])
    def get(self, request: Request) -> Response:
        items = alert_services.list_alerts(user=request.user)
        return Response({"count": len(items), "results": items})
