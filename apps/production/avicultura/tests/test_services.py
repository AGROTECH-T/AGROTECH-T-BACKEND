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
def test_mortalidad_resta_y_no_deja_saldo_negativo(user, farm_id):
    """Las bajas bajan el saldo; no se puede restar más aves de las vivas."""
    house = _galpon(user, farm_id, mortality_alert=3)
    lot = _lote(user, farm_id, house.id, received=10)
    event_services.register_event(
        user=user,
        farm_id=farm_id,
        lot_id=lot.id,
        kind="mortalidad",
        occurred_at=timezone.now(),
        responsible="Ana Pérez",
        observed_value=Decimal("3"),
        notes="Tres aves sin vida.",
    )
    lot.refresh_from_db()
    assert lot.live_count == 7
    with pytest.raises(AppError) as error:
        event_services.register_event(
            user=user,
            farm_id=farm_id,
            lot_id=lot.id,
            kind="mortalidad",
            occurred_at=timezone.now(),
            responsible="Ana Pérez",
            observed_value=Decimal("8"),
        )
    assert error.value.status_code == 400
    assert "7" in error.value.message


@pytest.mark.django_db
def test_aislamiento_no_resta_saldo(user, farm_id):
    """Apartar aves no cambia el saldo vivo."""
    house = _galpon(user, farm_id)
    lot = _lote(user, farm_id, house.id, received=50)
    event_services.register_event(
        user=user,
        farm_id=farm_id,
        lot_id=lot.id,
        kind="aislamiento",
        occurred_at=timezone.now(),
        responsible="Ana Pérez",
        observed_value=Decimal("4"),
        notes="Se apartaron por observación.",
    )
    lot.refresh_from_db()
    assert lot.live_count == 50
    assert lot_services.summarize(lot)["isolated"] == 4


@pytest.mark.django_db
def test_aislamiento_devuelve_al_espacio_normal(user, farm_id):
    """Las aves aisladas bajan al reintegrarlas; no se puede devolver de más."""
    house = _galpon(user, farm_id)
    lot = _lote(user, farm_id, house.id, received=50)
    event_services.register_event(
        user=user,
        farm_id=farm_id,
        lot_id=lot.id,
        kind="aislamiento",
        occurred_at=timezone.now(),
        responsible="Ana Pérez",
        observed_value=Decimal("4"),
        payload={"direccion": "apartar"},
    )
    event_services.register_event(
        user=user,
        farm_id=farm_id,
        lot_id=lot.id,
        kind="aislamiento",
        occurred_at=timezone.now(),
        responsible="Ana Pérez",
        observed_value=Decimal("4"),
        payload={"direccion": "devolver"},
        notes="Ya se recuperaron.",
    )
    lot.refresh_from_db()
    assert lot.live_count == 50
    assert lot_services.summarize(lot)["isolated"] == 0
    with pytest.raises(AppError) as error:
        event_services.register_event(
            user=user,
            farm_id=farm_id,
            lot_id=lot.id,
            kind="aislamiento",
            occurred_at=timezone.now(),
            responsible="Ana Pérez",
            observed_value=Decimal("1"),
            payload={"direccion": "devolver"},
        )
    assert error.value.status_code == 400


@pytest.mark.django_db
def test_ajuste_suma_resta_y_exige_nota(user, farm_id):
    """El ajuste mueve el saldo solo con dirección y nota."""
    house = _galpon(user, farm_id)
    lot = _lote(user, farm_id, house.id, received=20)
    with pytest.raises(AppError) as error:
        event_services.register_event(
            user=user,
            farm_id=farm_id,
            lot_id=lot.id,
            kind="ajuste",
            occurred_at=timezone.now(),
            responsible="Ana Pérez",
            observed_value=Decimal("2"),
            payload={"direccion": "sumar"},
        )
    assert error.value.status_code == 400
    event_services.register_event(
        user=user,
        farm_id=farm_id,
        lot_id=lot.id,
        kind="ajuste",
        occurred_at=timezone.now(),
        responsible="Ana Pérez",
        observed_value=Decimal("2"),
        notes="Conteo físico: había dos más.",
        payload={"direccion": "sumar"},
    )
    lot.refresh_from_db()
    assert lot.live_count == 22


@pytest.mark.django_db
def test_corregir_mortalidad_devuelve_el_saldo(user, farm_id):
    """La corrección guarda el valor anterior y restaura las aves."""
    house = _galpon(user, farm_id)
    lot = _lote(user, farm_id, house.id, received=30)
    event = event_services.register_event(
        user=user,
        farm_id=farm_id,
        lot_id=lot.id,
        kind="mortalidad",
        occurred_at=timezone.now(),
        responsible="Ana Pérez",
        observed_value=Decimal("5"),
        notes="Dato mal contado.",
    )
    event_services.correct_event(
        user=user,
        farm_id=farm_id,
        event_id=event.id,
        reason="Era una sola ave.",
        observed_value=Decimal("1"),
    )
    lot.refresh_from_db()
    assert lot.live_count == 29
    assert event.corrections.count() == 1


@pytest.mark.django_db
def test_cierre_deja_disponible_y_reabrir_ocupa(user, farm_id):
    """Al cerrar, el galpón queda disponible; al reabrir, vuelve a ocupado."""
    house = _galpon(user, farm_id)
    lot = _lote(user, farm_id, house.id)
    lot_services.close_lot(user=user, farm_id=farm_id, lot_id=lot.id, note="Salida total")
    house.refresh_from_db()
    lot.refresh_from_db()
    assert lot.status == LotStatus.CERRADO
    assert house.status == HouseStatus.DISPONIBLE
    assert lot.close_summary["nota_cierre"] == "Salida total"
    lot_services.reopen_lot(user=user, farm_id=farm_id, lot_id=lot.id)
    house.refresh_from_db()
    lot.refresh_from_db()
    assert lot.status == LotStatus.ABIERTO
    assert house.status == HouseStatus.OCUPADO


