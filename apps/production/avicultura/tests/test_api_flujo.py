# ruff: noqa: S101
"""Flujo HTTP del galpón: galpones, lotes, bitácora y avisos.

Propósito: cubrir edición y baja, cierre y reapertura del lote, corrección
de eventos y cierre de alertas, que sólo estaban probados por servicio.
@author Cristian Deysdayr Jimenez
"""
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.farms.services import create_farm
from apps.production.avicultura.services import events as event_services
from apps.production.avicultura.services import houses as house_services
from apps.production.avicultura.services import lots as lot_services


def _root(farm_id: int) -> str:
    return f"/api/v1/avicultura/{farm_id}"


def _farm(user) -> int:
    return create_farm(user=user, name="La Esperanza").farm_id


def _house(user, farm_id: int, **extra):
    return house_services.create_house(user=user, farm_id=farm_id, name="Norte", **extra)


def _lot(user, farm_id: int, house, code: str = "L-100", received: int = 100):
    return lot_services.open_lot(
        user=user,
        farm_id=farm_id,
        house_id=house.id,
        code=code,
        arrived_at=timezone.now(),
        received=received,
        receiver_name="Ana Pérez",
    )


@pytest.mark.django_db
def test_galpon_se_edita_y_se_elimina_con_sus_lotes(login, user):
    """El parche cambia datos y la baja arrastra el lote abierto."""
    farm_id = _farm(user)
    house = _house(user, farm_id, length_m="8", width_m="5")
    _lot(user, farm_id, house)
    client = login(user)

    edited = client.patch(
        f"{_root(farm_id)}/galpones/{house.id}/",
        {"name": "Sur", "location": "Lote 2", "mortality_alert": 7},
        format="json",
    )
    assert edited.status_code == 200
    body = edited.json()
    assert body["name"] == "Sur"
    assert body["mortality_alert"] == 7
    assert body["open_lot_code"] == "L-100"
    assert body["birds_per_m2"] == "2.5"

    invalid = client.patch(
        f"{_root(farm_id)}/galpones/{house.id}/",
        {"status": "inventado"},
        format="json",
    )
    assert invalid.status_code == 400

    assert client.delete(f"{_root(farm_id)}/galpones/{house.id}/").status_code == 204
    assert client.get(f"{_root(farm_id)}/galpones/").json() == []


