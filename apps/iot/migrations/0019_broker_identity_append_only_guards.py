"""BUILD-FLEPSI-02: append-only triggers for delivery events and fingerprints.

Requires the TRIGGER privilege for the migrating account (already required by
iot.0012/0013). MariaDB trigger DDL commits implicitly, so the migration is
non-atomic and retryable. The rollback barrier is the first reverse step: a
rollback below this point would drop broker identity evidence and the durable
MQTT inbox, so it is refused unless the explicit disposable-test opt-in is set.
"""

from django.db import migrations

from apps.iot.migrations._broker_identity_triggers import (
    install_broker_identity_triggers,
    uninstall_broker_identity_triggers,
)
from apps.iot.migrations._rollback_guard import noop, rollback_barrier


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("iot", "0018_broker_identity_registry")]

    operations = [
        migrations.RunPython(
            install_broker_identity_triggers,
            reverse_code=uninstall_broker_identity_triggers,
            atomic=False,
            elidable=False,
        ),
        migrations.RunPython(noop, reverse_code=rollback_barrier("iot.0019")),
    ]