@pytest.mark.django_db
def test_borrar_lote_abierto_libera_el_galpon(user, farm_id):
    """Si no queda otro lote abierto, el galpón vuelve a disponible."""
    house = _galpon(user, farm_id)
    lot = _lote(user, farm_id, house.id)
    lot_services.delete_lot(user=user, farm_id=farm_id, lot_id=lot.id)
    house.refresh_from_db()
    assert house.status == HouseStatus.DISPONIBLE


@pytest.mark.django_db
def test_lote_cerrado_no_acepta_bitacora(user, farm_id):
    """No se anota en un ciclo ya cerrado."""
    house = _galpon(user, farm_id)
    lot = _lote(user, farm_id, house.id)
    lot_services.close_lot(user=user, farm_id=farm_id, lot_id=lot.id)
    with pytest.raises(AppError) as error:
        event_services.register_event(
            user=user,
            farm_id=farm_id,
            lot_id=lot.id,
            kind="ronda",
            occurred_at=timezone.now(),
            responsible="Ana Pérez",
            notes="Ronda tarde.",
        )
    assert error.value.status_code == 400


@pytest.mark.django_db
def test_aviso_por_umbral_y_cierre(user, farm_id):
    """Al llegar al umbral se abre un aviso; cerrarlo pide el permiso de ciclo."""
    house = _galpon(user, farm_id, mortality_alert=3)
    lot = _lote(user, farm_id, house.id, received=40)
    event_services.register_event(
        user=user,
        farm_id=farm_id,
        lot_id=lot.id,
        kind="mortalidad",
        occurred_at=timezone.now(),
        responsible="Ana Pérez",
        observed_value=Decimal("3"),
        notes="Tres bajas del día.",
    )
    alerts = list(event_services.list_alerts(user=user, farm_id=farm_id))
    assert len(alerts) == 1
    closed = event_services.close_alert(
        user=user,
        farm_id=farm_id,
        alert_id=alerts[0].id,
        follow_up="Revisado en ronda.",
    )
    assert closed.state == AlertState.CERRADA


@pytest.mark.django_db
def test_consulta_no_cierra_el_ciclo(user, make_user, farm_id):
    """Cerrar lote o aviso exige el permiso de cierre, no solo ver."""
    house = _galpon(user, farm_id)
    lot = _lote(user, farm_id, house.id)
    reader = make_user(identification="87654323")
    FarmMembership.objects.create(
        farm_id=farm_id,
        user=reader,
        role=FarmRole.CONSULTA,
    )
    with pytest.raises(AppError) as error:
        lot_services.close_lot(user=reader, farm_id=farm_id, lot_id=lot.id)
    assert error.value.status_code == 403


@pytest.mark.django_db
def test_peso_sin_cantidad_no_se_guarda(user, farm_id):
    """Peso, agua o alimento piden cantidad; no basta el responsable."""
    house = _galpon(user, farm_id)
    lot = _lote(user, farm_id, house.id)
    with pytest.raises(AppError) as error:
        event_services.register_event(
            user=user,
            farm_id=farm_id,
            lot_id=lot.id,
            kind="peso",
            occurred_at=timezone.now(),
            responsible="Ana Pérez",
        )
    assert error.value.status_code == 400
    assert "cantidad" in error.value.message


@pytest.mark.django_db
def test_ronda_sin_cantidad_si_se_guarda(user, farm_id):
    """La ronda no pide cifra; sí el responsable."""
    house = _galpon(user, farm_id)
    lot = _lote(user, farm_id, house.id)
    event = event_services.register_event(
        user=user,
        farm_id=farm_id,
        lot_id=lot.id,
        kind="ronda",
        occurred_at=timezone.now(),
        responsible="Ana Pérez",
        notes="Aves activas.",
    )
    assert event.kind == "ronda"
    assert event.observed_value is None


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


@pytest.mark.django_db
def test_lista_lotes_y_su_detalle(user, farm_id):
    """El listado trae el lote abierto y el detalle incluye el resumen."""
    house = _galpon(user, farm_id)
    lot = _lote(user, farm_id, house.id, code="L-020")
    listed = lot_services.list_lots(user=user, farm_id=farm_id, house_id=house.id)
    assert listed.count() == 1
    detail = lot_services.get_lot(user=user, farm_id=farm_id, lot_id=lot.id)
    payload = lot_services.lot_payload(detail, with_summary=True)
    assert payload["code"] == "L-020"
    assert "summary" in payload


@pytest.mark.django_db
def test_lista_y_borra_un_registro_de_bitacora(user, farm_id):
    """La bitácora lista el hecho y al borrarlo el saldo vuelve."""
    house = _galpon(user, farm_id)
    lot = _lote(user, farm_id, house.id, received=20)
    event = event_services.register_event(
        user=user,
        farm_id=farm_id,
        lot_id=lot.id,
        kind="mortalidad",
        occurred_at=timezone.now(),
        responsible="Ana Pérez",
        observed_value=Decimal("2"),
        notes="Dos débiles.",
    )
    listed = event_services.list_events(user=user, farm_id=farm_id, lot_id=lot.id)
    assert listed.count() == 1
    event_services.delete_event(user=user, farm_id=farm_id, event_id=event.id)
    lot.refresh_from_db()
    assert lot.live_count == 20
    assert event_services.list_events(user=user, farm_id=farm_id, lot_id=lot.id).count() == 0
