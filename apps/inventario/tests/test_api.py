# ruff: noqa: S101
"""Contrato HTTP del inventario.

Propósito: cubrir las vistas de productos, movimientos, préstamos, reportes
y catálogo, que hasta ahora sólo tenían pruebas de servicio.
@author Cristian Deysdayr Jimenez
"""
from datetime import date
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.farms.services import create_farm
from apps.inventario import services
from apps.inventario.models import MovementKind
from apps.inventario.seed_catalog import seed_catalog


@pytest.fixture(autouse=True)
def _catalog(db):
    seed_catalog()


@pytest.fixture
def farm(user) -> int:
    """Finca donde el usuario es administrador."""
    return create_farm(user=user, name="La Esperanza").farm_id


@pytest.fixture
def product(user, farm):
    """Insumo con stock mínimo para las pruebas de lista."""
    return services.create_product(
        user=user,
        farm_id=farm,
        name="Semilla de maíz",
        unit_code="kg",
        category_code="semilla",
        min_stock=Decimal("5"),
    )


def _root(farm_id: int) -> str:
    return f"/api/v1/inventory/{farm_id}"


def _entrada(client, farm_id: int, product_id: int, quantity: str = "10"):
    """Carga stock para poder sacar o prestar."""
    return client.post(
        f"{_root(farm_id)}/products/{product_id}/movements/",
        {"kind": MovementKind.ENTRADA, "quantity": quantity, "note": "carga"},
        format="json",
    )


@pytest.mark.django_db
def test_lista_productos_paginada(login, user, farm, product):
    """El listado llega con el sobre de paginación."""
    client = login(user)
    response = client.get(f"{_root(farm)}/products/?status=all&page=1&page_size=1")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 1
    assert body["results"][0]["id"] == product.id


@pytest.mark.django_db
def test_lista_filtra_por_busqueda_y_bajo_stock(login, user, farm, product):
    """La búsqueda y el filtro de stock mínimo se aplican en la consulta."""
    client = login(user)
    found = client.get(f"{_root(farm)}/products/?status=all&q=ma%C3%ADz")
    assert found.json()["count"] == 1
    empty = client.get(f"{_root(farm)}/products/?status=all&q=no-existe")
    assert empty.json()["count"] == 0
    low = client.get(f"{_root(farm)}/products/?status=all&low_stock=1")
    assert low.json()["count"] == 1


