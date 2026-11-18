"""Lectura de parámetros del historial de movimientos.

Propósito: validar tipo, fechas y paginación en un solo lugar.
@author Cristian Deysdayr Jimenez
"""
from datetime import date

from apps.accounts.errors import AppError


def optional_date(raw: str | None, label: str) -> date | None:
    """Lee una fecha YYYY-MM-DD o nada."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise AppError(f"{label} no es válida", 400) from exc


def positive_int(
    raw: str | None,
    label: str,
    *,
    default: int,
    limit: int | None = None,
) -> int:
    """Lee un entero positivo, con tope opcional."""
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise AppError(f"{label} no es válida", 400) from exc
    if value < 1:
        raise AppError(f"{label} no es válida", 400)
    if limit is not None and value > limit:
        return limit
    return value
