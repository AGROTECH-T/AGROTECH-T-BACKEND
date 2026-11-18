"""BUILD-FLEPSI-02H (H-02): admit ``bi:overflow`` / ``bi:overflowcap`` keys.

Only widens the ``dedupe_key`` CHECK so overflow evidences can be recorded as
idempotent append-only events (``UNIQUE(delivery, dedupe_key)``, triggers of
iot.0019). The new constraint is a strict superset of the old one and is added
*before* the old one is removed, so there is never a window without a CHECK
(MariaDB DDL is not transactional). No data is read, changed or deleted.

The rollback barrier is the first reverse step (newest migration): rolling back
below this point would drop broker identity evidence and the durable inbox.
"""

from django.db import migrations, models
from django.db.models import Q

from apps.iot.migrations._rollback_guard import noop, rollback_barrier

DEDUPE_KEY_REGEX_V2 = (
    r"^bi:((mismatch|postfinal|overflow):v[0-9]{1,5}:[0-9a-f]{64}"
    r"|unverifiable:v[0-9]{1,5}|late|limit|overflowcap)$"
)


class Migration(migrations.Migration):
    dependencies = [("iot", "0019_broker_identity_append_only_guards")]

    operations = [
        migrations.AddConstraint(
            model_name="iotmqttdeliveryevent",
            constraint=models.CheckConstraint(
                condition=Q(dedupe_key__isnull=True) | Q(dedupe_key__regex=DEDUPE_KEY_REGEX_V2),
                name="iot_mqtt_event_dedupe_v2_ck",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="iotmqttdeliveryevent",
            name="iot_mqtt_event_dedupe_ck",
        ),
        migrations.RunPython(noop, reverse_code=rollback_barrier("iot.0020")),
    ]
