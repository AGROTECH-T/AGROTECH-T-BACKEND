"""Errores de dominio y respuestas HTTP uniformes.

@author Cristian Deysdayr Jimenez
"""
from rest_framework import status
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.views import exception_handler

from apps.accounts.errors import AppError
from apps.accounts.limit_message import attempts_limit_message


def api_exception_handler(exc: Exception, context: dict) -> Response | None:
    """Convierte errores de dominio y DRF al contrato JSON."""
    if isinstance(exc, AppError):
        return Response({"error": exc.message, **exc.extra}, status=exc.status_code)
    if isinstance(exc, Throttled):
        return Response(
            {"error": attempts_limit_message(exc.wait)},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    response = exception_handler(exc, context)
    if response is not None and response.status_code >= status.HTTP_400_BAD_REQUEST:
        response.data = {"error": _first_message(response.data)}
    return response


def _first_message(data) -> str:
    """Extrae el primer mensaje de una estructura DRF."""
    if isinstance(data, dict):
        return _first_message(next(iter(data.values())))
    if isinstance(data, list):
        return _first_message(data[0])
    return str(data)
