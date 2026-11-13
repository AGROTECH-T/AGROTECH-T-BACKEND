"""Catálogo fijo de métricas IoT (IoT-02.1, DEC-13).

Propósito: definir en un único lugar qué magnitudes puede medir un sensor, su
código estable, su nombre, su unidad y su tipo de magnitud.

Contexto: igual que ``apps.inventario.catalog``, el catálogo vive en código y
no en base de datos: las métricas se agregan con el desarrollo, no en tiempo de
ejecución. El código es estable porque se guardará en la base (sensores y
mediciones); nunca debe renombrarse. La unidad se deriva siempre de la métrica
y no se almacena por separado, así no puede quedar incoherente. Los rangos
físicos válidos pertenecen a IoT-04 y no se definen aquí.
"""
from dataclasses import dataclass
from types import MappingProxyType

from django.db import models


class MetricCode(models.TextChoices):
    """Códigos estables de las métricas soportadas."""

    TEMPERATURE = "temperature", "Temperatura"
    RELATIVE_HUMIDITY = "relative_humidity", "Humedad relativa"


class QuantityKind(models.TextChoices):
    """Tipo de magnitud física de una métrica."""

    TEMPERATURE = "temperature", "Temperatura"
    PROPORTION = "proportion", "Proporción"


class UnknownMetricError(ValueError):
    """La métrica solicitada no está en el catálogo."""


@dataclass(frozen=True)
class Metric:
    """Definición de una métrica del catálogo."""

    code: str
    unit: str
    quantity: str

    @property
    def label(self) -> str:
        """Nombre descriptivo, tomado de ``MetricCode`` para no duplicarlo."""
        return MetricCode(self.code).label


_DEFINITIONS = (
    Metric(
        code=MetricCode.TEMPERATURE,
        unit="°C",
        quantity=QuantityKind.TEMPERATURE,
    ),
    Metric(
        code=MetricCode.RELATIVE_HUMIDITY,
        unit="%",
        quantity=QuantityKind.PROPORTION,
    ),
)


def _build_catalog(definitions) -> MappingProxyType:
    """Indexa las definiciones y exige una por cada ``MetricCode``."""
    catalog = {}
    for metric in definitions:
        if metric.code in catalog:
            raise ValueError(f"métrica duplicada: {metric.code!r}.")
        if metric.quantity not in QuantityKind.values:
            raise ValueError(f"tipo de magnitud inválido en {metric.code!r}.")
        if not metric.unit:
            raise ValueError(f"la métrica {metric.code!r} no tiene unidad.")
        catalog[str(metric.code)] = metric
    if set(catalog) != set(MetricCode.values):
        raise ValueError("cada MetricCode debe tener exactamente una definición.")
    return MappingProxyType(catalog)


METRICS = _build_catalog(_DEFINITIONS)


def is_known_metric(code) -> bool:
    """Indica si ``code`` es una métrica registrada (coincidencia exacta)."""
    return isinstance(code, str) and code in METRICS


def get_metric(code) -> Metric:
    """Devuelve la definición de la métrica o lanza ``UnknownMetricError``."""
    if not is_known_metric(code):
        raise UnknownMetricError("métrica no registrada en el catálogo")
    return METRICS[code]


def metric_unit(code) -> str:
    """Devuelve la unidad de la métrica o lanza ``UnknownMetricError``."""
    return get_metric(code).unit
