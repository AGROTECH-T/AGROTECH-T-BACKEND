"""Aviso cuando se agotan los intentos.

Propósito: comprobar que el 429 habla de minutos u horas.
Contexto: el texto técnico del regulador no llega al usuario.
@author Cristian Deysdayr Jimenez
"""
from rest_framework.exceptions import Throttled

from apps.accounts.exceptions import api_exception_handler
from apps.accounts.limit_message import attempts_limit_message


def test_wait_of_2097_seconds_reads_as_minutes():
    """2097 segundos se leen como 35 minutos."""
    assert attempts_limit_message(2097) == (
        "Límite de intentos alcanzado. Se restablece en 35 minutos."
    )


def test_full_hour_omits_minutes():
    """Una hora exacta no agrega minutos."""
    assert attempts_limit_message(3600).endswith("en 1 hora.")


def test_hour_and_minutes_stay_together():
    """Una hora con minutos se dice completa."""
    assert attempts_limit_message(3660).endswith("en 1 hora y 1 minuto.")


def test_short_wait_stays_under_a_minute():
    """Menos de un minuto no se expresa en segundos."""
    assert attempts_limit_message(20).endswith("en menos de un minuto.")


def test_missing_wait_asks_to_try_later():
    """Sin tiempo estimado el aviso no inventa una cifra."""
    assert attempts_limit_message(None) == "Límite de intentos alcanzado. Inténtalo más tarde."


def test_handler_returns_429_with_that_text():
    """El manejador reemplaza el aviso técnico del regulador."""
    response = api_exception_handler(Throttled(wait=2097), {})
    assert response is not None
    assert response.status_code == 429
    assert response.data["error"] == attempts_limit_message(2097)
