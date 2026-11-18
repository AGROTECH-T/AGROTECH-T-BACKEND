# ruff: noqa: S101, S105, S106
"""Pruebas de la finca demo K11_Example_SENA.

Propósito: cubrir cuentas, roles, lote e inventario sembrados al arranque.
Contexto: el invitado no tiene membresía ni permisos.
@author Cristian Deysdayr Jimenez
"""
from django.core.management import call_command

from apps.accounts.models import User
from apps.accounts.seeders import (
    DEMO_ADMIN_ID,
    DEMO_CONSULTA_ID,
    DEMO_INVITADO_ID,
    DEMO_OPERADOR_ID,
    USUARIOS_INICIALES,
)
from apps.accounts.super_admin_seed import seed_usuarios_iniciales
from apps.farms.capabilities import farm_capabilities
from apps.farms.models import Farm, FarmMembership, FarmRole
from apps.farms.seed_demo_farm import (
    DEMO_FARM_NAME,
    DEMO_LOT_CODE,
    DEMO_PRODUCTS,
    seed_demo_farm,
)
from apps.inventario.models import Product
from apps.production.avicultura.models import House, Lot, LotStatus


def test_siembra_cuentas_roles_finca_lote_e_inventario(db):
    """El arranque deja las cuatro cuentas, la finca y cinco insumos."""
    seed_usuarios_iniciales()

    assert User.objects.filter(
        identification__in=[u["identification"] for u in USUARIOS_INICIALES]
    ).count() == len(USUARIOS_INICIALES)

    farm = Farm.objects.get(name=DEMO_FARM_NAME)
    admin = User.objects.get(identification=DEMO_ADMIN_ID)
    operador = User.objects.get(identification=DEMO_OPERADOR_ID)
    invitado = User.objects.get(identification=DEMO_INVITADO_ID)

    assert admin.is_administrador is True
    assert operador.is_administrador is False
    assert User.objects.filter(identification=DEMO_CONSULTA_ID).exists()
    assert FarmMembership.objects.filter(farm=farm, user=invitado).exists() is False

    roles = {
        m.user.identification: m.role
        for m in FarmMembership.objects.filter(farm=farm).select_related("user")
    }
    assert roles[DEMO_ADMIN_ID] == FarmRole.ADMINISTRADOR
    assert roles[DEMO_OPERADOR_ID] == FarmRole.OPERADOR
    assert roles[DEMO_CONSULTA_ID] == FarmRole.CONSULTA

    assert House.objects.filter(farm=farm).count() == 1
    lot = Lot.objects.get(farm=farm, code=DEMO_LOT_CODE)
    assert lot.status == LotStatus.ABIERTO
    assert lot.live_count == 7980

    products = Product.objects.filter(farm=farm, is_removed=False)
    assert products.count() == len(DEMO_PRODUCTS)
    assert set(products.values_list("barcode", flat=True)) == {
        item["barcode"] for item in DEMO_PRODUCTS
    }


def test_invitado_sin_capacidades_y_consulta_solo_lectura(db):
    """Consulta lee; operador escribe inventario; invitado no tiene finca."""
    seed_usuarios_iniciales()
    farm = Farm.objects.get(name=DEMO_FARM_NAME)

    admin_m = FarmMembership.objects.get(farm=farm, user__identification=DEMO_ADMIN_ID)
    op_m = FarmMembership.objects.get(farm=farm, user__identification=DEMO_OPERADOR_ID)
    cons_m = FarmMembership.objects.get(farm=farm, user__identification=DEMO_CONSULTA_ID)

    admin_caps = farm_capabilities(admin_m)
    op_caps = farm_capabilities(op_m)
    cons_caps = farm_capabilities(cons_m)

    assert admin_caps["can_manage_members"] is True
    assert admin_caps["can_manage_iot"] is True
    assert op_caps["can_write_inventory"] is True
    assert op_caps["can_manage_iot"] is False
    assert cons_caps["can_read_inventory"] is True
    assert cons_caps["can_write_inventory"] is False
    assert cons_caps["can_manage_members"] is False

    invitado = User.objects.get(identification=DEMO_INVITADO_ID)
    assert FarmMembership.objects.filter(user=invitado).count() == 0


def test_segunda_corrida_no_duplica_demo(db):
    """Volver a sembrar no crea otra finca, lote ni productos."""
    seed_usuarios_iniciales()
    seed_demo_farm()
    seed_demo_farm()

    assert Farm.objects.filter(name=DEMO_FARM_NAME).count() == 1
    farm = Farm.objects.get(name=DEMO_FARM_NAME)
    assert Lot.objects.filter(farm=farm, code=DEMO_LOT_CODE).count() == 1
    assert Product.objects.filter(farm=farm).count() == len(DEMO_PRODUCTS)
    assert FarmMembership.objects.filter(farm=farm).count() == 3


def test_comando_seed_usuarios_incluye_finca_demo(db):
    """El comando de arranque deja la finca demo lista."""
    call_command("seed_usuarios")
    assert Farm.objects.filter(name=DEMO_FARM_NAME).exists()
