"""Catálogo normalizado, FKs en producto y snapshots en movimientos."""

from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


def seed_and_migrate(apps, schema_editor):
    from apps.inventario.seed_catalog import (
        LEGACY_CATEGORY,
        LEGACY_UNIT,
        seed_catalog,
    )

    seed_catalog()

    Product = apps.get_model("inventario", "Product")
    InventoryCategory = apps.get_model("inventario", "InventoryCategory")
    InventoryUnit = apps.get_model("inventario", "InventoryUnit")
    Movement = apps.get_model("inventario", "Movement")
    FarmMembership = apps.get_model("farms", "FarmMembership")

    categories = {item.code: item for item in InventoryCategory.objects.all()}
    units = {item.code: item for item in InventoryUnit.objects.all()}
    fallback_category = categories["insumo_general"]
    fallback_unit = units["u"]

    for product in Product.objects.all():
        old_category = getattr(product, "legacy_category", "") or ""
        old_unit = getattr(product, "legacy_unit", "") or ""
        category_code = LEGACY_CATEGORY.get(old_category, "insumo_general")
        unit_code = LEGACY_UNIT.get(old_unit, "u")
        product.category = categories.get(category_code, fallback_category)
        product.unit = units.get(unit_code, fallback_unit)
        product.save(update_fields=["category", "unit"])

    orphans = []
    for movement in Movement.objects.all():
        product = movement.product
        membership = FarmMembership.objects.filter(
            farm_id=product.farm_id,
            user_id=movement.created_by_id,
        ).first()
        if membership is None:
            membership = FarmMembership.objects.filter(farm_id=product.farm_id).first()
        if membership is None:
            orphans.append(movement.id)
            continue
        movement.unit = product.unit
        movement.quantity_base = Decimal(movement.quantity) * Decimal(product.unit.factor)
        movement.stock_before = Decimal("0")
        movement.stock_after = movement.quantity
        movement.membership = membership
        movement.save(
            update_fields=[
                "unit",
                "quantity_base",
                "stock_before",
                "stock_after",
                "membership",
            ]
        )
    if orphans:
        Movement.objects.filter(id__in=orphans).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_harden_sessions"),
        ("farms", "0001_initial"),
        ("inventario", "0002_catalog_choices"),
    ]

    operations = [
        migrations.CreateModel(
            name="InventoryCategory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=40, unique=True)),
                ("name", models.CharField(max_length=80)),
                ("description", models.CharField(blank=True, default="", max_length=200)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "inventory_categories",
                "ordering": ["name", "id"],
            },
        ),
        migrations.CreateModel(
            name="InventoryBaseUnit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=20, unique=True)),
                ("name", models.CharField(max_length=60)),
                ("symbol", models.CharField(max_length=10)),
                ("nature", models.CharField(choices=[("discreta", "Discreta"), ("continua", "Continua"), ("presentacion", "Presentación")], max_length=20)),
                ("decimal_places", models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                "db_table": "inventory_base_units",
                "ordering": ["code"],
            },
        ),
        migrations.CreateModel(
            name="InventoryUnit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=40, unique=True)),
                ("label", models.CharField(max_length=80)),
                ("factor", models.DecimalField(decimal_places=6, default=1, max_digits=14)),
                ("nature", models.CharField(choices=[("discreta", "Discreta"), ("continua", "Continua"), ("presentacion", "Presentación")], max_length=20)),
                ("decimal_places", models.PositiveSmallIntegerField(default=0)),
                ("is_system", models.BooleanField(default=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("base_unit", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="presentations", to="inventario.inventorybaseunit")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="inventory_units_created", to="accounts.user")),
            ],
            options={
                "db_table": "inventory_units",
                "ordering": ["label", "id"],
            },
        ),
        migrations.CreateModel(
            name="CategoryUnit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="unit_links", to="inventario.inventorycategory")),
                ("unit", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="category_links", to="inventario.inventoryunit")),
            ],
            options={
                "db_table": "inventory_category_units",
            },
        ),
        migrations.AddConstraint(
            model_name="categoryunit",
            constraint=models.UniqueConstraint(fields=("category", "unit"), name="uniq_inventory_category_unit"),
        ),
        migrations.RenameField(
            model_name="product",
            old_name="category",
            new_name="legacy_category",
        ),
        migrations.RenameField(
            model_name="product",
            old_name="unit",
            new_name="legacy_unit",
        ),
        migrations.AddField(
            model_name="product",
            name="category",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="products", to="inventario.inventorycategory"),
        ),
        migrations.AddField(
            model_name="product",
            name="unit",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="products", to="inventario.inventoryunit"),
        ),
        migrations.AddField(
            model_name="movement",
            name="unit",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="movements", to="inventario.inventoryunit"),
        ),
        migrations.AddField(
            model_name="movement",
            name="quantity_base",
            field=models.DecimalField(decimal_places=6, max_digits=16, null=True),
        ),
        migrations.AddField(
            model_name="movement",
            name="stock_before",
            field=models.DecimalField(decimal_places=3, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="movement",
            name="stock_after",
            field=models.DecimalField(decimal_places=3, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="movement",
            name="membership",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="inventory_movements", to="farms.farmmembership"),
        ),
        migrations.AddField(
            model_name="movement",
            name="idempotency_key",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RunPython(seed_and_migrate, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="product",
            name="category",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="products", to="inventario.inventorycategory"),
        ),
        migrations.AlterField(
            model_name="product",
            name="unit",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="products", to="inventario.inventoryunit"),
        ),
        migrations.AlterField(
            model_name="movement",
            name="unit",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="movements", to="inventario.inventoryunit"),
        ),
        migrations.AlterField(
            model_name="movement",
            name="quantity_base",
            field=models.DecimalField(decimal_places=6, max_digits=16),
        ),
        migrations.AlterField(
            model_name="movement",
            name="stock_before",
            field=models.DecimalField(decimal_places=3, max_digits=12),
        ),
        migrations.AlterField(
            model_name="movement",
            name="stock_after",
            field=models.DecimalField(decimal_places=3, max_digits=12),
        ),
        migrations.AlterField(
            model_name="movement",
            name="membership",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="inventory_movements", to="farms.farmmembership"),
        ),
        migrations.RemoveConstraint(
            model_name="product",
            name="uniq_inventory_barcode_per_farm",
        ),
        migrations.AlterField(
            model_name="product",
            name="barcode",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(
                condition=~models.Q(barcode=""),
                fields=("farm", "barcode"),
                name="uniq_inventory_barcode_per_farm",
            ),
        ),
        migrations.AddConstraint(
            model_name="movement",
            constraint=models.UniqueConstraint(
                condition=~models.Q(idempotency_key=""),
                fields=("product", "idempotency_key"),
                name="uniq_inventory_movement_idempotency",
            ),
        ),
        migrations.RemoveField(
            model_name="product",
            name="legacy_category",
        ),
        migrations.RemoveField(
            model_name="product",
            name="legacy_unit",
        ),
    ]
