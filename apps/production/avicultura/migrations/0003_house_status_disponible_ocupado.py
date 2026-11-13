"""Deja el galpón solo en disponible u ocupado.

Los estados extra no se usaban: el lote abierto ya marca ocupado.
"""

from django.db import migrations, models


def normalizar_estados(apps, schema_editor):
    """Pasa cada galpón a ocupado si tiene lote abierto; si no, a disponible."""
    House = apps.get_model("avicultura", "House")
    Lot = apps.get_model("avicultura", "Lot")
    ocupadas = set(
        Lot.objects.filter(status="abierto").values_list("house_id", flat=True)
    )
    for house in House.objects.all():
        nuevo = "ocupado" if house.id in ocupadas else "disponible"
        if house.status != nuevo:
            house.status = nuevo
            house.save(update_fields=["status"])


class Migration(migrations.Migration):

    dependencies = [
        ("avicultura", "0002_alter_house_bale_kg_alter_house_bales_per_drum_and_more"),
    ]

    operations = [
        migrations.RunPython(normalizar_estados, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="house",
            name="avi_house_status_valid",
        ),
        migrations.AlterField(
            model_name="house",
            name="status",
            field=models.CharField(
                choices=[("disponible", "Disponible"), ("ocupado", "Ocupado")],
                default="disponible",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="house",
            constraint=models.CheckConstraint(
                condition=models.Q(status__in=["disponible", "ocupado"]),
                name="avi_house_status_valid",
            ),
        ),
    ]