@pytest.mark.django_db
def test_cerrar_y_reabrir_lote(login, user):
    """El cierre guarda el resumen y la reapertura devuelve el lote a abierto."""
    farm_id = _farm(user)
    house = _house(user, farm_id)
    lot = _lot(user, farm_id, house)
    client = login(user)

    closed = client.post(
        f"{_root(farm_id)}/lotes/{lot.id}/cerrar/",
        {"note": "ciclo terminado"},
        format="json",
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "cerrado"
    assert closed.json()["close_summary"]["nota_cierre"] == "ciclo terminado"

    again = client.post(f"{_root(farm_id)}/lotes/{lot.id}/cerrar/", {}, format="json")
    assert again.status_code == 400

    reopened = client.post(f"{_root(farm_id)}/lotes/{lot.id}/reabrir/", {}, format="json")
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "abierto"

    twice = client.post(f"{_root(farm_id)}/lotes/{lot.id}/reabrir/", {}, format="json")
    assert twice.status_code == 400


@pytest.mark.django_db
def test_evento_de_lote_cerrado_se_rechaza(login, user):
    """Con el lote cerrado no se aceptan registros nuevos."""
    farm_id = _farm(user)
    house = _house(user, farm_id)
    lot = _lot(user, farm_id, house)
    lot_services.close_lot(user=user, farm_id=farm_id, lot_id=lot.id)

    response = login(user).post(
        f"{_root(farm_id)}/lotes/{lot.id}/eventos/",
        {
            "kind": "ronda",
            "occurred_at": timezone.now().isoformat(),
            "responsible": "Ana Pérez",
        },
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_evento_se_registra_corrige_y_elimina(login, user):
    """La bitácora admite alta, corrección con motivo y borrado."""
    farm_id = _farm(user)
    house = _house(user, farm_id)
    lot = _lot(user, farm_id, house)
    client = login(user)

    created = client.post(
        f"{_root(farm_id)}/lotes/{lot.id}/eventos/",
        {
            "kind": "ronda",
            "occurred_at": timezone.now().isoformat(),
            "responsible": "Ana Pérez",
            "notes": "Ronda de la mañana.",
        },
        format="json",
    )
    assert created.status_code == 201
    event_id = created.json()["id"]

    corrected = client.post(
        f"{_root(farm_id)}/eventos/{event_id}/corregir/",
        {"reason": "Se anotó en el lote equivocado", "observed_value": "3"},
        format="json",
    )
    assert corrected.status_code == 200
    assert corrected.json()["observed_value"] == "3"
    assert corrected.json()["corrections"][0]["reason"] == "Se anotó en el lote equivocado"

    assert client.delete(f"{_root(farm_id)}/eventos/{event_id}/").status_code == 204
    assert client.post(f"{_root(farm_id)}/eventos/{event_id}/corregir/", {}).status_code == 400


@pytest.mark.django_db
def test_evento_valida_tipo_y_cantidad(login, user):
    """Un tipo desconocido o una mortalidad sin aves responden 400."""
    farm_id = _farm(user)
    house = _house(user, farm_id)
    lot = _lot(user, farm_id, house)
    client = login(user)
    url = f"{_root(farm_id)}/lotes/{lot.id}/eventos/"

    bad_kind = client.post(
        url,
        {
            "kind": "inventado",
            "occurred_at": timezone.now().isoformat(),
            "responsible": "Ana Pérez",
        },
        format="json",
    )
    assert bad_kind.status_code == 400

    no_birds = client.post(
        url,
        {
            "kind": "mortalidad",
            "occurred_at": timezone.now().isoformat(),
            "responsible": "Ana Pérez",
        },
        format="json",
    )
    assert no_birds.status_code == 400


@pytest.mark.django_db
def test_mortalidad_genera_aviso_y_se_cierra(login, user):
    """Al llegar al umbral del galpón aparece la alerta y se puede cerrar."""
    farm_id = _farm(user)
    house = _house(user, farm_id, mortality_alert=5)
    lot = _lot(user, farm_id, house)
    client = login(user)

    registered = client.post(
        f"{_root(farm_id)}/lotes/{lot.id}/eventos/",
        {
            "kind": "mortalidad",
            "occurred_at": timezone.now().isoformat(),
            "responsible": "Ana Pérez",
            "observed_value": "5",
        },
        format="json",
    )
    assert registered.status_code == 201
    assert registered.json()["observed_value"] == "5"

    alerts = client.get(f"{_root(farm_id)}/alertas/")
    assert alerts.status_code == 200
    assert len(alerts.json()) == 1
    alert_id = alerts.json()[0]["id"]

    closed = client.post(
        f"{_root(farm_id)}/alertas/{alert_id}/cerrar/",
        {"follow_up": "Se revisó el agua y se llamó al veterinario."},
        format="json",
    )
    assert closed.status_code == 200
    assert closed.json()["state"] == "cerrada"
    assert closed.json()["follow_up"].startswith("Se revisó")

    assert client.post(f"{_root(farm_id)}/alertas/999999/cerrar/", {}).status_code == 404


@pytest.mark.django_db
def test_otra_cuenta_no_ve_el_galpon(login, make_user, user):
    """Quien no pertenece a la finca recibe 404 en los listados."""
    farm_id = _farm(user)
    outsider = make_user(identification="87654321")
    response = login(outsider).get(f"{_root(farm_id)}/galpones/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_lote_acepta_fechas_de_etapa(login, user):
    """Las fechas de levante y engorde se guardan en la ficha del lote."""
    farm_id = _farm(user)
    house = _house(user, farm_id)
    client = login(user)
    arrived = timezone.now()
    levante = date.today() + timedelta(days=20)
    engorde = date.today() + timedelta(days=40)

    created = client.post(
        f"{_root(farm_id)}/lotes/",
        {
            "house_id": house.id,
            "code": "L-200",
            "arrived_at": arrived.isoformat(),
            "received": 120,
            "receiver_name": "Ana Pérez",
            "levante_on": levante.isoformat(),
            "engorde_on": engorde.isoformat(),
        },
        format="json",
    )
    assert created.status_code == 201
    body = created.json()
    assert body["levante_on"] == levante.isoformat()
    assert body["engorde_on"] == engorde.isoformat()
    assert body["live_count"] == 120

    filtered = client.get(f"{_root(farm_id)}/lotes/?house={house.id}")
    assert filtered.status_code == 200
    assert len(filtered.json()) == 1

    deleted = client.delete(f"{_root(farm_id)}/lotes/{body['id']}/")
    assert deleted.status_code == 204
    assert client.get(f"{_root(farm_id)}/lotes/").json() == []


@pytest.mark.django_db
def test_bitacora_filtra_por_tipo_invalido(login, user):
    """Un filtro de tipo que no existe responde 400."""
    farm_id = _farm(user)
    house = _house(user, farm_id)
    lot = _lot(user, farm_id, house)
    event_services.register_event(
        user=user,
        farm_id=farm_id,
        lot_id=lot.id,
        kind="ronda",
        occurred_at=timezone.now(),
        responsible="Ana Pérez",
    )
    response = login(user).get(f"{_root(farm_id)}/lotes/{lot.id}/eventos/?kind=inventado")
    assert response.status_code == 400
