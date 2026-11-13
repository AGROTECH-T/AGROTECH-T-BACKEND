"""Pruebas unitarias del catálogo centralizado de permisos.

No requieren base de datos: el catálogo es estático en código.
"""
import pytest

from apps.farms.models import FarmRole
from apps.permissions.catalog import (
    PERMISSION_CATALOG,
    Permission,
    role_ceiling,
    validate_identifier,
)

ADMIN_CEILING = {"farms.consultar", "inventario.consultar", "inventario.crear"}
OPERATOR_CEILING = {"farms.consultar", "inventario.consultar", "inventario.crear"}
CONSULTATION_CEILING = {"farms.consultar", "inventario.consultar"}


def test_catalog_is_not_empty():
    assert len(PERMISSION_CATALOG) > 0


def test_catalog_has_no_duplicate_keys():
    keys = [perm.key for perm in PERMISSION_CATALOG]
    assert len(keys) == len(set(keys))


def test_catalog_identifiers_are_valid():
    for perm in PERMISSION_CATALOG:
        assert perm.module == validate_identifier(perm.module, "module")
        assert perm.action == validate_identifier(perm.action, "action")


def test_validate_identifier_accepts_valid():
    assert validate_identifier("inventario", "module") == "inventario"
    assert validate_identifier("crear", "action") == "crear"
    assert validate_identifier("consulta_historial", "action") == "consulta_historial"


@pytest.mark.parametrize(
    "value",
    ["", "Inicial", "CONSULTAR", "con sultar", "crear-x", "crear.x", "créar", "consultar "],
)
def test_validate_identifier_rejects_invalid(value):
    with pytest.raises(ValueError):
        validate_identifier(value, "module")


def test_permission_rejects_invalid_identifier():
    with pytest.raises(ValueError):
        Permission(
            module="Inventario",
            action="crear",
            label="x",
            allowed_roles=frozenset(),
        )


def test_admin_ceiling():
    assert role_ceiling(FarmRole.ADMINISTRADOR) == ADMIN_CEILING


def test_operator_ceiling():
    assert role_ceiling(FarmRole.OPERADOR) == OPERATOR_CEILING


def test_consultation_ceiling():
    assert role_ceiling(FarmRole.CONSULTA) == CONSULTATION_CEILING


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        role_ceiling("propietario")


def test_catalog_is_operational_only():
    for perm in PERMISSION_CATALOG:
        assert perm.category == "operational"


def test_catalog_is_db_independent():
    # No usa fixtures de base de datos: el techo se calcula solo del catálogo.
    assert role_ceiling(FarmRole.ADMINISTRADOR) == ADMIN_CEILING
