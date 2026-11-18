"""Add durable coordinator credential generations (IoT-04.3-C1).

The migration is intentionally transitional: ``key_version`` stays nullable and
no legacy credential is backfilled. Reversing it drops the generation counter and
the stored generations; that is only acceptable for disposable or local
databases. In production, rolling back the application must keep this migration
applied because the schema is compatible with 0008. See
docs/modules/iot-credenciales-coordinador.md section 2.11.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("iot", "0008_iot_persistence_composite_fks"),
    ]

    operations = [
        migrations.AddField(
            model_name="coordinatorcredential",
            name="key_version",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="CoordinatorKeyGeneration",
            fields=[
                (
                    "coordinator",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        primary_key=True,
                        related_name="key_generation",
                        serialize=False,
                        to="iot.coordinator",
                    ),
                ),
                ("last_key_version", models.PositiveSmallIntegerField()),
                ("updated_at", models.DateTimeField()),
            ],
            options={
                "db_table": "iot_coordinator_key_generation",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("last_key_version__gte", 1),
                            ("last_key_version__lte", 65535),
                        ),
                        name="iot_coordkeygen_range",
                    ),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="coordinatorcredential",
            constraint=models.CheckConstraint(
                condition=models.Q(("key_version__isnull", True))
                | models.Q(
                    ("key_version__gte", 1),
                    ("key_version__lte", 65535),
                ),
                name="iot_coordcred_generation_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="coordinatorcredential",
            constraint=models.UniqueConstraint(
                fields=("coordinator", "key_version"),
                name="iot_coordcred_generation_uniq",
            ),
        ),
    ]
