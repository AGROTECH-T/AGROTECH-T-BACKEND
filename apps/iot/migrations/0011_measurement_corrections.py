"""Add public measurement UUIDs and append-only correction records."""

import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Count

from apps.iot.migrations._composite_fk import (
    ForeignKeySpec,
    add_foreign_keys,
    drop_foreign_keys,
)
from apps.iot.migrations._rollback_guard import noop, rollback_barrier

CORRECTION_FKS = (
    ForeignKeySpec(
        "iot_mcorr_previous_measurement_fk",
        "iot_measurement_corrections",
        ("previous_correction_id", "measurement_id", "previous_revision"),
        "iot_measurement_corrections",
        ("id", "measurement_id", "revision"),
    ),
)


def _set_correction_fks(apps, schema_editor):
    add_foreign_keys(apps, schema_editor, CORRECTION_FKS, migration="iot.0011")


def _drop_correction_fks(apps, schema_editor):
    drop_foreign_keys(apps, schema_editor, CORRECTION_FKS, migration="iot.0011")


def populate_measurement_uids(apps, schema_editor):
    """Assign a distinct public UUID to each existing row without changing evidence."""
    Measurement = apps.get_model("iot", "Measurement")
    last_pk = 0
    while True:
        rows = list(
            Measurement.objects.filter(uid__isnull=True, pk__gt=last_pk)
            .only("pk", "uid")
            .order_by("pk")[:1000]
        )
        if not rows:
            break
        for row in rows:
            row.uid = uuid.uuid4()
        Measurement.objects.bulk_update(rows, ["uid"], batch_size=1000)
        last_pk = rows[-1].pk

    if Measurement.objects.filter(uid__isnull=True).exists():
        raise RuntimeError("Quedaron mediciones sin UID público; no se aplicará unicidad.")
    duplicate = (
        Measurement.objects.values("uid")
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
        .order_by("uid")
        .first()
    )
    if duplicate is not None:
        raise RuntimeError("Hay UID públicos de medición duplicados; no se aplicará unicidad.")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("iot", "0010_sensor_source")]

    operations = [
        migrations.AddField(
            model_name="measurement",
            name="uid",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(populate_measurement_uids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="measurement",
            name="uid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.CreateModel(
            name="MeasurementCorrectionSequence",
            fields=[
                (
                    "measurement",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        primary_key=True,
                        related_name="correction_sequence",
                        serialize=False,
                        to="iot.measurement",
                    ),
                ),
                ("last_revision", models.PositiveIntegerField(default=0)),
            ],
            options={"db_table": "iot_measurement_correction_sequences"},
        ),
        migrations.CreateModel(
            name="MeasurementCorrection",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("revision", models.PositiveIntegerField()),
                ("previous_revision", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "value_before",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
                ),
                (
                    "corrected_value",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
                ),
                (
                    "correction_type",
                    models.CharField(
                        choices=[
                            ("value_adjustment", "Corrección de valor"),
                            ("invalidation", "Invalidación"),
                        ],
                        max_length=32,
                    ),
                ),
                ("reason", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("process_id", models.CharField(blank=True, max_length=100, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="iot_measurement_corrections",
                        to="accounts.user",
                    ),
                ),
                (
                    "measurement",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="corrections",
                        to="iot.measurement",
                    ),
                ),
                (
                    "previous_correction",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="successors",
                        to="iot.measurementcorrection",
                    ),
                ),
            ],
            options={"db_table": "iot_measurement_corrections", "ordering": ["measurement_id", "revision"]},
        ),
        migrations.AddConstraint(
            model_name="measurementcorrectionsequence",
            constraint=models.CheckConstraint(
                condition=models.Q(("last_revision__gte", 0)),
                name="iot_mcorrseq_revision_nonnegative",
            ),
        ),
        migrations.AddConstraint(
            model_name="measurementcorrection",
            constraint=models.UniqueConstraint(
                fields=("measurement", "revision"), name="iot_mcorr_meas_revision_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="measurementcorrection",
            constraint=models.UniqueConstraint(
                fields=("id", "measurement", "revision"),
                name="iot_mcorr_id_meas_rev_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="measurementcorrection",
            constraint=models.CheckConstraint(
                condition=models.Q(("revision__gte", 1)),
                name="iot_mcorr_revision_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="measurementcorrection",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("correction_type", "value_adjustment"),
                        ("corrected_value__isnull", False),
                    )
                    | models.Q(
                        ("correction_type", "invalidation"),
                        ("corrected_value__isnull", True),
                    )
                ),
                name="iot_mcorr_type_value_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="measurementcorrection",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(("created_by__isnull", False), ("process_id__isnull", True))
                    | models.Q(("created_by__isnull", True), ("process_id__regex", r".*\S.*"))
                ),
                name="iot_mcorr_actor_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="measurementcorrection",
            constraint=models.CheckConstraint(
                condition=models.Q(("reason__regex", r".*\S.*")),
                name="iot_mcorr_reason_nonblank",
            ),
        ),
        migrations.AddConstraint(
            model_name="measurementcorrection",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        ("revision", 1),
                        ("previous_correction__isnull", True),
                        ("previous_revision__isnull", True),
                    )
                    | models.Q(
                        ("revision__gt", 1),
                        ("previous_correction__isnull", False),
                        ("previous_revision", models.F("revision") - models.Value(1)),
                    )
                ),
                name="iot_mcorr_previous_revision_valid",
            ),
        ),
        migrations.AddIndex(
            model_name="measurementcorrection",
            index=models.Index(
                fields=["measurement", "-revision"], name="iot_mcorr_meas_rev_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="measurementcorrection",
            index=models.Index(
                fields=["previous_correction", "measurement", "previous_revision"],
                name="iot_mcorr_prev_fk_idx",
            ),
        ),
        migrations.RunPython(_set_correction_fks, _drop_correction_fks, atomic=False),
        # Corrections and assigned public measurement UUIDs are durable history.
        # Refuse automatic rollback that would drop either data set, unless the
        # explicit disposable-test opt-in is enabled (VALIDATION-02).
        migrations.RunPython(noop, reverse_code=rollback_barrier("iot.0011")),
    ]
