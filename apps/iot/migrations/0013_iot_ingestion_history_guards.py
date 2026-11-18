"""Protect accepted IoT frames and conflict records from rewrite or deletion."""

from django.db import migrations

from apps.iot.migrations._ingestion_history_triggers import (
    install_ingestion_history_triggers,
    uninstall_ingestion_history_triggers,
)


class Migration(migrations.Migration):
    # MariaDB trigger DDL implicitly commits; partial installation is retryable.
    atomic = False

    dependencies = [("iot", "0012_iot_history_triggers")]

    operations = [
        migrations.AlterModelOptions(
            name="receivedframe",
            options={"base_manager_name": "objects", "default_manager_name": "objects"},
        ),
        migrations.AlterModelOptions(
            name="frameconflict",
            options={"base_manager_name": "objects", "default_manager_name": "objects"},
        ),
        # An ordinary rollback still cannot remove the immutable-history guards:
        # the reverse refuses unless IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK is
        # explicitly enabled (disposable test databases only; see
        # apps/iot/migrations/_rollback_guard.py). VALIDATION-02.
        migrations.RunPython(
            install_ingestion_history_triggers,
            reverse_code=uninstall_ingestion_history_triggers,
            atomic=False,
            elidable=False,
        ),
    ]
