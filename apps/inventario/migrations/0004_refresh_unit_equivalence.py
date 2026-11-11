"""Actualiza libra, tonelada, galón y metro cúbico a su equivalencia."""

from django.db import migrations


def refresh_catalog(apps, schema_editor):
    from apps.inventario.seed_catalog import seed_catalog

    seed_catalog()


class Migration(migrations.Migration):
    dependencies = [
        ("inventario", "0003_catalog_units_and_snapshots"),
    ]

    operations = [
        migrations.RunPython(refresh_catalog, migrations.RunPython.noop),
    ]
