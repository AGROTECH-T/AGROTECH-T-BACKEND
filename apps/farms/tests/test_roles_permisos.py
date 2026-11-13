# ruff: noqa: S101
"""Prueba el catálogo de roles y permisos de Configuración.

"""
import pytest

from apps.farms import roles

URL = "/api/v1/configuracion/roles-permisos/"


@pytest.mark.django_db
def test_catalogo_requiere_sesion(client):
    """Sin token la consulta queda rechazada."""
    assert client.get(URL).status_code == 401


@pytest.mark.django_db
def test_catalogo_entrega_roles_y_matriz(auth_client):
    """La sesión válida recibe roles, actores, matriz y reglas."""
    response = auth_client.get(URL)
    data = response.json()

    assert response.status_code == 200
    assert [rol["id"] for rol in data["roles"]] == [
        roles.ADMINISTRADOR,
        roles.OPERADOR,
        roles.CONSULTA,
    ]
    assert [actor["id"] for actor in data["actores"]] == ["responsable_pago", "soporte"]
    assert len(data["permisos"]) == len(roles.PERMISOS)
    assert len(data["reglas"]) == len(roles.REGLAS)
    assert len(data["pendientes"]) == len(roles.PENDIENTES)


@pytest.mark.django_db
def test_cada_permiso_cubre_los_tres_roles(auth_client):
    """Toda acción declara un estado válido para cada rol."""
    data = auth_client.get(URL).json()
    for permiso in data["permisos"]:
        assert set(permiso["por_rol"]) == {
            roles.ADMINISTRADOR,
            roles.OPERADOR,
            roles.CONSULTA,
        }
        for celda in permiso["por_rol"].values():
            assert celda["estado"] in roles.ESTADOS
            assert celda["detalle"]


@pytest.mark.django_db
def test_matriz_respeta_decisiones_clave(auth_client):
    """Los permisos sensibles coinciden con la definición funcional."""
    matriz = {fila["id"]: fila["por_rol"] for fila in auth_client.get(URL).json()["permisos"]}

    assert matriz["cambiar_roles"][roles.ADMINISTRADOR]["estado"] == roles.PERMITIDO
    assert matriz["cambiar_roles"][roles.OPERADOR]["estado"] == roles.NO_PERMITIDO
    assert matriz["ajustar_stock"][roles.ADMINISTRADOR]["estado"] == roles.PERMITIDO
    assert matriz["ajustar_stock"][roles.OPERADOR]["estado"] == roles.NO_PERMITIDO
    assert matriz["gestionar_productos"][roles.OPERADOR]["estado"] == roles.NO_PERMITIDO
    assert matriz["eliminar_finca"][roles.OPERADOR]["estado"] == roles.NO_PERMITIDO
    assert matriz["crear_finca"][roles.ADMINISTRADOR]["estado"] == roles.PERMITIDO
    assert matriz["crear_finca"][roles.OPERADOR]["estado"] == roles.NO_PERMITIDO


def test_identificadores_sin_duplicados():
    """Roles, actores y acciones mantienen identificadores únicos."""
    ids_roles = [rol["id"] for rol in roles.ROLES]
    ids_actores = [actor["id"] for actor in roles.ACTORES]
    ids_permisos = [permiso["id"] for permiso in roles.PERMISOS]

    assert len(ids_roles) == len(set(ids_roles))
    assert len(ids_actores) == len(set(ids_actores))
    assert len(ids_permisos) == len(set(ids_permisos))
    assert not set(ids_roles) & set(ids_actores)
