# ruff: noqa: S101
"""Pruebas de autorización y baja lógica en inventario."""
from decimal import Decimal

import pytest

from apps.accounts.errors import AppError
from apps.farms.models import Farm, FarmMembership, FarmRole
from apps.farms.services import create_farm, deactivate_farm
from apps.inventario import services
from apps.inventario.catalog import InventoryCategory, InventoryUnit
from apps.inventario.models import Movement, MovementKind, Product


def _create_farm_with_member(*, owner, member=None, role=FarmRole.CONSULTA) -> int:
    """Crea una finca y, opcionalmente, una membresía adicional."""
    farm_id = create_farm(user=owner, name=f"Finca {owner.identification}").farm_id
    if member is not None:
        FarmMembership.objects.create(
            farm_id=farm_id,
            user=member,
            role=role,
        )
    return farm_id


def _create_product(*, user, farm_id: int, barcode: str = "770001") -> Product:
    """Crea un insumo válido para las pruebas."""
    return services.create_product(
        user=user,
        farm_id=farm_id,
        name="Fertilizante",
        barcode=barcode,
        unit=InventoryUnit.KG,
        category=InventoryCategory.FERTILIZANTES,
    )


@pytest.mark.django_db
def test_consulta_cannot_create_product(make_user):
    """El rol consulta no puede crear insumos."""
    owner = make_user("11111111")
    viewer = make_user("22222222")
    farm_id = _create_farm_with_member(owner=owner, member=viewer)

    with pytest.raises(AppError) as error:
        _create_product(user=viewer, farm_id=farm_id)

    assert error.value.status_code == 403
    assert not Product.objects.filter(farm_id=farm_id).exists()


@pytest.mark.django_db
def test_consulta_cannot_register_movement(make_user):
    """El rol consulta no puede modificar stock ni crear movimientos."""
    owner = make_user("11111111")
    viewer = make_user("22222222")
    farm_id = _create_farm_with_member(owner=owner, member=viewer)
    product = _create_product(user=owner, farm_id=farm_id)

    with pytest.raises(AppError) as error:
        services.register_movement(
            user=viewer,
            farm_id=farm_id,
            product_id=product.id,
            kind=MovementKind.ENTRADA,
            quantity=Decimal("5"),
        )

    assert error.value.status_code == 403
    product.refresh_from_db()
    assert product.quantity == Decimal("0")
    assert not Movement.objects.filter(product=product).exists()


@pytest.mark.django_db
def test_consulta_can_read_inventory(make_user):
    """El rol consulta conserva acceso de lectura a una finca activa."""
    owner = make_user("11111111")
    viewer = make_user("22222222")
    farm_id = _create_farm_with_member(owner=owner, member=viewer)
    product = _create_product(user=owner, farm_id=farm_id)
    movement = services.register_movement(
        user=owner,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("2"),
    )

    products = list(services.list_products(user=viewer, farm_id=farm_id))
    movements = list(
        services.list_movements(
            user=viewer,
            farm_id=farm_id,
            product_id=product.id,
        )
    )

    assert [item.id for item in products] == [product.id]
    assert [item.id for item in movements] == [movement.id]


@pytest.mark.django_db
def test_operator_can_create_product_and_register_movement(make_user):
    """El rol operador conserva las escrituras autorizadas."""
    owner = make_user("11111111")
    operator = make_user("22222222")
    farm_id = _create_farm_with_member(
        owner=owner,
        member=operator,
        role=FarmRole.OPERADOR,
    )

    product = _create_product(user=operator, farm_id=farm_id)
    movement = services.register_movement(
        user=operator,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("3"),
    )

    product.refresh_from_db()
    assert product.quantity == Decimal("3")
    assert movement.created_by == operator
    assert Movement.objects.filter(product=product).count() == 1


@pytest.mark.django_db
def test_user_without_membership_cannot_create_product(make_user):
    """Una cuenta sin membresía no puede operar en la finca."""
    owner = make_user("11111111")
    outsider = make_user("22222222")
    farm_id = create_farm(user=owner, name="Finca privada").farm_id

    with pytest.raises(AppError) as error:
        _create_product(user=outsider, farm_id=farm_id)

    assert error.value.status_code == 404
    assert not Product.objects.filter(farm_id=farm_id).exists()


@pytest.mark.django_db
def test_member_of_other_farm_cannot_modify_resources(make_user):
    """La membresía de una finca no autoriza operar en otra."""
    owner = make_user("11111111")
    other_owner = make_user("22222222")
    farm_id = create_farm(user=owner, name="Finca propia").farm_id
    other_farm_id = create_farm(user=other_owner, name="Finca ajena").farm_id

    with pytest.raises(AppError) as error:
        _create_product(user=owner, farm_id=other_farm_id)

    assert error.value.status_code == 404
    assert not Product.objects.filter(farm_id=other_farm_id).exists()
    assert Product.objects.filter(farm_id=farm_id).count() == 0


@pytest.mark.django_db
def test_deactivated_farm_blocks_inventory_writes(make_user):
    """Una finca desactivada rechaza escrituras aunque el usuario sea admin."""
    owner = make_user("11111111")
    farm_id = create_farm(user=owner, name="Finca temporal").farm_id
    product = _create_product(user=owner, farm_id=farm_id)
    deactivate_farm(user=owner, farm_id=farm_id)

    with pytest.raises(AppError) as create_error:
        _create_product(user=owner, farm_id=farm_id, barcode="770002")
    with pytest.raises(AppError) as movement_error:
        services.register_movement(
            user=owner,
            farm_id=farm_id,
            product_id=product.id,
            kind=MovementKind.ENTRADA,
            quantity=Decimal("1"),
        )

    assert create_error.value.status_code == 404
    assert movement_error.value.status_code == 404
    assert Product.objects.filter(farm_id=farm_id).count() == 1


@pytest.mark.django_db
def test_deactivation_preserves_inventory_records(make_user):
    """La baja lógica no elimina finca, membresía, producto ni movimiento."""
    owner = make_user("11111111")
    farm_id = create_farm(user=owner, name="Finca histórica").farm_id
    product = _create_product(user=owner, farm_id=farm_id)
    movement = services.register_movement(
        user=owner,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("4"),
    )

    deactivate_farm(user=owner, farm_id=farm_id)

    assert Farm.objects.filter(pk=farm_id).exists()
    assert FarmMembership.objects.filter(farm_id=farm_id).exists()
    assert Product.objects.filter(pk=product.pk).exists()
    assert Movement.objects.filter(pk=movement.pk).exists()
    product.refresh_from_db()
    assert product.quantity == Decimal("4")
