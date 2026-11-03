"""Ajusta unidad y categoría al catálogo fijo del sistema."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventario", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="product",
            name="category",
            field=models.CharField(
                choices=[
                    ("concentrados", "Concentrados"),
                    ("vitaminas_medicamentos", "Vitaminas y medicamentos"),
                    ("cama_cascarilla", "Cama y cascarilla"),
                    ("fertilizantes", "Fertilizantes"),
                    ("forrajes", "Forrajes"),
                    ("herramientas", "Herramientas"),
                    ("otros", "Otros"),
                ],
                default="otros",
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="unit",
            field=models.CharField(
                choices=[
                    ("kg", "Kilogramo (kg)"),
                    ("g", "Gramo (g)"),
                    ("bulto_40kg", "Bulto (40 kg)"),
                    ("saco", "Saco"),
                    ("litro", "Litro (L)"),
                    ("unidad", "Unidad"),
                    ("arroba", "Arroba"),
                    ("caneca", "Caneca"),
                ],
                max_length=20,
            ),
        ),
    ]
