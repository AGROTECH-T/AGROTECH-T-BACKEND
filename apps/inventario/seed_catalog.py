"""Semilla del catálogo de inventario.

Propósito: cargar categorías, unidades base y presentaciones del sistema.
Contexto: se ejecuta en migración y puede reutilizarse en tests.
@author Cristian Deysdayr Jimenez
"""
from decimal import Decimal

from apps.inventario.models import (
    CategoryUnit,
    InventoryBaseUnit,
    InventoryCategory,
    InventoryUnit,
    MagnitudeNature,
)

CATEGORIES = (
    ("alimento_concentrado", "Alimento/Concentrado", ""),
    ("medicamento_veterinario", "Medicamento/Veterinario", ""),
    ("semilla", "Semilla", ""),
    ("fertilizante_abono", "Fertilizante/Abono", ""),
    ("herramienta_equipo", "Herramienta/Equipo", ""),
    ("agua_riego_liquidos", "Agua/Riego/Líquidos", ""),
    ("insumo_general", "Insumo general/Varios", ""),
)

BASE_UNITS = (
    ("kg", "Kilogramo", "kg", MagnitudeNature.CONTINUA, 3),
    ("g", "Gramo", "g", MagnitudeNature.CONTINUA, 0),
    ("lb", "Libra", "lb", MagnitudeNature.CONTINUA, 3),
    ("t", "Tonelada", "t", MagnitudeNature.CONTINUA, 3),
    ("l", "Litro", "L", MagnitudeNature.CONTINUA, 3),
    ("ml", "Mililitro", "mL", MagnitudeNature.CONTINUA, 0),
    ("gal", "Galón", "gal", MagnitudeNature.CONTINUA, 3),
    ("m3", "Metro cúbico", "m³", MagnitudeNature.CONTINUA, 3),
    ("u", "Unidad", "u", MagnitudeNature.DISCRETA, 0),
    ("m", "Metro", "m", MagnitudeNature.CONTINUA, 3),
)

# code, label, base_code, factor, nature, decimals
UNITS = (
    ("u", "Unidad (u)", "u", "1", MagnitudeNature.DISCRETA, 0),
    ("par", "Par", "u", "2", MagnitudeNature.DISCRETA, 0),
    ("docena", "Docena (12 u)", "u", "12", MagnitudeNature.DISCRETA, 0),
    ("kg", "Kilogramo (kg)", "kg", "1", MagnitudeNature.CONTINUA, 3),
    ("g", "Gramo (g)", "g", "1", MagnitudeNature.CONTINUA, 0),
    ("lb", "Libra (lb, 0.4536 kg)", "kg", "0.4536", MagnitudeNature.CONTINUA, 3),
    ("t", "Tonelada (t, 1000 kg)", "kg", "1000", MagnitudeNature.CONTINUA, 3),
    ("l", "Litro (L)", "l", "1", MagnitudeNature.CONTINUA, 3),
    ("ml", "Mililitro (mL)", "ml", "1", MagnitudeNature.CONTINUA, 0),
    ("gal", "Galón (3.785 L)", "l", "3.785", MagnitudeNature.CONTINUA, 3),
    ("m", "Metro (m)", "m", "1", MagnitudeNature.CONTINUA, 3),
    ("m3", "Metro cúbico (1000 L)", "l", "1000", MagnitudeNature.CONTINUA, 3),
    ("bulto_25kg", "Bulto (25 kg)", "kg", "25", MagnitudeNature.PRESENTACION, 0),
    ("bulto_40kg", "Bulto (40 kg)", "kg", "40", MagnitudeNature.PRESENTACION, 0),
    ("bulto_50kg", "Bulto (50 kg)", "kg", "50", MagnitudeNature.PRESENTACION, 0),
    ("bulto_65kg", "Bulto (65 kg)", "kg", "65", MagnitudeNature.PRESENTACION, 0),
    ("bulto_120kg", "Bulto (120 kg)", "kg", "120", MagnitudeNature.PRESENTACION, 0),
    ("caneca_20l", "Caneca (20 L)", "l", "20", MagnitudeNature.PRESENTACION, 0),
    ("caneca_60l", "Caneca (60 L)", "l", "60", MagnitudeNature.PRESENTACION, 0),
    ("caneca_200l", "Caneca (200 L)", "l", "200", MagnitudeNature.PRESENTACION, 0),
    ("caja_6u", "Caja (6 u)", "u", "6", MagnitudeNature.PRESENTACION, 0),
    ("caja_12u", "Caja (12 u)", "u", "12", MagnitudeNature.PRESENTACION, 0),
    ("caja_24u", "Caja (24 u)", "u", "24", MagnitudeNature.PRESENTACION, 0),
    ("paquete_10u", "Paquete (10 u)", "u", "10", MagnitudeNature.PRESENTACION, 0),
    ("paquete_50u", "Paquete (50 u)", "u", "50", MagnitudeNature.PRESENTACION, 0),
    ("paquete_100u", "Paquete (100 u)", "u", "100", MagnitudeNature.PRESENTACION, 0),
    ("rollo_10m", "Rollo (10 m)", "m", "10", MagnitudeNature.PRESENTACION, 0),
    ("rollo_50m", "Rollo (50 m)", "m", "50", MagnitudeNature.PRESENTACION, 0),
    ("rollo_100m", "Rollo (100 m)", "m", "100", MagnitudeNature.PRESENTACION, 0),
    ("dosis", "Dosis", "u", "1", MagnitudeNature.DISCRETA, 0),
    ("frasco_100ml", "Frasco (100 mL)", "ml", "100", MagnitudeNature.PRESENTACION, 0),
    ("frasco_250ml", "Frasco (250 mL)", "ml", "250", MagnitudeNature.PRESENTACION, 0),
    ("frasco_500ml", "Frasco (500 mL)", "ml", "500", MagnitudeNature.PRESENTACION, 0),
    ("ampolla_5ml", "Ampolla (5 mL)", "ml", "5", MagnitudeNature.PRESENTACION, 0),
    ("ampolla_10ml", "Ampolla (10 mL)", "ml", "10", MagnitudeNature.PRESENTACION, 0),
    ("sobre_10g", "Sobre (10 g)", "g", "10", MagnitudeNature.PRESENTACION, 0),
    ("sobre_50g", "Sobre (50 g)", "g", "50", MagnitudeNature.PRESENTACION, 0),
    ("sobre_100g", "Sobre (100 g)", "g", "100", MagnitudeNature.PRESENTACION, 0),
    ("sobre_500g", "Sobre (500 g)", "g", "500", MagnitudeNature.PRESENTACION, 0),
)

