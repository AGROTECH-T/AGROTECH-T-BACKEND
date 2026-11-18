# ruff: noqa: S101
"""Soportes de las FK compuestas de ``iot.0002`` (IoT-02.2-R2, sin base de datos).

Propósito: detectar una modificación futura que elimine el ``UNIQUE (id, farm)``
de las tablas referenciadas o los índices ``(x, farm)`` de las que referencian.
Django no conoce las FK compuestas: si desaparecieran estos soportes de
``Meta``, ``makemigrations`` generaría su eliminación y MariaDB la rechazaría
(el índice lo usa una FK) o dejaría de garantizar la misma finca.
Se comprueban los modelos y el estado proyectado por las migraciones en disco.
"""
import importlib

import pytest
from django.db import models
from django.db.migrations.loader import MigrationLoader

from apps.iot.models import (
    Coordinator,
    CoordinatorGateway,
    Gateway,
    GatewayCredential,
    Sensor,
    SensorSource,
)

FKS_MODULE = importlib.import_module("apps.iot.migrations.0002_composite_farm_fks")
SOURCE_FKS_MODULE = importlib.import_module("apps.iot.migrations.0010_sensor_source")

REFERENCED_UNIQUES = {
    Gateway: ("iot_gateway_id_farm_uniq", ("id", "farm")),
    Coordinator: ("iot_coord_id_farm_uniq", ("id", "farm")),
    SensorSource: ("iot_ss_id_coord_farm_uniq", ("id", "coordinator", "farm")),
}
REFERENCING_INDEXES = {
    Sensor: {
        ("coordinator", "farm"): "iot_sensor_coord_farm_idx",
        ("source", "coordinator", "farm"): "iot_sn_source_fk_idx",
    },
    SensorSource: {("coordinator", "farm"): "iot_ss_coord_farm_idx"},
    CoordinatorGateway: {
        ("coordinator", "farm"): "iot_cg_coord_farm_idx",
        ("gateway", "farm"): "iot_cg_gw_farm_idx",
    },
}
EXPECTED_FKS = (
    ("iot_sensor_coord_farm_fk", "iot_sensors", "coordinator_id", "iot_coordinators"),
    ("iot_cg_coord_farm_fk", "iot_coordinator_gateways", "coordinator_id", "iot_coordinators"),
    ("iot_cg_gw_farm_fk", "iot_coordinator_gateways", "gateway_id", "iot_gateways"),
)


def _uniques(constraints):
    return {
        c.name: tuple(c.fields)
        for c in constraints
        if isinstance(c, models.UniqueConstraint) and c.condition is None
    }


def _indexes(indexes):
    return {tuple(i.fields): i.name for i in indexes}


@pytest.mark.parametrize("model", list(REFERENCED_UNIQUES))
def test_referenced_tables_declare_unique_id_farm(model):
    name, fields = REFERENCED_UNIQUES[model]
    assert _uniques(model._meta.constraints).get(name) == fields


@pytest.mark.parametrize("model", list(REFERENCING_INDEXES))
def test_referencing_tables_declare_x_farm_indexes(model):
    declared = _indexes(model._meta.indexes)
    for fields, name in REFERENCING_INDEXES[model].items():
        assert declared.get(fields) == name


def test_migration_state_keeps_supports():
    state = MigrationLoader(None, ignore_no_migrations=True).project_state()
    for model, (name, fields) in REFERENCED_UNIQUES.items():
        options = state.models[("iot", model._meta.model_name)].options
        assert _uniques(options["constraints"]).get(name) == fields
    for model, expected in REFERENCING_INDEXES.items():
        options = state.models[("iot", model._meta.model_name)].options
        declared = _indexes(options["indexes"])
        for fields, name in expected.items():
            assert declared.get(fields) == name


def test_composite_fk_catalog_is_unchanged():
    assert FKS_MODULE.COMPOSITE_FKS == EXPECTED_FKS


def test_composite_fks_point_to_tables_and_columns_of_the_models():
    tables = {m._meta.db_table: m for m in (Gateway, Coordinator, Sensor, CoordinatorGateway)}
    for _name, table, column, target in FKS_MODULE.COMPOSITE_FKS:
        model = tables[table]
        assert column in {f.column for f in model._meta.concrete_fields}
        assert "farm_id" in {f.column for f in model._meta.concrete_fields}
        assert target in tables
        assert FKS_MODULE.expected_definition("mysql", column, target) == [
            (column, target, "id"),
            ("farm_id", target, "farm_id"),
        ]


def test_farm_and_device_relations_are_protected():
    for model in (Gateway, Coordinator, Sensor, SensorSource, CoordinatorGateway):
        assert model._meta.get_field("farm").remote_field.on_delete is models.PROTECT
    assert Sensor._meta.get_field("coordinator").remote_field.on_delete is models.PROTECT
    assert Sensor._meta.get_field("source").remote_field.on_delete is models.PROTECT
    assert SensorSource._meta.get_field("coordinator").remote_field.on_delete is models.PROTECT
    for field in ("coordinator", "gateway"):
        relation = CoordinatorGateway._meta.get_field(field).remote_field
        assert relation.on_delete is models.PROTECT
    gateway = GatewayCredential._meta.get_field("gateway").remote_field
    assert gateway.on_delete is models.PROTECT


def test_source_fk_catalog_preserves_farm_and_coordinator_scope():
    assert [
        (spec.name, spec.table, spec.columns, spec.target_table, spec.target_columns)
        for spec in SOURCE_FKS_MODULE.SOURCE_FKS
    ] == [
        (
            "iot_ss_coord_farm_fk",
            "iot_sensor_sources",
            ("coordinator_id", "farm_id"),
            "iot_coordinators",
            ("id", "farm_id"),
        ),
        (
            "iot_sensor_source_coord_farm_fk",
            "iot_sensors",
            ("source_id", "coordinator_id", "farm_id"),
            "iot_sensor_sources",
            ("id", "coordinator_id", "farm_id"),
        ),
    ]


def test_legacy_source_relation_stays_nullable_in_model_and_migration_state():
    assert Sensor._meta.get_field("source").null is True
    state = MigrationLoader(None, ignore_no_migrations=True).project_state()
    assert state.models[("iot", "sensor")].get_field("source").null is True


def test_gateway_credential_has_no_secret_farm_or_expiry_fields():
    names = {f.name for f in GatewayCredential._meta.get_fields()}
    assert {"secret", "farm", "expires_at", "last_used_at"}.isdisjoint(names)
    assert {"fingerprint", "key_version", "active_slot", "revocation_cause"} <= names
