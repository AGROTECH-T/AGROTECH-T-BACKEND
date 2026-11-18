"""Siembra la finca demo K11_Example_SENA.

Propósito: dejar al arranque una finca de ejemplo con roles, un galpón, un
lote abierto e inventario con insumos reales de avicultura.
Contexto: se ejecuta después de ``seed_usuarios``. El invitado no recibe
membresía ni permisos. Idempotente: no duplica finca, lote ni productos.
@author Cristian Deysdayr Jimenez
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.seeders import (
    DEMO_ADMIN_ID,
    DEMO_CONSULTA_ID,
    DEMO_OPERADOR_ID,
)
from apps.farms.models import Farm, FarmMembership, FarmRole, MembershipStatus
from apps.inventario.models import InventoryCategory, InventoryUnit, Product
from apps.inventario.seed_catalog import seed_catalog
from apps.production.avicultura.models import (
    FeedMode,
    House,
    HouseStatus,
    Lot,
    LotStage,
    LotStatus,
)

DEMO_FARM_NAME = "K11_Example_SENA"
DEMO_HOUSE_NAME = "Galpon 1"
DEMO_LOT_CODE = "LOT-K11-001"

# Insumos típicos de engorde en Colombia (cantidades en la unidad del producto).
DEMO_PRODUCTS = (
    {
        "barcode": "K11-ALM-INI-40",
        "name": "Concentrado iniciador pollito 40 kg",
        "category_code": "alimento_concentrado",
        "unit_code": "bulto_40kg",
        "min_stock": Decimal("10"),
        "quantity": Decimal("48"),
    },
    {
        "barcode": "K11-ALM-ENG-40",
        "name": "Concentrado engorde pollo 40 kg",
        "category_code": "alimento_concentrado",
        "unit_code": "bulto_40kg",
        "min_stock": Decimal("15"),
        "quantity": Decimal("72"),
    },
    {
        "barcode": "K11-MED-ENRO-500",
        "name": "Enrofloxacina 10% frasco 500 mL",
        "category_code": "medicamento_veterinario",
        "unit_code": "frasco_500ml",
        "min_stock": Decimal("4"),
        "quantity": Decimal("12"),
    },
    {
        "barcode": "K11-LIQ-YOD-20",
        "name": "Desinfectante yodado caneca 20 L",
        "category_code": "agua_riego_liquidos",
        "unit_code": "caneca_20l",
        "min_stock": Decimal("2"),
        "quantity": Decimal("6"),
    },
    {
        "barcode": "K11-INS-CAS-KG",
        "name": "Cascarilla de arroz para cama",
        "category_code": "insumo_general",
        "unit_code": "kg",
        "min_stock": Decimal("200"),
        "quantity": Decimal("850"),
    },
)


def seed_demo_farm() -> Farm:
    """Crea o actualiza la finca demo con membresías, galpón, lote e inventario.

    @returns Finca ``K11_Example_SENA``.
    """
    seed_catalog()
    admin = _require_user(DEMO_ADMIN_ID)
    operador = _require_user(DEMO_OPERADOR_ID)
    consulta = _require_user(DEMO_CONSULTA_ID)

    with transaction.atomic():
        farm = _ensure_farm()
        _ensure_membership(farm, admin, FarmRole.ADMINISTRADOR)
        _ensure_membership(farm, operador, FarmRole.OPERADOR)
        _ensure_membership(farm, consulta, FarmRole.CONSULTA)
        house = _ensure_house(farm)
        _ensure_lot(farm=farm, house=house, created_by=admin)
        _ensure_products(farm)
    return farm


def _require_user(identification: str) -> User:
    """Exige la cuenta sembrada; sin ella la demo no puede armarse."""
    user = User.objects.filter(identification=identification).first()
    if user is None:
        raise ValueError(f"falta la cuenta sembrada {identification}")
    if not user.email_verified:
        user.email_verified = True
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified", "email_verified_at"])
    return user


def _ensure_farm() -> Farm:
    """Una sola finca con el nombre fijo de la demo."""
    farm = Farm.objects.filter(name=DEMO_FARM_NAME).order_by("id").first()
    if farm is not None:
        if not farm.is_active:
            farm.is_active = True
            farm.deactivated_at = None
            farm.deactivated_by = None
            farm.save(
                update_fields=[
                    "is_active",
                    "deactivated_at",
                    "deactivated_by",
                    "updated_at",
                ]
            )
        return farm
    return Farm.objects.create(name=DEMO_FARM_NAME)


def _ensure_membership(farm: Farm, user: User, role: str) -> FarmMembership:
    """Asocia la cuenta a la finca con el rol pedido."""
    membership, created = FarmMembership.objects.get_or_create(
        farm=farm,
        user=user,
        defaults={"role": role, "status": MembershipStatus.ACTIVE},
    )
    if created:
        return membership
    membership.role = role
    membership.status = MembershipStatus.ACTIVE
    membership.revoked_at = None
    membership.revoked_by = None
    membership.left_at = None
    membership.save(
        update_fields=[
            "role",
            "status",
            "revoked_at",
            "revoked_by",
            "left_at",
            "updated_at",
        ]
    )
    return membership


def _ensure_house(farm: Farm) -> House:
    """Galpón único de la demo con medidas habituales de engorde."""
    house, created = House.objects.get_or_create(
        farm=farm,
        name=DEMO_HOUSE_NAME,
        defaults={
            "location": "Sector A — módulo SENA K11",
            "length_m": Decimal("40.00"),
            "width_m": Decimal("12.00"),
            "capacity": 8500,
            "usual_birds": 8000,
            "equipment": "Comederos automáticos, bebederos de niple, cortinas laterales",
            "status": HouseStatus.DISPONIBLE,
            "bale_kg": Decimal("40.00"),
            "bales_per_drum": Decimal("3.00"),
            "feed_mode": FeedMode.MEDIDO,
            "cycle_days": 42,
            "mortality_alert": 25,
        },
    )
    if created:
        return house
    house.location = "Sector A — módulo SENA K11"
    house.length_m = Decimal("40.00")
    house.width_m = Decimal("12.00")
    house.capacity = 8500
    house.usual_birds = 8000
    house.equipment = "Comederos automáticos, bebederos de niple, cortinas laterales"
    house.bale_kg = Decimal("40.00")
    house.bales_per_drum = Decimal("3.00")
    house.feed_mode = FeedMode.MEDIDO
    house.cycle_days = 42
    house.mortality_alert = 25
    house.save()
    return house


def _ensure_lot(*, farm: Farm, house: House, created_by: User) -> Lot:
    """Un lote abierto de engorde; si ya existe, no lo recrea."""
    existing = Lot.objects.filter(farm=farm, code=DEMO_LOT_CODE).first()
    if existing is not None:
        if house.status != HouseStatus.OCUPADO and existing.status == LotStatus.ABIERTO:
            house.status = HouseStatus.OCUPADO
            house.save(update_fields=["status", "updated_at"])
        return existing

    arrived = timezone.now()
    lot = Lot.objects.create(
        farm=farm,
        house=house,
        code=DEMO_LOT_CODE,
        arrived_at=arrived,
        received=8000,
        dead_on_arrival=12,
        discarded_on_arrival=8,
        live_count=7980,
        supplier="Incubadora del Valle S.A.S.",
        documents="Guía sanitaria GS-K11-001 / factura FV-45821",
        initial_age_days=1,
        initial_weight=Decimal("0.042"),
        stage=LotStage.INICIO,
        feed_type="Concentrado iniciador pollito",
        reception_notes="Lote Cobb 500, recepción en horario diurno, cama seca.",
        receiver_name="Admin Sena",
        status=LotStatus.ABIERTO,
        created_by=created_by,
    )
    house.status = HouseStatus.OCUPADO
    house.save(update_fields=["status", "updated_at"])
    return lot


def _ensure_products(farm: Farm) -> list[Product]:
    """Cinco insumos reales; actualiza stock y mínimos si ya existían."""
    products: list[Product] = []
    for item in DEMO_PRODUCTS:
        category = InventoryCategory.objects.filter(
            code=item["category_code"], is_active=True
        ).first()
        unit = InventoryUnit.objects.filter(code=item["unit_code"], is_active=True).first()
        if category is None or unit is None:
            raise ValueError(
                f"catálogo incompleto para {item['barcode']}: "
                f"{item['category_code']}/{item['unit_code']}"
            )
        product, created = Product.objects.get_or_create(
            farm=farm,
            barcode=item["barcode"],
            defaults={
                "name": item["name"],
                "category": category,
                "unit": unit,
                "min_stock": item["min_stock"],
                "quantity": item["quantity"],
                "is_active": True,
                "is_removed": False,
            },
        )
        if not created:
            product.name = item["name"]
            product.category = category
            product.unit = unit
            product.min_stock = item["min_stock"]
            product.quantity = item["quantity"]
            product.is_active = True
            product.is_removed = False
            product.save()
        products.append(product)
    return products