@pytest.mark.django_db
def test_crear_producto_por_http(login, user, farm):
    """El alta responde 201 y una unidad inválida responde 400."""
    client = login(user)
    created = client.post(
        f"{_root(farm)}/products/",
        {
            "name": "Cal viva",
            "unit": "kg",
            "category": "insumo_general",
            "min_stock": "2",
        },
        format="json",
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Cal viva"

    rejected = client.post(
        f"{_root(farm)}/products/",
        {
            "name": "Otro",
            "unit": "unidad_rara",
            "category": "insumo_general",
            "min_stock": "0",
        },
        format="json",
    )
    assert rejected.status_code == 400


@pytest.mark.django_db
def test_detalle_edita_y_desactiva(login, user, farm, product):
    """El detalle se consulta, se edita y se desactiva sin borrar historial."""
    client = login(user)
    detail = client.get(f"{_root(farm)}/products/{product.id}/")
    assert detail.status_code == 200
    assert detail.json()["name"] == "Semilla de maíz"

    renamed = client.patch(
        f"{_root(farm)}/products/{product.id}/",
        {"name": "Semilla mejorada"},
        format="json",
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Semilla mejorada"

    inactive = client.patch(
        f"{_root(farm)}/products/{product.id}/",
        {"is_active": False},
        format="json",
    )
    assert inactive.status_code == 200
    assert inactive.json()["is_active"] is False


@pytest.mark.django_db
def test_borrar_producto(login, user, farm, product):
    """La baja responde 204 y luego el detalle ya no existe."""
    client = login(user)
    deleted = client.delete(f"{_root(farm)}/products/{product.id}/")
    assert deleted.status_code == 204
    assert client.get(f"{_root(farm)}/products/{product.id}/").status_code == 404


@pytest.mark.django_db
def test_foto_se_sube_se_ve_y_se_quita(login, user, farm, product, settings, tmp_path):
    """La foto del insumo recorre subida, descarga y borrado."""
    settings.MEDIA_ROOT = tmp_path
    client = login(user)
    url = f"{_root(farm)}/products/{product.id}/image/"

    assert client.post(url, {}, format="multipart").status_code == 400

    wrong = SimpleUploadedFile("nota.txt", b"hola", content_type="text/plain")
    assert client.post(url, {"image": wrong}, format="multipart").status_code == 400

    image = SimpleUploadedFile("foto.jpg", b"\xff\xd8\xff\xe0foto", content_type="image/jpeg")
    uploaded = client.post(url, {"image": image}, format="multipart")
    assert uploaded.status_code == 200
    assert uploaded.json()["has_image"] is True

    shown = client.get(url)
    assert shown.status_code == 200
    assert b"".join(shown.streaming_content) == b"\xff\xd8\xff\xe0foto"

    removed = client.delete(url)
    assert removed.status_code == 200
    assert removed.json()["has_image"] is False
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_inventario_en_excel(login, user, farm, product):
    """La descarga del inventario arma un libro y filtra por ids."""
    client = login(user)
    response = client.get(f"{_root(farm)}/products/inventory.xlsx")
    assert response.status_code == 200
    assert response.content[:2] == b"PK"

    filtered = client.get(f"{_root(farm)}/products/inventory.xlsx?ids={product.id}")
    assert filtered.status_code == 200

    invalid = client.get(f"{_root(farm)}/products/inventory.xlsx?ids=abc")
    assert invalid.status_code == 400


@pytest.mark.django_db
def test_plantilla_vacia_sin_productos(login, user, farm, product):
    """La plantilla solo trae encabezados, aunque la finca ya tenga productos."""
    from apps.inventario.services.xlsx_reader import read_rows

    client = login(user)
    response = client.get(f"{_root(farm)}/products/template.xlsx")
    assert response.status_code == 200
    assert response.content[:2] == b"PK"
    rows = read_rows(response.content)
    assert len(rows) == 1
    assert rows[0] == [
        "Producto",
        "Código",
        "Categoría",
        "Unidad",
        "Stock actual",
        "Equivalencia",
        "Stock mínimo",
    ]
    assert all(product.name not in cell for cell in rows[0])


@pytest.mark.django_db
def test_movimientos_entrada_salida_e_historial(login, user, farm, product):
    """Se registra entrada y salida, y el historial se filtra por tipo y fecha."""
    client = login(user)
    assert _entrada(client, farm, product.id).status_code == 201

    salida = client.post(
        f"{_root(farm)}/products/{product.id}/movements/",
        {"kind": MovementKind.SALIDA, "quantity": "4"},
        format="json",
    )
    assert salida.status_code == 201
    assert salida.json()["stock_after"] == "6.000"

    listed = client.get(f"{_root(farm)}/products/{product.id}/movements/?page=1")
    assert listed.status_code == 200
    assert listed.json()["count"] == 2

    only_in = client.get(f"{_root(farm)}/movements/?kind=entrada")
    assert only_in.json()["count"] == 1

    today = date.today().isoformat()
    ranged = client.get(f"{_root(farm)}/movements/?from={today}&to={today}")
    assert ranged.json()["count"] == 2

    bad_date = client.get(f"{_root(farm)}/movements/?from=ayer")
    assert bad_date.status_code == 400


@pytest.mark.django_db
def test_historial_en_excel_de_producto_y_finca(login, user, farm, product):
    """Los dos historiales se descargan en Excel."""
    client = login(user)
    _entrada(client, farm, product.id)

    from_product = client.get(f"{_root(farm)}/products/{product.id}/history.xlsx")
    assert from_product.status_code == 200
    assert from_product.content[:2] == b"PK"

    from_farm = client.get(f"{_root(farm)}/movements/history.xlsx?kind=entrada")
    assert from_farm.status_code == 200
    assert from_farm.content[:2] == b"PK"


@pytest.mark.django_db
def test_prestamo_crear_listar_devolver_y_descargar(login, user, farm, product):
    """El préstamo descuenta stock, aparece en la lista y se devuelve."""
    client = login(user)
    _entrada(client, farm, product.id)
    url = f"{_root(farm)}/products/{product.id}/loans/"

    created = client.post(
        url,
        {"borrower_name": "Ana Pérez", "document_number": "10203040", "quantity": "3"},
        format="json",
    )
    assert created.status_code == 201
    loan_id = created.json()["loan"]["id"]

    listed = client.get(url)
    assert listed.status_code == 200
    assert listed.json()["count"] == 1

    returned = client.post(
        f"{url}{loan_id}/return/",
        {"quantity": "3", "note": "devuelto completo"},
        format="json",
    )
    assert returned.status_code == 200

    history = client.get(f"{url}history.xlsx")
    assert history.status_code == 200
    assert history.content[:2] == b"PK"


@pytest.mark.django_db
def test_reportes_en_json(login, user, farm, product):
    """Los cuatro reportes responden 200 y validan el producto pedido."""
    client = login(user)
    _entrada(client, farm, product.id)
    client.post(
        f"{_root(farm)}/products/{product.id}/movements/",
        {"kind": MovementKind.SALIDA, "quantity": "2"},
        format="json",
    )
    today = date.today().isoformat()

    adjustments = client.get(
        f"{_root(farm)}/reports/adjustments/?from={today}&to={today}&reason=conteo_fisico"
    )
    assert adjustments.status_code == 200
    assert "rows" in adjustments.json()

    consumption = client.get(f"{_root(farm)}/reports/consumption/?from={today}&to={today}")
    assert consumption.status_code == 200
    assert consumption.json()["rows"][0]["product"] == "Semilla de maíz"

    loans = client.get(f"{_root(farm)}/reports/loans/?status=all")
    assert loans.status_code == 200
    assert "people" in loans.json()

    order = client.get(f"{_root(farm)}/reports/order/?low=si&target=min")
    assert order.status_code == 200
    assert "to_order" in order.json()

    invalid = client.get(f"{_root(farm)}/reports/consumption/?product=abc")
    assert invalid.status_code == 400


@pytest.mark.django_db
def test_reportes_en_excel(login, user, farm, product):
    """Los cuatro reportes se descargan en Excel."""
    client = login(user)
    _entrada(client, farm, product.id)
    for name in ("adjustments", "consumption", "loans", "order"):
        response = client.get(f"{_root(farm)}/reports/{name}.xlsx")
        assert response.status_code == 200, name
        assert response.content[:2] == b"PK", name


@pytest.mark.django_db
def test_catalogo_disponible(login, user):
    """El catálogo fijo se sirve para armar los formularios."""
    response = login(user).get("/api/v1/inventory/catalog/")
    assert response.status_code == 200
    body = response.json()
    assert body["categories"]
    assert body["units"]


@pytest.mark.django_db
def test_categoria_se_crea_renombra_y_borra(login, user, farm):
    """El ciclo completo de una categoría propia de la finca."""
    client = login(user)
    created = client.post(f"{_root(farm)}/categories/", {"name": "Prueba API"}, format="json")
    assert created.status_code == 201
    code = created.json()["created"]

    renamed = client.patch(
        f"{_root(farm)}/categories/{code}/",
        {"name": "Prueba API 2"},
        format="json",
    )
    assert renamed.status_code == 200

    assert client.delete(f"{_root(farm)}/categories/{code}/").status_code == 204


@pytest.mark.django_db
def test_medida_base_se_crea_edita_y_borra(login, user, farm):
    """Una medida que no viene en el sistema se administra por HTTP."""
    client = login(user)
    created = client.post(
        f"{_root(farm)}/base-units/",
        {"name": "Arroba", "symbol": "@", "decimals": True},
        format="json",
    )
    assert created.status_code == 201
    code = created.json()["created"]

    edited = client.patch(
        f"{_root(farm)}/base-units/{code}/",
        {"name": "Arroba corta", "symbol": "@", "decimals": True},
        format="json",
    )
    assert edited.status_code == 200

    assert client.delete(f"{_root(farm)}/base-units/{code}/").status_code == 204


@pytest.mark.django_db
def test_presentacion_custom_se_crea_edita_y_borra(login, user, farm):
    """Una presentación comercial propia se administra por HTTP."""
    client = login(user)
    created = client.post(
        f"{_root(farm)}/units/",
        {
            "stem": "bulto",
            "label": "Bulto (7 kg)",
            "base_unit": "kg",
            "factor": "7",
            "categories": ["insumo_general"],
        },
        format="json",
    )
    assert created.status_code == 201
    code = created.json()["id"]

    edited = client.patch(
        f"{_root(farm)}/units/{code}/",
        {"label": "Bulto x7", "categories": ["insumo_general"]},
        format="json",
    )
    assert edited.status_code == 200

    assert client.delete(f"{_root(farm)}/units/{code}/").status_code == 204


@pytest.mark.django_db
def test_inventario_exige_sesion(client, user, farm):
    """Sin token no se ve el inventario de la finca."""
    assert client.get(f"{_root(farm)}/products/?status=all").status_code == 401
