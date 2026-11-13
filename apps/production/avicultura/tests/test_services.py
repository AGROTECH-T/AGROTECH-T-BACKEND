# ruff: noqa: S101
"""Reglas del galpón: acceso, saldo de aves, cierre y avisos.

@author Cristian Deysdayr Jimenez
"""
from decimal import Decimal
import pytest
from django.utils import timezone
from apps.accounts.errors import AppError
from apps.farms.models import FarmMembership, FarmPermission, FarmRole
from apps.farms.permissions import REGISTRAR_GALPON
from apps.farms.services import create_farm
from apps.production.avicultura.models import AlertState, HouseStatus, LotStatus
from apps.production.avicultura.services import events as event_services
from apps.production.avicultura.services import houses as house_services
from apps.production.avicultura.services import lots as lot_services
@pytest.fixture
def farm_id(user) -> int:
    """Finca de prueba con el usuario como administrador."""
    return create_farm(user=user, name="La Esperanza").farm_id
def _galpon(user, farm_id: int, name: str = "Galpón 1", **data):
    return house_services.create_house(user=user, farm_id=farm_id, name=name, **data)
def _lote(user, farm_id: int, house_id: int, code: str = "L-001", received: int = 100):
    return lot_services.open_lot(
        user=user,
        farm_id=farm_id,
        house_id=house_id,
        code=code,
        arrived_at=timezone.now(),
        received=received,
        receiver_name="Ana Pérez",
    )
@pytest.mark.django_db
def test_crea_galpon_y_calcula_area(user, farm_id):
    """El área sale del largo y el ancho; el sistema no inventa cifras."""
    house = _galpon(user, farm_id, length_m="10", width_m="4")
    payload = house_services.house_payload(house)
    assert payload["area_m2"] == "40"
    assert payload["status"] == HouseStatus.DISPONIBLE
@pytest.mark.django_db
def test_nombre_de_galpon_unico_en_la_finca(user, farm_id):
    """El mismo nombre no se repite dentro de la finca."""
    _galpon(user, farm_id, name="Norte")
    with pytest.raises(AppError) as error:
        _galpon(user, farm_id, name="Norte")
    assert error.value.status_code == 409
@pytest.mark.django_db
def test_consulta_lee_y_no_escribe(user, make_user, farm_id):
    """El rol consulta ve galpones y no puede crearlos."""
    house = _galpon(user, farm_id)
    reader = make_user(identification="87654321")
    FarmMembership.objects.create(
        farm_id=farm_id,
        user=reader,
        role=FarmRole.CONSULTA,
    )
    listed = list(house_services.list_houses(user=reader, farm_id=farm_id))
    assert listed[0].id == house.id
    with pytest.raises(AppError) as error:
        _galpon(reader, farm_id, name="Otro")
    assert error.value.status_code == 403
@pytest.mark.django_db
def test_permiso_directo_permite_registrar(user, make_user, farm_id):
    """Un permiso extra suma el registro sin cambiar el rol."""
    reader = make_user(identification="87654322")
    membership = FarmMembership.objects.create(
        farm_id=farm_id,
        user=reader,
        role=FarmRole.CONSULTA,
    )
    FarmPermission.objects.create(membership=membership, permission=REGISTRAR_GALPON)
    house = _galpon(reader, farm_id, name="Delegado")
    assert house.name == "Delegado"
@pytest.mark.django_db
def test_abrir_lote_ocupa_el_galpon(user, farm_id):
    """La recepción deja el galpón ocupado y el saldo vivo descuenta bajas."""
    house = _galpon(user, farm_id)
    lot = lot_services.open_lot(
        user=user,
        farm_id=farm_id,
        house_id=house.id,
        code="L-010",
        arrived_at=timezone.now(),
        received=130,
        dead_on_arrival=2,
        discarded_on_arrival=1,
        receiver_name="Ana Pérez",
    )
    house.refresh_from_db()
    assert lot.live_count == 127
    assert house.status == HouseStatus.OCUPADO
@pytest.mark.django_db
def test_codigo_de_lote_unico(user, farm_id):
    """El código no se repite en la finca."""
    house = _galpon(user, farm_id)
    _lote(user, farm_id, house.id, code="L-001")
    other = _galpon(user, farm_id, name="Galpón 2")
    with pytest.raises(AppError) as error:
        _lote(user, farm_id, other.id, code="L-001")
    assert error.value.status_code == 409
@pytest.mark.django_db
def test_actualiza_y_borra_un_galpon(user, farm_id):
    """Se puede cambiar el nombre y luego quitar el galpón."""
    house = _galpon(user, farm_id, name="Norte")
    updated = house_services.update_house(
        user=user,
        farm_id=farm_id,
        house_id=house.id,
        name="Sur",
    )
    assert updated.name == "Sur"
    house_services.delete_house(user=user, farm_id=farm_id, house_id=house.id)
    listed = house_services.list_houses(user=user, farm_id=farm_id)
    assert listed.count() == 0
@pytest.mark.django_db
def test_no_renombra_a_un_galpon_existente(user, farm_id):
    """El nombre no se duplica en la misma finca."""
    _galpon(user, farm_id, name="Norte")
    other = _galpon(user, farm_id, name="Sur")
    with pytest.raises(AppError) as error:
        house_services.update_house(
            user=user,
            farm_id=farm_id,
            house_id=other.id,
            name="Norte",
        )
    assert error.value.status_code == 409
