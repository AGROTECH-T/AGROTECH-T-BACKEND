"""Textos compartidos por los reportes de historial.

Propósito: que el reporte del producto y el de la finca usen las mismas columnas.
@author Cristian Deysdayr Jimenez
"""
from apps.inventario.services.loans import read_loan_note

KIND_LABELS = {"entrada": "Entrada", "salida": "Salida", "ajuste": "Ajuste"}


def kind_label(kind: str) -> str:
    """Nombre visible del tipo de movimiento."""
    return KIND_LABELS.get(kind, kind)


def amount_text(move) -> str:
    """Cantidad con signo. En el ajuste es la diferencia, no el conteo."""
    value = move.stock_after - move.stock_before if move.kind == "ajuste" else move.quantity
    if move.kind == "salida":
        value = -abs(value)
    text = format(abs(value), "f").rstrip("0").rstrip(".") or "0"
    if value > 0:
        return f"+{text}"
    if value < 0:
        return f"-{text}"
    return text


def loan_columns(note: str) -> tuple[str, str, str, str]:
    """Estado, persona, documento y observación de una nota de préstamo.

    Si la nota no es de un préstamo, la observación es la nota completa
    y las demás columnas quedan vacías.
    """
    loan = read_loan_note(note)
    if loan is None:
        return "", "", "", note
    return loan["state"], loan["name"], loan["document"], loan["observation"]
