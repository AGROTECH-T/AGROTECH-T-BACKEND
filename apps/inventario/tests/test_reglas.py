# ruff: noqa: S101
"""Reglas de catálogo, productos e historial de inventario.

Propósito: cubrir las validaciones que no pasan por el contrato HTTP
y los textos compartidos por los reportes.
@author Cristian Deysdayr Jimenez
"""
from decimal import Decimal

import pytest

from apps.accounts.errors import AppError
from apps.farms.services import create_farm
from apps.inventario import services
from apps.inventario.models import CategoryUnit, MovementKind
from apps.inventario.seed_catalog import seed_catalog
from apps.inventario.services import categories as category_services
from apps.inventario.services import history as history_services
from apps.inventario.services import products as product_services


@pytest.fixture(autouse=True)
def _catalog(db):
    seed_catalog()


@pytest.fixture
def farm(user) -> int:
    return create_farm(user=user, name="La Esperanza").farm_id


def _product(user, farm_id: int, **data):
    return services.create_product(
        user=user,
        farm_id=farm_id,
        name=data.pop("name", "Semilla de maíz"),
        unit_code=data.pop("unit_code", "kg"),
        category_code=data.pop("category_code", "semilla"),
        **data,
    )


def _error(callable_under_test, status: int) -> AppError:
    with pytest.raises(AppError) as caught:
        callable_under_test()
    assert caught.value.status_code == status
    return caught.value


@pytest.mark.django_db
def test_lista_rechaza_orden_y_estado_desconocidos(user, farm):
    _error(
        lambda: product_services.list_products(user=user, farm_id=farm, order="color"),
        400,
    )
    _error(
        lambda: product_services.list_products(user=user, farm_id=farm, status="todo"),
        400,
    )


@pytest.mark.django_db
def test_lista_busca_por_codigo_y_ordena(user, farm):
    first = _product(user, farm, name="Alimento", barcode="770123")
    second = _product(user, farm, name="Cal", category_code="insumo_general", unit_code="kg")
    services.register_movement(
        user=user,
        farm_id=farm,
        product_id=second.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("4"),
    )

    by_code = product_services.list_products(user=user, farm_id=farm, search="7701")
    assert list(by_code) == [first]

    by_quantity = product_services.list_products(user=user, farm_id=farm, order="quantity")
    assert list(by_quantity)[0] == first

    recent = product_services.list_products(user=user, farm_id=farm, order="last_movement")
    assert list(recent)[0] == second

    by_category = product_services.list_products(
        user=user, farm_id=farm, category_code="semilla"
    )
    assert list(by_category) == [first]


@pytest.mark.django_db
def test_producto_valida_catalogo_y_codigo(user, farm):
    _error(lambda: _product(user, farm, name="   "), 400)
    _error(lambda: _product(user, farm, name="x" * 101), 400)
    _error(lambda: _product(user, farm, min_stock=Decimal("-1")), 400)
    _error(lambda: _product(user, farm, category_code="inventada"), 400)
    _error(lambda: _product(user, farm, unit_code="inventada"), 400)
    _error(lambda: _product(user, farm, unit_code="u"), 400)
    _error(lambda: _product(user, farm, barcode="1" * 65), 400)

    _product(user, farm, barcode="12345")
    _error(lambda: _product(user, farm, name="Otra", barcode="12345"), 409)


@pytest.mark.django_db
def test_editar_producto_valida_cambios(user, farm):
    product = _product(user, farm, barcode="12345")
    other = _product(user, farm, name="Cal", category_code="insumo_general")

    _error(
        lambda: product_services.update_product(
            user=user,
            farm_id=farm,
            product_id=product.id,
            name="  ",
        ),
        400,
    )
    _error(
        lambda: product_services.update_product(
            user=user,
            farm_id=farm,
            product_id=product.id,
            name="x" * 101,
        ),
        400,
    )
    _error(
        lambda: product_services.update_product(
            user=user,
            farm_id=farm,
            product_id=product.id,
            min_stock=Decimal("-2"),
        ),
        400,
    )
    _error(
        lambda: product_services.update_product(
            user=user,
            farm_id=farm,
            product_id=product.id,
            category_code="inventada",
        ),
        400,
    )
    _error(
        lambda: product_services.update_product(
            user=user,
            farm_id=farm,
            product_id=product.id,
            unit_code="inventada",
        ),
        400,
    )
    _error(
        lambda: product_services.update_product(
            user=user,
            farm_id=farm,
            product_id=product.id,
            unit_code="u",
        ),
        400,
    )
    _error(
        lambda: product_services.update_product(
            user=user,
            farm_id=farm,
            product_id=other.id,
            barcode="12345",
        ),
        409,
    )

    renamed = product_services.update_product(
        user=user,
        farm_id=farm,
        product_id=product.id,
        name="Semilla mejorada",
        barcode="   ",
        min_stock=Decimal("3"),
    )
    assert renamed.name == "Semilla mejorada"
    assert renamed.barcode == ""
    assert renamed.min_stock == Decimal("3")


@pytest.mark.django_db
def test_producto_inactivo_y_borrado(user, farm):
    product = _product(user, farm)
    product_services.set_product_active(
        user=user, farm_id=farm, product_id=product.id, is_active=False
    )

    _error(
        lambda: product_services.get_product(
            user=user, farm_id=farm, product_id=product.id
        ),
        404,
    )
    assert product_services.get_product(
        user=user, farm_id=farm, product_id=product.id, allow_inactive=True
    )

    product_services.delete_product(user=user, farm_id=farm, product_id=product.id)
    product.refresh_from_db()
    assert product.is_removed is True
    assert product.is_active is False
    assert product.barcode == ""
    _error(
        lambda: product_services.delete_product(
            user=user, farm_id=farm, product_id=product.id
        ),
        404,
    )


