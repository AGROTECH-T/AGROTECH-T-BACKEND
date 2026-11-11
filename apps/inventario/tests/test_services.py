# ruff: noqa: S101
"""Pruebas de reglas de stock, barcode, catálogo y unidades custom.

@author Cristian Deysdayr Jimenez
"""
from datetime import date
from decimal import Decimal

import pytest

from apps.accounts.errors import AppError
from apps.farms.models import FarmMembership, FarmRole
from apps.farms.services import create_farm
from apps.inventario import services
from apps.inventario.models import InventoryUnit, MovementKind
from apps.inventario.seed_catalog import seed_catalog


@pytest.fixture(autouse=True)
def _catalog(db):
    seed_catalog()


@pytest.mark.django_db
def test_create_product_allows_empty_barcode(user):
    """Sin código de barras sí se crea el insumo."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    product = services.create_product(
        user=user,
        farm_id=farm_id,
        name="Fertilizante",
        barcode="   ",
        unit_code="kg",
        category_code="fertilizante_abono",
    )
    assert product.barcode == ""


@pytest.mark.django_db
def test_create_product_rejects_invalid_unit(user):
    """Una unidad fuera del catálogo se rechaza."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    with pytest.raises(AppError) as error:
        services.create_product(
            user=user,
            farm_id=farm_id,
            name="Concentrado",
            barcode="770001",
            unit_code="tonelada_rara",
            category_code="alimento_concentrado",
        )
    assert error.value.status_code == 400


@pytest.mark.django_db
def test_barcode_unique_per_farm(user):
    """El mismo código de barras no se repite en una finca."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    services.create_product(
        user=user,
        farm_id=farm_id,
        name="NPK",
        barcode="770123",
        unit_code="kg",
        category_code="fertilizante_abono",
    )
    with pytest.raises(AppError) as error:
        services.create_product(
            user=user,
            farm_id=farm_id,
            name="Otro",
            barcode="770123",
            unit_code="kg",
            category_code="fertilizante_abono",
        )
    assert error.value.status_code == 409


@pytest.mark.django_db
def test_salida_no_deja_stock_negativo(user):
    """Una salida mayor al stock se rechaza con mensaje claro."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    product = services.create_product(
        user=user,
        farm_id=farm_id,
        name="Concentrado inicio",
        barcode="770999",
        unit_code="bulto_40kg",
        category_code="alimento_concentrado",
    )
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("2"),
    )
    with pytest.raises(AppError) as error:
        services.register_movement(
            user=user,
            farm_id=farm_id,
            product_id=product.id,
            kind=MovementKind.SALIDA,
            quantity=Decimal("5"),
        )
    assert error.value.status_code == 400
    assert "disponible 2" in error.value.message


@pytest.mark.django_db
def test_take_all_vacia_stock(user):
    """Sacar todo deja el stock en cero."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    product = services.create_product(
        user=user,
        farm_id=farm_id,
        name="Concentrado",
        barcode="771000",
        unit_code="bulto_40kg",
        category_code="alimento_concentrado",
    )
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("3"),
    )
    movement = services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.SALIDA,
        take_all=True,
    )
    product.refresh_from_db()
    assert product.quantity == 0
    assert movement.quantity == Decimal("3")
    assert movement.note == "Salida total"
    assert movement.quantity_base == Decimal("120.000000")


@pytest.mark.django_db
def test_custom_unit_generates_internal_code(user):
    """El usuario crea bulto 55 kg y el sistema arma bulto_55kg."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    unit = services.create_custom_unit(
        user=user,
        farm_id=farm_id,
        stem="bulto",
        label="Bulto (55 kg)",
        base_unit_code="kg",
        factor=Decimal("55"),
        category_codes=["alimento_concentrado"],
    )
    assert unit.code == "bulto_55kg"
    assert unit.is_system is False
    product = services.create_product(
        user=user,
        farm_id=farm_id,
        name="Concentrado especial",
        unit_code="bulto_55kg",
        category_code="alimento_concentrado",
    )
    assert product.unit.code == "bulto_55kg"


