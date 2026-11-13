# ruff: noqa: S101
"""Pruebas del catálogo de métricas IoT (IoT-02.1).

Propósito: verificar códigos, nombres, unidades y tipos de magnitud, y el
rechazo de métricas no registradas. No usan base de datos.
"""
import pytest

from apps.iot import catalog
from apps.iot.catalog import (
    METRICS,
    Metric,
    MetricCode,
    QuantityKind,
    UnknownMetricError,
    get_metric,
    is_known_metric,
    metric_unit,
)


def test_temperature_metric():
    metric = get_metric("temperature")
    assert metric.code == MetricCode.TEMPERATURE
    assert metric.label == "Temperatura"
    assert metric.unit == "°C"
    assert metric.quantity == QuantityKind.TEMPERATURE


def test_relative_humidity_metric():
    metric = get_metric("relative_humidity")
    assert metric.code == MetricCode.RELATIVE_HUMIDITY
    assert metric.label == "Humedad relativa"
    assert metric.unit == "%"
    assert metric.quantity == QuantityKind.PROPORTION


@pytest.mark.parametrize(
    ("code", "unit"),
    [("temperature", "°C"), ("relative_humidity", "%")],
)
def test_metric_unit(code, unit):
    assert metric_unit(code) == unit


def test_initial_catalog_has_exactly_the_approved_metrics():
    assert set(METRICS) == {"temperature", "relative_humidity"}
    assert set(METRICS) == set(MetricCode.values)


@pytest.mark.parametrize(
    "code",
    ["pressure", "", "TEMPERATURE", " temperature", "temperatura", None, 1],
)
def test_unknown_code_is_rejected(code):
    assert is_known_metric(code) is False
    with pytest.raises(UnknownMetricError):
        get_metric(code)
    with pytest.raises(UnknownMetricError):
        metric_unit(code)


def test_unknown_metric_error_is_value_error_without_echo():
    with pytest.raises(ValueError) as excinfo:
        get_metric("secret-looking-code")
    assert "secret-looking-code" not in str(excinfo.value)


def test_known_codes_are_recognized():
    assert is_known_metric("temperature") is True
    assert is_known_metric("relative_humidity") is True


def test_catalog_is_read_only():
    with pytest.raises(TypeError):
        METRICS["pressure"] = Metric(code="pressure", unit="hPa", quantity="x")


def test_metric_definition_is_frozen():
    with pytest.raises(AttributeError):
        get_metric("temperature").unit = "°F"


def test_build_rejects_duplicates():
    duplicated = (
        Metric(MetricCode.TEMPERATURE, "°C", QuantityKind.TEMPERATURE),
        Metric(MetricCode.TEMPERATURE, "°C", QuantityKind.TEMPERATURE),
    )
    with pytest.raises(ValueError, match="duplicada"):
        catalog._build_catalog(duplicated)


def test_build_requires_one_definition_per_code():
    missing = (Metric(MetricCode.TEMPERATURE, "°C", QuantityKind.TEMPERATURE),)
    with pytest.raises(ValueError, match="exactamente una"):
        catalog._build_catalog(missing)


def test_build_rejects_invalid_quantity_and_empty_unit():
    with pytest.raises(ValueError, match="magnitud"):
        catalog._build_catalog((Metric(MetricCode.TEMPERATURE, "°C", "length"),))
    with pytest.raises(ValueError, match="unidad"):
        catalog._build_catalog(
            (Metric(MetricCode.TEMPERATURE, "", QuantityKind.TEMPERATURE),)
        )