@pytest.mark.django_db
def test_categoria_copia_unidades_y_crea_presentacion(user, farm):
    with_units = category_services.create_user_category(
        user=user,
        farm_id=farm,
        name="Semillas certificadas",
        unit_codes=["kg", "g"],
    )
    linked = CategoryUnit.objects.filter(category=with_units).values_list(
        "unit__code", flat=True
    )
    assert set(linked) == {"kg", "g"}

    copied = category_services.create_user_category(
        user=user,
        farm_id=farm,
        name="Semilla copiada",
        copy_from="semilla",
    )
    assert CategoryUnit.objects.filter(category=copied).exists()

    both = category_services.create_user_category(
        user=user,
        farm_id=farm,
        name="Semilla mixta",
        copy_from="semilla",
        unit_codes=["kg"],
    )
    assert list(
        CategoryUnit.objects.filter(category=both).values_list("unit__code", flat=True)
    ) == ["kg"]

    with_custom = category_services.create_user_category(
        user=user,
        farm_id=farm,
        name="Sacos",
        unit_label="Saco (7 kg)",
        base_unit_code="kg",
        factor=Decimal("7"),
    )
    assert CategoryUnit.objects.filter(
        category=with_custom, unit__label="Saco (7 kg)"
    ).exists()


@pytest.mark.django_db
def test_categoria_valida_nombre_y_repeticion(user, farm):
    _error(
        lambda: category_services.create_user_category(user=user, farm_id=farm, name="  "),
        400,
    )
    _error(
        lambda: category_services.create_user_category(
            user=user, farm_id=farm, name="x" * 81
        ),
        400,
    )
    _error(
        lambda: category_services.create_user_category(user=user, farm_id=farm, name="!!!"),
        400,
    )

    repeated = category_services.create_user_category(
        user=user, farm_id=farm, name="Semilla"
    )
    assert repeated.code == "semilla_2"


@pytest.mark.django_db
def test_renombrar_categoria_valida_duplicados_y_unidades(user, farm):
    custom = category_services.create_user_category(
        user=user, farm_id=farm, name="Forrajes"
    )

    _error(
        lambda: category_services.rename_category(
            user=user, farm_id=farm, code=custom.code, name="  "
        ),
        400,
    )
    _error(
        lambda: category_services.rename_category(
            user=user, farm_id=farm, code=custom.code, name="x" * 81
        ),
        400,
    )
    _error(
        lambda: category_services.rename_category(
            user=user, farm_id=farm, code=custom.code, name="Semilla"
        ),
        400,
    )
    _error(
        lambda: category_services.rename_category(
            user=user, farm_id=farm, code=custom.code, name="Forrajes verdes", unit_codes=[]
        ),
        400,
    )
    _error(
        lambda: category_services.rename_category(
            user=user,
            farm_id=farm,
            code=custom.code,
            name="Forrajes verdes",
            unit_codes=["inventada"],
        ),
        400,
    )

    renamed = category_services.rename_category(
        user=user,
        farm_id=farm,
        code=custom.code,
        name="Forrajes verdes",
        unit_codes=["kg", "g"],
    )
    assert renamed.name == "Forrajes verdes"
    assert set(
        CategoryUnit.objects.filter(category=renamed).values_list("unit__code", flat=True)
    ) == {"kg", "g"}


@pytest.mark.django_db
def test_borrar_categoria_en_uso(user, farm):
    custom = category_services.create_user_category(
        user=user, farm_id=farm, name="Forrajes", unit_codes=["kg"]
    )
    _product(user, farm, name="Pasto", category_code=custom.code)

    _error(
        lambda: category_services.delete_category(
            user=user, farm_id=farm, code=custom.code
        ),
        400,
    )
    _error(
        lambda: category_services.delete_category(
            user=user, farm_id=farm, code="inventada"
        ),
        404,
    )


def test_textos_de_historial():
    assert history_services.kind_label("entrada") == "Entrada"
    assert history_services.kind_label("otro") == "otro"


class _Move:
    def __init__(self, kind: str, quantity: str, before: str, after: str) -> None:
        self.kind = kind
        self.quantity = Decimal(quantity)
        self.stock_before = Decimal(before)
        self.stock_after = Decimal(after)


def test_cantidad_con_signo():
    assert history_services.amount_text(_Move("entrada", "5", "0", "5")) == "+5"
    assert history_services.amount_text(_Move("salida", "2", "5", "3")) == "-2"
    assert history_services.amount_text(_Move("ajuste", "0", "3", "7")) == "+4"
    assert history_services.amount_text(_Move("ajuste", "0", "7", "7")) == "0"


def test_columnas_de_nota_de_prestamo():
    assert history_services.loan_columns("compra directa") == (
        "",
        "",
        "",
        "compra directa",
    )
    assert history_services.loan_columns("Préstamo: Ana Pérez (10203040). Para el lote") == (
        "Prestado",
        "Ana Pérez",
        "10203040",
        "Para el lote",
    )
    assert history_services.loan_columns("Devolución: Ana Pérez (10203040)") == (
        "Devuelto",
        "Ana Pérez",
        "10203040",
        "",
    )
