"""Install MariaDB guards for immutable IoT evidence and correction sequencing."""

from django.db import migrations

from apps.iot.migrations._history_triggers import (
    install_history_triggers,
    uninstall_history_triggers,
)


class Migration(migrations.Migration):
    # MariaDB trigger DDL implicitly commits; install each named trigger explicitly.
    atomic = False

    dependencies = [("iot", "0011_measurement_corrections")]

    operations = [
        migrations.RunPython(
            install_history_triggers,
            reverse_code=uninstall_history_triggers,
            atomic=False,
        ),
    ]