@pytest.mark.django_db
def test_consulta_no_puede_crear_producto(user, make_user):
    """El rol consulta solo lee."""
    owner = user
    farm_id = create_farm(user=owner, name="La Esperanza").farm_id
    reader = make_user(identification="87654321")
    FarmMembership.objects.create(
        farm_id=farm_id,
        user=reader,
        role=FarmRole.CONSULTA,
    )
    with pytest.raises(AppError) as error:
        services.create_product(
            user=reader,
            farm_id=farm_id,
            name="Algo",
            unit_code="kg",
            category_code="fertilizante_abono",
        )
    assert error.value.status_code == 403


@pytest.mark.django_db
def test_ajuste_requiere_nota_y_admin(user, make_user):
    """Solo administrador ajusta y la nota es obligatoria."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    product = services.create_product(
        user=user,
        farm_id=farm_id,
        name="Semilla",
        unit_code="kg",
        category_code="semilla",
    )
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("10"),
    )
    with pytest.raises(AppError) as error:
        services.register_movement(
            user=user,
            farm_id=farm_id,
            product_id=product.id,
            kind=MovementKind.AJUSTE,
            quantity=Decimal("8"),
            note="",
        )
    assert error.value.status_code == 400

    operator = make_user(identification="11223344")
    FarmMembership.objects.create(
        farm_id=farm_id,
        user=operator,
        role=FarmRole.OPERADOR,
    )
    with pytest.raises(AppError) as error:
        services.register_movement(
            user=operator,
            farm_id=farm_id,
            product_id=product.id,
            kind=MovementKind.AJUSTE,
            quantity=Decimal("8"),
            note="conteo físico",
        )
    assert error.value.status_code == 403

    movement = services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.AJUSTE,
        quantity=Decimal("8"),
        note="conteo físico",
    )
    product.refresh_from_db()
    assert product.quantity == Decimal("8")
    assert movement.stock_before == Decimal("10")
    assert movement.stock_after == Decimal("8")


@pytest.mark.django_db
def test_libra_y_galon_convierten_a_base(user):
    """Libra pasa a kg y galón a litros, con el factor de la finca."""
    del user
    libra = InventoryUnit.objects.get(code="lb")
    galon = InventoryUnit.objects.get(code="gal")
    assert libra.base_unit.code == "kg"
    assert libra.factor == Decimal("0.4536")
    assert galon.base_unit.code == "l"
    assert galon.factor == Decimal("3.785")


@pytest.mark.django_db
def test_listado_muestra_inactivos_y_ordena_por_cantidad(user):
    """El listado puede traer inactivos y ordenar de menor a mayor stock."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    poco = services.create_product(
        user=user,
        farm_id=farm_id,
        name="Poco",
        unit_code="kg",
        category_code="semilla",
    )
    mucho = services.create_product(
        user=user,
        farm_id=farm_id,
        name="Mucho",
        unit_code="kg",
        category_code="semilla",
    )
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=poco.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("1"),
    )
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=mucho.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("9"),
    )
    services.set_product_active(
        user=user,
        farm_id=farm_id,
        product_id=poco.id,
        is_active=False,
    )
    visible = list(services.list_products(user=user, farm_id=farm_id, status="all"))
    assert {item.name for item in visible} == {"Poco", "Mucho"}
    ordered = list(
        services.list_products(user=user, farm_id=farm_id, status="all", order="quantity")
    )
    assert [item.name for item in ordered] == ["Poco", "Mucho"]


