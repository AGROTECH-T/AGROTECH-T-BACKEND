"""Expone el token CSRF para clientes con cookies HttpOnly.

@author Cristian Deysdayr Jimenez
"""

from django.middleware.csrf import get_token
from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.api.serializers import CSRFTokenResponseSerializer


class CSRFTokenView(APIView):
    """Entrega el token CSRF en el cuerpo de la respuesta."""

    authentication_classes = []
    permission_classes = []

    @extend_schema(responses={200: CSRFTokenResponseSerializer})
    def get(self, request: Request) -> Response:
        """Retorna el token y asegura la cookie CSRF HttpOnly."""
        token = get_token(request)
        response = Response({"token": token})
        response["Cache-Control"] = "no-store"
        return response
