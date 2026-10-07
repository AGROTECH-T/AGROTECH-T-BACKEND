"""Aviso del límite de intentos.

Propósito: decir en cuánto tiempo se puede volver a intentar.
Contexto: la API responde 429 cuando se agota la cuota.
@author Cristian Deysdayr Jimenez
"""


def attempts_limit_message(wait: float | None) -> str:
    """Redacta el límite con el tiempo que falta.

    @param wait: Segundos que indica el regulador, o None.
    @returns Aviso en minutos u horas.
    """
    if wait is None:
        return "Límite de intentos alcanzado. Inténtalo más tarde."
    return f"Límite de intentos alcanzado. Se restablece en {_span(wait)}."


def _span(wait: float) -> str:
    """Convierte segundos a minutos u horas, redondeando hacia arriba."""
    total = max(0, int(wait))
    if total < 60:
        return "menos de un minuto"
    minutes = (total + 59) // 60
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{_count(hours, 'hora', 'horas')} y {_count(mins, 'minuto', 'minutos')}"
    if hours:
        return _count(hours, "hora", "horas")
    return _count(mins, "minuto", "minutos")


def _count(amount: int, one: str, many: str) -> str:
    """Une la cantidad con la palabra en singular o plural."""
    word = one if amount == 1 else many
    return f"{amount} {word}"