# category_code -> unit codes
CATEGORY_UNITS = {
    "alimento_concentrado": [
        "u", "kg", "g", "lb", "bulto_25kg", "bulto_40kg", "bulto_50kg",
        "bulto_65kg", "bulto_120kg", "t",
    ],
    "medicamento_veterinario": [
        "u", "dosis", "frasco_100ml", "frasco_250ml", "frasco_500ml",
        "ampolla_5ml", "ampolla_10ml", "ml", "l", "sobre_10g", "sobre_50g",
        "sobre_100g", "sobre_500g", "kg", "g",
    ],
    "semilla": [
        "kg", "g", "lb", "bulto_25kg", "bulto_40kg", "bulto_50kg",
        "bulto_120kg", "sobre_10g", "sobre_50g", "sobre_100g", "sobre_500g",
    ],
    "fertilizante_abono": [
        "kg", "g", "bulto_25kg", "bulto_40kg", "bulto_50kg", "bulto_65kg",
        "bulto_120kg", "t", "l", "ml", "caneca_20l", "caneca_60l", "caneca_200l",
    ],
    "herramienta_equipo": [
        "u", "par", "caja_6u", "caja_12u", "caja_24u", "docena",
    ],
    "insumo_general": [
        "u", "par", "docena", "caja_6u", "caja_12u", "caja_24u",
        "paquete_10u", "paquete_50u", "paquete_100u", "rollo_10m", "rollo_50m",
        "rollo_100m", "m", "kg", "g", "l", "ml",
    ],
    "agua_riego_liquidos": [
        "l", "ml", "caneca_20l", "caneca_60l", "caneca_200l", "gal", "m3",
    ],
}

# Mapeo desde el catálogo TextChoices anterior
LEGACY_CATEGORY = {
    "concentrados": "alimento_concentrado",
    "vitaminas_medicamentos": "medicamento_veterinario",
    "cama_cascarilla": "insumo_general",
    "fertilizantes": "fertilizante_abono",
    "forrajes": "alimento_concentrado",
    "herramientas": "herramienta_equipo",
    "otros": "insumo_general",
}

LEGACY_UNIT = {
    "kg": "kg",
    "g": "g",
    "bulto_40kg": "bulto_40kg",
    "saco": "bulto_40kg",
    "litro": "l",
    "unidad": "u",
    "arroba": "kg",
    "caneca": "caneca_20l",
}


def seed_catalog() -> None:
    """Crea o actualiza el catálogo del sistema."""
    categories = {}
    for code, name, description in CATEGORIES:
        categories[code], _ = InventoryCategory.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "description": description,
                "is_active": True,
            },
        )

    bases = {}
    for code, name, symbol, nature, decimals in BASE_UNITS:
        bases[code], _ = InventoryBaseUnit.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "symbol": symbol,
                "nature": nature,
                "decimal_places": decimals,
            },
        )

    units = {}
    for code, label, base_code, factor, nature, decimals in UNITS:
        units[code], _ = InventoryUnit.objects.update_or_create(
            code=code,
            defaults={
                "label": label,
                "base_unit": bases[base_code],
                "factor": Decimal(factor),
                "nature": nature,
                "decimal_places": decimals,
                "is_system": True,
                "is_active": True,
            },
        )

    for category_code, unit_codes in CATEGORY_UNITS.items():
        category = categories[category_code]
        for unit_code in unit_codes:
            CategoryUnit.objects.get_or_create(
                category=category,
                unit=units[unit_code],
            )
