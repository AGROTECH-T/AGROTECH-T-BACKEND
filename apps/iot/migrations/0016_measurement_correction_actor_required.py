"""Require an actor on every measurement correction (VALIDATION-02).

Why: ``iot_mcorr_actor_valid`` (0011) was
``(created_by IS NOT NULL AND process_id IS NULL)
  OR (created_by IS NULL AND process_id REGEXP '.*\\S.*')``.
With both columns NULL the second branch evaluates to NULL (``NULL REGEXP``),
and a CHECK only rejects FALSE, so a correction without any actor was
accepted by MariaDB. The audit requirement is "exactly one actor".

Effect: adds ``iot_mcorr_actor_required`` with an explicit
``process_id IS NOT NULL`` and only then removes the old constraint, so there
is never a window without an actor CHECK (MariaDB DDL is not transactional).
MariaDB validates existing rows when the constraint is added: if any legacy
row lacks an actor, the migration stops before removing anything. Services
always assign an actor, so no such rows are expected. No data is modified.
Reverse restores the previous constraint first, then drops the new one.
"""

from django.db import migrations, models

ACTOR_REQUIRED = models.CheckConstraint(
    condition=(
        models.Q(created_by__isnull=False, process_id__isnull=True)
        | models.Q(
            created_by__isnull=True,
            process_id__isnull=False,
            process_id__regex=r".*\S.*",
        )
    ),
    name="iot_mcorr_actor_required",
)


class Migration(migrations.Migration):
    dependencies = [("iot", "0015_iot_delivery_identity_pauses")]

    operations = [
        migrations.AddConstraint(model_name="measurementcorrection", constraint=ACTOR_REQUIRED),
        migrations.RemoveConstraint(
            model_name="measurementcorrection", name="iot_mcorr_actor_valid"
        ),
    ]
