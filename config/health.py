"""Verificación de infraestructura del backend.

Propósito: confirmar que el proceso HTTP responde sin tocar la base de datos.
Contexto: endpoint público, fuera del esquema de autenticación.
@author Cristian Deysdayr Jimenez
"""
from django.http import HttpRequest, JsonResponse


def health(_request: HttpRequest) -> JsonResponse:
    """Responde el estado del proceso.

    @param _request: solicitud HTTP, no se usa.
    @returns JsonResponse con status ok.
    """
    return JsonResponse({"status": "ok", "project": "agrotech-t-backend"})