@pytest.mark.django_db
def test_historial_finca_filtra_por_fecha(user):
    """El historial de la finca respeta el rango de fechas."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    product = services.create_product(
        user=user,
        farm_id=farm_id,
        name="Cascarilla",
        unit_code="bulto_40kg",
        category_code="fertilizante_abono",
    )
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("2"),
    )
    today = list(
        services.list_farm_movements(
            user=user,
            farm_id=farm_id,
            date_from=date.today(),
            date_to=date.today(),
        )
    )
    assert len(today) == 1
    assert today[0].created_by_id == user.id
    empty = services.list_farm_movements(
        user=user,
        farm_id=farm_id,
        date_from=date(2000, 1, 1),
        date_to=date(2000, 1, 2),
    )
    assert empty.count() == 0


@pytest.mark.django_db
def test_categoria_se_renombra_y_no_se_borra_si_tiene_producto(user):
    """Una categoría usada por un producto no se elimina."""
    from apps.inventario.services.categories import delete_category, rename_category

    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    renamed = rename_category(
        user=user,
        farm_id=farm_id,
        code="fertilizante_abono",
        name="Abonos",
    )
    assert renamed.name == "Abonos"
    services.create_product(
        user=user,
        farm_id=farm_id,
        name="Urea",
        unit_code="kg",
        category_code="fertilizante_abono",
    )
    with pytest.raises(AppError, match="está en un producto"):
        delete_category(user=user, farm_id=farm_id, code="fertilizante_abono")


@pytest.mark.django_db
def test_unidad_cambia_categorias_y_no_se_borra_si_tiene_producto(user):
    """La unidad se puede mover de categoría y no se borra si ya se usa."""
    from apps.inventario.models import CategoryUnit
    from apps.inventario.services.units import delete_unit, update_unit

    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    update_unit(
        user=user,
        farm_id=farm_id,
        code="kg",
        label="Kilo",
        category_codes=["semilla"],
    )
    assert CategoryUnit.objects.filter(unit__code="kg", category__code="semilla").exists()
    services.create_product(
        user=user,
        farm_id=farm_id,
        name="Maíz",
        unit_code="kg",
        category_code="semilla",
    )
    with pytest.raises(AppError, match="está en un producto"):
        delete_unit(user=user, farm_id=farm_id, code="kg")


@pytest.mark.django_db
def test_lista_prestamos_incluye_devueltos_y_pendientes(user):
    """El Excel usa todos los préstamos; la ventana solo los pendientes."""
    from apps.inventario.services import loans as loan_services

    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    product = services.create_product(
        user=user,
        farm_id=farm_id,
        name="Concentrado",
        unit_code="bulto_40kg",
        category_code="alimento_concentrado",
    )
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("4"),
    )
    returned, _ = loan_services.lend_product(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        borrower_name="Ana Pérez",
        document_number="1234567890",
        quantity=Decimal("1"),
    )
    loan_services.return_loan(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        loan_id=returned.id,
        quantity=Decimal("1"),
    )
    loan_services.lend_product(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        borrower_name="Luis Gómez",
        document_number="0987654321",
        quantity=Decimal("1"),
    )

    todos = loan_services.list_loans(user=user, farm_id=farm_id, product_id=product.id)
    pendientes = loan_services.list_open_loans(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
    )
    assert todos.count() == 2
    assert pendientes.count() == 1


@pytest.mark.django_db
def test_nota_prestamo_separa_estado_persona_documento_y_observacion():
    """El reporte lee la nota y deja cada dato en su columna."""
    from apps.inventario.services.loans import read_loan_note

    salida = read_loan_note("Préstamo: Ana Pérez (1234567890)")
    devolucion = read_loan_note(
        "Devolución: Luis Gómez (0987654321). Llegó completo"
    )
    assert salida == {
        "state": "Prestado",
        "name": "Ana Pérez",
        "document": "1234567890",
        "observation": "",
    }
    assert devolucion == {
        "state": "Devuelto",
        "name": "Luis Gómez",
        "document": "0987654321",
        "observation": "Llegó completo",
    }
    assert read_loan_note("Merma por humedad") is None


@pytest.mark.django_db
def test_medida_nueva_no_se_borra_si_ya_se_usa(user):
    """Una medida creada no se borra cuando una unidad cuenta con ella."""
    from apps.inventario.services.units import (
        create_base_unit,
        create_custom_unit,
        delete_base_unit,
    )

    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    base = create_base_unit(
        user=user,
        farm_id=farm_id,
        name="Arroba",
        symbol="@",
        decimals=False,
    )
    create_custom_unit(
        user=user,
        farm_id=farm_id,
        stem="saco",
        label="Saco",
        base_unit_code=base.code,
        factor=Decimal("1"),
        category_codes=["alimento_concentrado"],
    )
    with pytest.raises(AppError, match="se usa para contar"):
        delete_base_unit(user=user, farm_id=farm_id, code=base.code)
