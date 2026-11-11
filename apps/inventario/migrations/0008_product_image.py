"""Foto del producto, comprimida antes de guardarse."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventario", "0007_loan_user_fk"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="image",
            field=models.FileField(blank=True, default="", upload_to="products"),
        ),
    ]
