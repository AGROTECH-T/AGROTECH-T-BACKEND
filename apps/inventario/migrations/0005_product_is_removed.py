"""Marca productos quitados de la lista sin borrar su historial."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventario", "0004_refresh_unit_equivalence"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="is_removed",
            field=models.BooleanField(default=False),
        ),
    ]
