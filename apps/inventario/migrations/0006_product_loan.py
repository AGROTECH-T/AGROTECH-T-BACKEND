"""Préstamos de producto, sin borrar el historial de stock."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
        ("inventario", "0005_product_is_removed"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductLoan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("borrower_name", models.CharField(max_length=120)),
                ("document_number", models.CharField(max_length=32)),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("returned_quantity", models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=models.PROTECT,
                        related_name="product_loans",
                        to="accounts.user",
                    ),
                ),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="loans",
                        to="inventario.product",
                    ),
                ),
            ],
            options={
                "db_table": "inventory_product_loans",
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
