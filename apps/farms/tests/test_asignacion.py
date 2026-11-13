# ruff: noqa: S101
"""Prueba la asignación de roles desde Configuración.

"""
import pytest
from apps.farms.models import Farm, FarmMembership, FarmRole
LISTA = "/api/v1/configuracion/usuarios/"
def _rol(user_id: int) -> str:
    return f"/api/v1/configuracion/usuarios/{user_id}/rol/"
def _finca(name: str) -> Farm:
    return Farm.objects.create(name=name)
def _miembro(farm: Farm, user, role: str) -> FarmMembership:
    return FarmMembership.objects.create(farm=farm, user=user, role=role)
@pytest.mark.django_db
def test_lista_exige_sesion(client):
    """Sin token no se ven las personas."""
    assert client.get(LISTA).status_code == 401
@pytest.mark.django_db
def test_administrador_ve_solo_sus_fincas(make_user, login):
    """El listado incluye las fincas administradas y omite las ajenas."""
    admin = make_user("10000001", first_name="Ada", last_name="Admin")
    otro = make_user("10000002", first_name="Omar", last_name="Operador")
    ajeno = make_user("10000003", first_name="Ana", last_name="Ajena")
    propia = _finca("La propia")
    extra = _finca("La ajena")
    _miembro(propia, admin, FarmRole.ADMINISTRADOR)
    _miembro(propia, otro, FarmRole.OPERADOR)
    _miembro(extra, ajeno, FarmRole.ADMINISTRADOR)

    data = login(admin).get(LISTA).json()
    nombres = [persona["nombre"] for persona in data["usuarios"]]

    assert data["puedo_asignar"] is True
    assert nombres == ["Ada Admin", "Omar Operador"]
    assert data["usuarios"][0]["es_yo"] is True
@pytest.mark.django_db
def test_operador_no_asigna(make_user, login):
    """Quien no administra ninguna finca no recibe personas."""
    operador = make_user("10000011", first_name="Olga", last_name="Campo")
    _miembro(_finca("Huerta"), operador, FarmRole.OPERADOR)

    data = login(operador).get(LISTA).json()

    assert data == {"puedo_asignar": False, "fincas": [], "usuarios": []}
@pytest.mark.django_db
def test_administrador_cambia_el_rol(make_user, login):
    """El administrador reemplaza el rol de otra persona en su finca."""
    admin = make_user("10000021", first_name="Sara", last_name="Admin")
    persona = make_user("10000022", first_name="Luis", last_name="Campo")
    finca = _finca("El roble")
    _miembro(finca, admin, FarmRole.ADMINISTRADOR)
    _miembro(finca, persona, FarmRole.OPERADOR)

    response = login(admin).put(
        _rol(persona.id),
        {"farm_id": finca.id, "role": FarmRole.CONSULTA},
        format="json",
    )

    assert response.status_code == 200
    assert response.json()["membresias"][0]["role"] == FarmRole.CONSULTA
@pytest.mark.django_db
def test_no_cambia_su_propio_rol(make_user, login):
    """La sesión no puede modificarse a sí misma."""
    admin = make_user("10000031", first_name="Nora", last_name="Admin")
    apoyo = make_user("10000032", first_name="Ivo", last_name="Admin")
    finca = _finca("El lago")
    _miembro(finca, admin, FarmRole.ADMINISTRADOR)
    _miembro(finca, apoyo, FarmRole.ADMINISTRADOR)

    response = login(admin).put(
        _rol(admin.id),
        {"farm_id": finca.id, "role": FarmRole.CONSULTA},
        format="json",
    )

    assert response.status_code == 403
    assert response.json()["error"] == "no puedes cambiar tu propio rol"
@pytest.mark.django_db
def test_conserva_un_administrador(make_user, login):
    """Cambiar a otro administrador deja la finca con uno activo."""
    admin = make_user("10000041", first_name="Eva", last_name="Admin")
    apoyo = make_user("10000042", first_name="Iván", last_name="Admin")
    finca = _finca("La loma")
    _miembro(finca, admin, FarmRole.ADMINISTRADOR)
    _miembro(finca, apoyo, FarmRole.ADMINISTRADOR)

    response = login(admin).put(
        _rol(apoyo.id),
        {"farm_id": finca.id, "role": FarmRole.OPERADOR},
        format="json",
    )

    assert response.status_code == 200
    assert (
        FarmMembership.objects.filter(
            farm=finca,
            role=FarmRole.ADMINISTRADOR,
        ).count()
        == 1
    )
@pytest.mark.django_db
def test_no_cambia_una_finca_ajena(make_user, login):
    """Administrar una finca no autoriza a cambiar otra."""
    admin = make_user("10000051", first_name="Leo", last_name="Admin")
    ajeno = make_user("10000052", first_name="Paz", last_name="Ajena")
    propia = _finca("Norte")
    extra = _finca("Sur")
    _miembro(propia, admin, FarmRole.ADMINISTRADOR)
    _miembro(extra, ajeno, FarmRole.OPERADOR)

    response = login(admin).put(
        _rol(ajeno.id),
        {"farm_id": extra.id, "role": FarmRole.CONSULTA},
        format="json",
    )

    assert response.status_code == 404
@pytest.mark.django_db
def test_ve_el_detalle_de_una_persona(make_user, login):
    """La ficha trae el rol y si la cuenta está activa."""
    admin = make_user("11000021", first_name="Ada", last_name="Admin")
    persona = make_user("11000022", first_name="Luis", last_name="Campo")
    finca = _finca("El roble")
    _miembro(finca, admin, FarmRole.ADMINISTRADOR)
    _miembro(finca, persona, FarmRole.CONSULTA)

    response = login(admin).get(f"/api/v1/configuracion/usuarios/{persona.id}/")

    assert response.status_code == 200
    assert response.json()["activo"] is True
    assert response.json()["membresias"][0]["role"] == FarmRole.CONSULTA
