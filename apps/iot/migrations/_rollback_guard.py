"""Explicit opt-in for destructive IoT migration rollbacks (VALIDATION-02).

Several IoT migrations hold durable history: measurement UUIDs and corrections
(0011), immutable-evidence triggers (0012/0013) and the durable MQTT inbox
(0014/0015). Reverting them drops that history or its protection.

Django unapplies migrations newest-first and only checks reversibility when it
reaches each one, so a refusal placed only in 0013 is reached *after* 0015/0014
have already dropped the MQTT delivery tables. The guard is therefore also the
first reverse step of 0015, the newest migration that holds durable data: by
default a rollback is refused before any data-bearing object is changed
(0016 only swaps a CHECK constraint and is reversible without data loss).

The opt-in is the Django setting ``IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK``.
It is not read from the environment and is not defined in ``config.settings``;
only disposable-database tests enable it (``settings`` fixture). Enabling it
anywhere else is a reviewed, explicit code change.
"""

from django.conf import settings
from django.db.migrations.exceptions import IrreversibleError

SETTING_NAME = "IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK"


def destructive_rollback_allowed() -> bool:
    return getattr(settings, SETTING_NAME, False) is True


def require_destructive_rollback_opt_in(label: str) -> None:
    if not destructive_rollback_allowed():
        raise IrreversibleError(
            f"La reversión de {label} destruiría historial IoT durable o su protección. "
            f"Solo se permite con {SETTING_NAME}=True (bases desechables de prueba) "
            "o mediante un procedimiento de administración revisado."
        )


def rollback_barrier(label: str):
    """Reverse function that refuses unless the explicit opt-in is enabled."""

    def reverse(apps, schema_editor):
        require_destructive_rollback_opt_in(label)

    reverse.__name__ = f"rollback_barrier_{label.replace('.', '_')}"
    return reverse


def noop(apps, schema_editor):
    """Forward no-op for barrier operations."""
