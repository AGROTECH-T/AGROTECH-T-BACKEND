"""La columna deja de ser un archivo y pasa a guardar el id de Cloudflare."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventario", "0009_movement_reason"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="image",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
