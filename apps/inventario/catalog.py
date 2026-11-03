"""Catálogo fijo de unidades y categorías de inventario.

Propósito: el usuario solo elige; no configura el catálogo.
Contexto: insumos de finca (concentrado, vitaminas, cama, etc.).
@author Cristian Deysdayr Jimenez
"""
from django.db import models


class InventoryUnit(models.TextChoices):
    """Unidades de medida del sistema."""

    KG = "kg", "Kilogramo (kg)"
    G = "g", "Gramo (g)"
    BULTO_40KG = "bulto_40kg", "Bulto (40 kg)"
    SACO = "saco", "Saco"
    LITRO = "litro", "Litro (L)"
    UNIDAD = "unidad", "Unidad"
    ARROBA = "arroba", "Arroba"
    CANECA = "caneca", "Caneca"


class InventoryCategory(models.TextChoices):
    """Categorías de insumos de finca."""

    CONCENTRADOS = "concentrados", "Concentrados"
    VITAMINAS_MEDICAMENTOS = "vitaminas_medicamentos", "Vitaminas y medicamentos"
    CAMA_CASCARILLA = "cama_cascarilla", "Cama y cascarilla"
    FERTILIZANTES = "fertilizantes", "Fertilizantes"
    FORRAJES = "forrajes", "Forrajes"
    HERRAMIENTAS = "herramientas", "Herramientas"
    OTROS = "otros", "Otros"


def catalog_payload() -> dict:
    """Lista unidades y categorías para que el cliente arme los selects."""
    return {
        "units": [{"id": key, "label": label} for key, label in InventoryUnit.choices],
        "categories": [
            {"id": key, "label": label} for key, label in InventoryCategory.choices
        ],
    }
