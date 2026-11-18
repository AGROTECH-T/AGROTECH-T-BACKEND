"""Registro y rotación de identidades de broker (BUILD-FLEPSI-02).

El ``token_id`` se lee solo por **stdin** (nunca como argumento, para que no
aparezca en la lista de procesos ni en el historial del shell), no se imprime,
no se registra y no se guarda: solo su huella HMAC versionada.

Subcomandos con escritura: ``register``, ``register-next``, ``revoke``.
Solo lectura: ``missing``, ``versions``, ``activation-check``,
``retirement-check``. La activación de una versión es un cambio de
configuración (variables de entorno) que este comando no realiza.
"""

from __future__ import annotations

import getpass
import json
import sys

from django.core.management.base import BaseCommand, CommandError

from apps.iot.broker_identity_config import load_admin_keyring
from apps.iot.models import BrokerIdentityRevocationCause
from apps.iot.services.broker_identity import (
    BrokerIdentityError,
    active_identities_missing_version,
    nonfinal_versions,
    register_identity,
    register_next_fingerprint,
    retirement_report,
    revoke_identity,
)


class Command(BaseCommand):
    help = "Gestiona identidades de broker MQTT (huellas versionadas, sin tokens)."
    stealth_options = ("stdin",)

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        for name in ("register", "register-next"):
            cmd = sub.add_parser(name)
            cmd.add_argument("--gateway-uid", required=True)
            cmd.add_argument("--operator", required=True)
            cmd.add_argument(
                "--token-stdin",
                action="store_true",
                required=True,
                help="Lee el token_id de una línea de stdin (obligatorio).",
            )
        revoke = sub.add_parser("revoke")
        revoke.add_argument("--gateway-uid", required=True)
        revoke.add_argument("--operator", required=True)
        revoke.add_argument(
            "--cause", required=True, choices=list(BrokerIdentityRevocationCause.values)
        )
        revoke.add_argument("--confirm", required=True)
        missing = sub.add_parser("missing")
        missing.add_argument("--version", type=int, default=None)
        sub.add_parser("versions")
        sub.add_parser("activation-check")
        retire = sub.add_parser("retirement-check")
        retire.add_argument("--version", type=int, required=True)

    def handle(self, *args, **options):
        action = options["action"]
        keyring = load_admin_keyring()
        try:
            if action == "register":
                identity = register_identity(
                    gateway_uid=options["gateway_uid"],
                    token_id=_read_token(options),
                    keyring=keyring,
                    process_id=_process(options["operator"]),
                )
                return self._emit(
                    {
                        "action": action,
                        "identity_id": identity.pk,
                        "versions": sorted(
                            identity.fingerprints.values_list("key_version", flat=True)
                        ),
                    }
                )
            if action == "register-next":
                fingerprint = register_next_fingerprint(
                    gateway_uid=options["gateway_uid"],
                    token_id=_read_token(options),
                    keyring=keyring,
                    process_id=_process(options["operator"]),
                )
                return self._emit({"action": action, "key_version": fingerprint.key_version})
            if action == "revoke":
                if options["confirm"] != options["gateway_uid"]:
                    raise CommandError("--confirm debe repetir exactamente el UID del gateway.")
                identity = revoke_identity(
                    gateway_uid=options["gateway_uid"],
                    cause=options["cause"],
                    process_id=_process(options["operator"]),
                )
                return self._emit({"action": action, "identity_id": identity.pk})
            if action == "missing":
                version = options["version"] or keyring.next_version or keyring.current_version
                gateways = active_identities_missing_version(version)
                return self._emit(
                    {"version": version, "count": len(gateways), "gateways": gateways}
                )
            if action == "versions":
                return self._emit(
                    {
                        "loaded": keyring.describe(),
                        "nonfinal_deliveries_by_version": {
                            f"v{k}": v for k, v in sorted(nonfinal_versions().items())
                        },
                    }
                )
            if action == "activation-check":
                if keyring.next_version is None:
                    raise CommandError("No hay versión NEXT configurada.")
                gateways = active_identities_missing_version(keyring.next_version)
                self._emit(
                    {"next": keyring.next_version, "missing": len(gateways), "gateways": gateways}
                )
                if gateways:
                    raise CommandError("Hay identidades activas sin huella NEXT: no activar.")
                return None
            report = retirement_report(options["version"], keyring)
            self._emit(
                {
                    "version": report.version,
                    "loaded_as_current_or_next": report.is_loaded_as_current_or_next,
                    "nonfinal_deliveries": report.nonfinal_deliveries,
                    "active_identities_without_current": report.active_identities_without_current,
                    "objective_criteria_met": report.objective_criteria_met,
                    "manual_criteria": [
                        "reconexión del consumer con session_present=True tras el cambio",
                        "decisión de retención de auditoría del propietario",
                    ],
                }
            )
            if not report.objective_criteria_met:
                raise CommandError("No se cumplen los criterios objetivos de retirada.")
            return None
        except BrokerIdentityError as exc:
            raise CommandError(str(exc)) from None

    def _emit(self, data) -> None:
        self.stdout.write(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _read_token(options) -> str:
    """Lee el token sin eco en terminal (getpass) o desde una tubería.

    BUILD-FLEPSI-02H (H-05): el valor nunca se escribe en la salida ni en los
    mensajes de error; la validación de formato la hace el servicio.
    """
    stream = options.get("stdin") or sys.stdin
    isatty = getattr(stream, "isatty", None)
    if callable(isatty) and isatty():
        line = getpass.getpass("token_id (no se mostrará): ")
    else:
        line = stream.readline()
    value = (line or "").rstrip("\r\n")
    if not value:
        raise CommandError("No se recibió token_id.")
    return value


def _process(operator: str) -> str:
    if not isinstance(operator, str) or not operator.strip() or len(operator) > 80:
        raise CommandError("--operator no es válido.")
    if not operator.isascii() or not operator.isprintable():
        raise CommandError("--operator no es válido.")
    return f"ops:{operator.strip()}"
