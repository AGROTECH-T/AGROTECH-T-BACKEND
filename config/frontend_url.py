"""Normalización y validación segura de FRONTEND_URL (P3/H1).

Propósito: impedir que una configuración incorrecta genere enlaces de
invitación hacia localhost, HTTP o destinos locales en producción.

Contexto: funciones puras, sin lectura de variables de entorno, sin Django
settings, sin resolución DNS y sin llamadas de red. La lectura de
``FRONTEND_URL`` y la decisión de ``DEBUG`` corresponden a ``config.platform``.

Riesgo residual documentado: la validación es estructural y sobre IP literales.
Un dominio aparentemente público podría resolver a una IP privada; no se
resuelve DNS al arrancar para evitar dependencias de red y condiciones de
carrera. La política IDN usa el códec IDNA2003 de la biblioteca estándar y exige
un round-trip canónico en las etiquetas ``xn--``; no se implementa IDNA2008.
Las direcciones IPv6 de transición (6to4, Teredo y NAT64) se rechazan de forma
explícita, igual que los literales IPvFuture y las IPv6 con zone-id. Las reglas
estructurales (IDNA2003, puertos canónicos y puntos finales) se aplican en todos
los entornos; ``DEBUG`` solo habilita el fallback local, HTTP y hosts locales o
privados. Unicode sin normalización controlada no está permitido.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

DEFAULT_DEV_FRONTEND_URL = "http://localhost:3000"
ACCEPT_PATH = "/invitations/accept"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s")
_HOST_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_NUMERIC_OR_HEX_HOST = re.compile(
    r"^(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:\.(?:0[xX][0-9a-fA-F]+|[0-9]+))*$"
)
_PORT_TEXT = re.compile(r"^[1-9][0-9]{0,4}$")
_SPECIAL_USE_DOMAINS = frozenset(
    {
        "localhost",
        "local",
        "internal",
        "home.arpa",
        "test",
        "invalid",
        "example",
        "onion",
        "alt",
        "arpa",
        "localdomain",
        "home",
    }
)
_SHARED_IPV4_NETWORK = ipaddress.ip_network("100.64.0.0/10")
_TRANSITION_NETWORKS = (
    ipaddress.ip_network("2001::/32"),
    ipaddress.ip_network("2002::/16"),
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)


def normalize_frontend_url(raw: str | None, *, debug: bool) -> str:
    """Normaliza y valida la URL base del frontend.

    En desarrollo, un valor vacío o solo de espacios usa
    ``http://localhost:3000``. En producción, ese caso es un error.

    Los puertos se validan en su forma textual canónica en todos los entornos.
    Los hosts entre corchetes deben ser IPv6 estándar, sin zone-id ni IPvFuture.
    """
    value = "" if raw is None else raw
    if not isinstance(value, str):
        raise ImproperlyConfigured("FRONTEND_URL debe ser una cadena de texto.")

    if _CONTROL_CHARS.search(value):
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede contener caracteres de control."
        )

    stripped = value.strip()
    if not stripped:
        if debug:
            return DEFAULT_DEV_FRONTEND_URL
        raise ImproperlyConfigured("FRONTEND_URL es obligatoria en producción.")

    if _WHITESPACE.search(stripped):
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede contener espacios internos."
        )
    if "\\" in stripped:
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede contener barras invertidas."
        )
    if "?" in stripped or "#" in stripped:
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede incluir query ni fragment."
        )

    try:
        parsed = urlsplit(stripped)
        hostname = parsed.hostname
    except ValueError as exc:
        raise ImproperlyConfigured(
            "FRONTEND_URL tiene un host o puerto malformado."
        ) from exc

    scheme = parsed.scheme.lower()
    if debug:
        if scheme not in {"http", "https"}:
            raise ImproperlyConfigured(
                "FRONTEND_URL debe usar http o https en desarrollo."
            )
    elif scheme != "https":
        raise ImproperlyConfigured(
            "FRONTEND_URL debe usar HTTPS en producción."
        )

    if not parsed.netloc or not hostname:
        raise ImproperlyConfigured(
            "FRONTEND_URL debe incluir un hostname válido."
        )
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede incluir credenciales."
        )
    if parsed.netloc.startswith("["):
        _validate_bracketed_literal(hostname)

    raw_port = _extract_raw_port(parsed.netloc)
    port = None
    if raw_port is not None:
        if not _PORT_TEXT.match(raw_port):
            raise ImproperlyConfigured(
                "FRONTEND_URL tiene un puerto malformado."
            )
        port = int(raw_port)
        if not 1 <= port <= 65535:
            raise ImproperlyConfigured(
                "FRONTEND_URL tiene un puerto fuera del rango permitido."
            )

    host = _normalize_host(hostname)
    _validate_host(host, debug=debug)

    path = parsed.path.rstrip("/")
    if path and not path.startswith("/"):
        raise ImproperlyConfigured(
            "FRONTEND_URL tiene una ruta base malformada."
        )

    host_for_url = f"[{host}]" if ":" in host else host
    netloc = host_for_url if port is None else f"{host_for_url}:{port}"
    return f"{scheme}://{netloc}{path}"


def build_accept_link(base: str, token: str) -> str:
    """Construye el enlace de aceptación con el token en el fragmento.

    La base debe estar validada. Aun así se rechazan query, fragment y
    caracteres que romperían la estructura del enlace.
    """
    normalized = (base or "").strip().rstrip("/")
    if not normalized:
        raise ImproperlyConfigured(
            "FRONTEND_URL no está configurada para construir el enlace."
        )
    if _CONTROL_CHARS.search(normalized):
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede contener caracteres de control."
        )
    if _WHITESPACE.search(normalized):
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede contener espacios."
        )
    if "\\" in normalized:
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede contener barras invertidas."
        )
    if "?" in normalized or "#" in normalized:
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede incluir query ni fragment."
        )
    return f"{normalized}{ACCEPT_PATH}#token={token}"


def _validate_bracketed_literal(hostname: str) -> None:
    """Exige que un host entre corchetes sea IPv6 estándar, sin zone-id."""
    if "%" in hostname:
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede usar IPv6 con zone-id."
        )
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError as exc:
        raise ImproperlyConfigured(
            "FRONTEND_URL debe usar una dirección IPv6 válida entre corchetes."
        ) from exc
    if not isinstance(address, ipaddress.IPv6Address):
        raise ImproperlyConfigured(
            "FRONTEND_URL debe usar una dirección IPv6 válida entre corchetes."
        )


def _extract_raw_port(netloc: str) -> str | None:
    """Extrae el puerto textual del netloc sin convertirlo a entero."""
    if netloc.startswith("["):
        closing = netloc.find("]")
        if closing == -1:
            raise ImproperlyConfigured(
                "FRONTEND_URL tiene un puerto malformado."
            )
        remainder = netloc[closing + 1:]
        if not remainder:
            return None
        if not remainder.startswith(":"):
            raise ImproperlyConfigured(
                "FRONTEND_URL tiene un puerto malformado."
            )
        return remainder[1:]
    if netloc.count(":") > 1:
        raise ImproperlyConfigured(
            "FRONTEND_URL tiene un puerto malformado."
        )
    if ":" not in netloc:
        return None
    return netloc.split(":", 1)[1]


def _normalize_host(hostname: str) -> str:
    """Normaliza el hostname y aplica la política IDN explícita."""
    host = hostname.strip().lower()
    if not host:
        raise ImproperlyConfigured(
            "FRONTEND_URL debe incluir un hostname válido."
        )
    if not host.isascii():
        raise ImproperlyConfigured(
            "FRONTEND_URL debe usar dominios ASCII o punycode; "
            "el Unicode sin normalizar no está permitido."
        )
    if host.endswith(".."):
        raise ImproperlyConfigured(
            "FRONTEND_URL tiene puntos finales repetidos en el hostname."
        )
    if host.endswith("."):
        host = host[:-1]
    if not host:
        raise ImproperlyConfigured(
            "FRONTEND_URL debe incluir un hostname válido."
        )
    return host


def _validate_host(host: str, *, debug: bool) -> None:
    """Valida hosts, IP literales y dominios locales o de uso especial."""
    if not host:
        raise ImproperlyConfigured(
            "FRONTEND_URL debe incluir un hostname válido."
        )
    if host.startswith(".") or ".." in host:
        raise ImproperlyConfigured(
            "FRONTEND_URL tiene un hostname malformado."
        )

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None

    if address is not None:
        _validate_ip_address(address, debug=debug)
        return

    if _NUMERIC_OR_HEX_HOST.match(host):
        raise ImproperlyConfigured(
            "FRONTEND_URL usa una representación IP no canónica."
        )

    labels = host.split(".")
    for label in labels:
        if label.startswith("xn--"):
            _validate_punycode_label(label)
        if not _HOST_LABEL.match(label):
            raise ImproperlyConfigured(
                "FRONTEND_URL tiene un hostname malformado."
            )
    if labels[-1].isdigit():
        raise ImproperlyConfigured(
            "FRONTEND_URL tiene un hostname malformado."
        )
    if len(host) > 253:
        raise ImproperlyConfigured(
            "FRONTEND_URL tiene un hostname demasiado largo."
        )

    if debug:
        return

    if host == "localhost" or host.endswith(".localhost"):
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede usar localhost en producción."
        )
    if _is_special_use(host):
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede usar un dominio de uso especial en producción."
        )
    if "." not in host:
        raise ImproperlyConfigured(
            "FRONTEND_URL debe usar un dominio público en producción."
        )


def _is_special_use(host: str) -> bool:
    """Comprueba dominios de uso especial respetando límites de etiqueta."""
    return any(
        host == domain or host.endswith(f".{domain}")
        for domain in _SPECIAL_USE_DOMAINS
    )


def _validate_punycode_label(label: str) -> None:
    """Valida una etiqueta A-label con round-trip IDNA2003 en todos los entornos."""
    if len(label) <= 4:
        raise ImproperlyConfigured(
            "FRONTEND_URL tiene una etiqueta punycode incompleta."
        )
    try:
        decoded = label.encode("ascii").decode("idna")
        canonical = decoded.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ImproperlyConfigured(
            "FRONTEND_URL tiene una etiqueta punycode inválida."
        ) from exc
    if not decoded or canonical != label:
        raise ImproperlyConfigured(
            "FRONTEND_URL tiene una etiqueta punycode no canónica."
        )


def _validate_ip_address(address, *, debug: bool) -> None:
    """Rechaza IP no globales y las IPv6 de transición."""
    if debug:
        return
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        _validate_ip_address(address.ipv4_mapped, debug=debug)
        return
    if isinstance(address, ipaddress.IPv6Address) and any(
        address in network for network in _TRANSITION_NETWORKS
    ):
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede usar direcciones IPv6 de transición en producción."
        )
    if isinstance(address, ipaddress.IPv4Address) and address in _SHARED_IPV4_NETWORK:
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede usar direcciones IP compartidas en producción."
        )
    forbidden = (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
        or address.is_multicast
        or not address.is_global
    )
    if forbidden:
        raise ImproperlyConfigured(
            "FRONTEND_URL no puede usar direcciones IP locales, privadas "
            "o no globales en producción."
        )
