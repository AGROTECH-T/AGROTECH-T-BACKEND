# ruff: noqa: S101
"""Pruebas unitarias de la configuración segura de FRONTEND_URL (P3/H1).

No leen variables de entorno, no resuelven DNS y no realizan llamadas de red.
"""
import pytest
from django.core.exceptions import ImproperlyConfigured

from config.frontend_url import (
    DEFAULT_DEV_FRONTEND_URL,
    build_accept_link,
    normalize_frontend_url,
)

PRODUCTION_URL = "https://agrotech.example.com"


def test_debug_empty_uses_local_fallback():
    assert normalize_frontend_url("", debug=True) == DEFAULT_DEV_FRONTEND_URL
    assert normalize_frontend_url(None, debug=True) == DEFAULT_DEV_FRONTEND_URL
    assert normalize_frontend_url("   ", debug=True) == DEFAULT_DEV_FRONTEND_URL


def test_debug_allows_explicit_local_http():
    assert (
        normalize_frontend_url("http://127.0.0.1:3000/app/", debug=True)
        == "http://127.0.0.1:3000/app"
    )


def test_debug_allows_private_ip():
    assert (
        normalize_frontend_url("http://192.168.1.10:8000/", debug=True)
        == "http://192.168.1.10:8000"
    )


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_production_requires_explicit_value(raw):
    with pytest.raises(ImproperlyConfigured, match="FRONTEND_URL"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "http://agrotech.example.com",
        "agrotech.example.com",
        "//agrotech.example.com",
        "ftp://agrotech.example.com",
    ],
)
def test_production_requires_https(raw):
    with pytest.raises(ImproperlyConfigured, match="HTTPS"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://localhost",
        "https://LOCALHOST.",
        "https://foo.localhost",
        "https://foo.localhost.",
    ],
)
def test_production_rejects_localhost(raw):
    with pytest.raises(ImproperlyConfigured, match="localhost"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://agrotech.local",
        "https://agrotech.internal",
        "https://agrotech.home.arpa",
        "https://agrotech.test",
        "https://agrotech.invalid",
        "https://agrotech.example",
    ],
)
def test_production_rejects_special_use_domains(raw):
    with pytest.raises(ImproperlyConfigured, match="uso especial"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://agrotech.onion",
        "https://agrotech.alt",
        "https://agrotech.arpa",
        "https://agrotech.localdomain",
        "https://agrotech.home",
    ],
)
def test_production_rejects_expanded_special_use_domains(raw):
    with pytest.raises(ImproperlyConfigured, match="uso especial"):
        normalize_frontend_url(raw, debug=False)


def test_production_rejects_uppercase_onion():
    with pytest.raises(ImproperlyConfigured, match="uso especial"):
        normalize_frontend_url("https://AGROTECH.ONION", debug=False)


def test_production_rejects_reverse_dns_arpa():
    with pytest.raises(ImproperlyConfigured, match="uso especial"):
        normalize_frontend_url("https://1.0.0.127.in-addr.arpa", debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://notexample.com",
        "https://myonion.com",
        "https://altavista.com",
        "https://notlocal.com",
    ],
)
def test_production_accepts_domains_containing_special_use_strings(raw):
    assert normalize_frontend_url(raw, debug=False) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "https://10.0.0.5",
        "https://172.16.0.5",
        "https://192.168.1.10",
        "https://100.64.0.1",
        "https://169.254.1.1",
        "https://0.0.0.0",
        "https://224.0.0.1",
    ],
)
def test_production_rejects_non_global_ipv4(raw):
    with pytest.raises(ImproperlyConfigured, match="IP"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://127.0.0.1",
        "https://[::1]",
        "https://[::ffff:127.0.0.1]",
    ],
)
def test_production_rejects_loopback(raw):
    with pytest.raises(ImproperlyConfigured, match="IP"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://[2002:808:808::1]",
        "https://[2002:c0a8:101::1]",
        "https://[2001:0:4136:e378:8000:63bf:3fff:fdd2]",
        "https://[64:ff9b::c0a8:101]",
        "https://[64:ff9b::808:808]",
        "https://[64:ff9b:1::1]",
    ],
)
def test_production_rejects_transition_ipv6(raw):
    with pytest.raises(ImproperlyConfigured, match="transición"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://[2606:4700:4700::1111]",
        "https://[2001:4860:4860::8888]",
        "https://[2a00:1450:4001:80b::200e]",
    ],
)
def test_production_accepts_global_ipv6(raw):
    assert normalize_frontend_url(raw, debug=False) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "https://[v1.foo]",
        "https://[V1.foo]",
    ],
)
def test_production_rejects_ipvfuture_literals(raw):
    with pytest.raises(ImproperlyConfigured, match="IPv6|malformado"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://[fe80::1%25eth0]",
        "https://[fe80::1%eth0]",
    ],
)
def test_production_rejects_ipv6_zone_id(raw):
    with pytest.raises(ImproperlyConfigured, match="zone-id|IPv6|malformado"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://[fd00::1]",
        "https://[fc00::1]",
        "https://[fe80::1]",
        "https://[2001:db8::1]",
    ],
)
def test_production_rejects_non_global_ipv6(raw):
    with pytest.raises(ImproperlyConfigured, match="IP"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://240.0.0.1",
        "https://198.18.0.1",
    ],
)
def test_production_rejects_reserved_ipv4(raw):
    with pytest.raises(ImproperlyConfigured, match="IP"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://user:pass@agrotech.example.com",
        "https://user@agrotech.example.com",
        "https://@agrotech.example.com",
    ],
)
def test_production_rejects_credentials(raw):
    with pytest.raises(ImproperlyConfigured, match="credenciales"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (f"{PRODUCTION_URL}/", PRODUCTION_URL),
        (f"{PRODUCTION_URL}:8443", f"{PRODUCTION_URL}:8443"),
        (f"{PRODUCTION_URL}/app/", f"{PRODUCTION_URL}/app"),
    ],
)
def test_production_accepts_valid_https(raw, expected):
    assert normalize_frontend_url(raw, debug=False) == expected


def test_production_normalizes_public_trailing_dot():
    assert normalize_frontend_url(f"{PRODUCTION_URL}.", debug=False) == PRODUCTION_URL


@pytest.mark.parametrize(
    "raw",
    [
        f"{PRODUCTION_URL}..",
        f"{PRODUCTION_URL}...",
    ],
)
def test_production_rejects_repeated_trailing_dots(raw):
    with pytest.raises(ImproperlyConfigured, match="puntos finales"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://a..b.com",
        "https://a..b.example.com",
    ],
)
def test_production_rejects_internal_double_dot(raw):
    with pytest.raises(ImproperlyConfigured, match="malformado"):
        normalize_frontend_url(raw, debug=False)


def test_production_rejects_localhost_with_trailing_dot():
    with pytest.raises(ImproperlyConfigured, match="localhost"):
        normalize_frontend_url("https://localhost.", debug=False)


def test_production_rejects_loopback_with_trailing_dot():
    with pytest.raises(ImproperlyConfigured, match="IP"):
        normalize_frontend_url("https://127.0.0.1.", debug=False)


def test_production_normalizes_punycode_with_trailing_dot():
    assert (
        normalize_frontend_url("https://xn--bcher-kva.com.", debug=False)
        == "https://xn--bcher-kva.com"
    )


def test_production_trims_outer_spaces():
    assert normalize_frontend_url(f"  {PRODUCTION_URL}/  ", debug=False) == PRODUCTION_URL


@pytest.mark.parametrize(
    "raw",
    [
        f"{PRODUCTION_URL}?x=1",
        f"{PRODUCTION_URL}/app#frag",
    ],
)
def test_production_rejects_query_and_fragment(raw):
    with pytest.raises(ImproperlyConfigured, match="query"):
        normalize_frontend_url(raw, debug=False)


def test_debug_rejects_query_and_fragment():
    with pytest.raises(ImproperlyConfigured, match="query"):
        normalize_frontend_url("http://localhost:3000?x=1", debug=True)


@pytest.mark.parametrize(
    "raw",
    [
        "https://2130706433",
        "https://0177.0.0.1",
        "https://0x7f.0.0.1",
        "https://127.1",
        "https://0x7f000001",
    ],
)
def test_production_rejects_non_canonical_numeric_hosts(raw):
    with pytest.raises(ImproperlyConfigured, match="no canónica"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://agrotech.example.com:abc",
        "https://agrotech.example.com:",
        "https://agrotech.example.com:0",
        "https://agrotech.example.com:70000",
    ],
)
def test_production_rejects_malformed_ports(raw):
    with pytest.raises(ImproperlyConfigured, match="puerto"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (f"{PRODUCTION_URL}:443", f"{PRODUCTION_URL}:443"),
        (f"{PRODUCTION_URL}:8443", f"{PRODUCTION_URL}:8443"),
        (f"{PRODUCTION_URL}:65535", f"{PRODUCTION_URL}:65535"),
    ],
)
def test_production_accepts_canonical_ports(raw, expected):
    assert normalize_frontend_url(raw, debug=False) == expected


@pytest.mark.parametrize(
    "raw",
    [
        f"{PRODUCTION_URL}:+443",
        f"{PRODUCTION_URL}:-443",
        f"{PRODUCTION_URL}:0443",
        f"{PRODUCTION_URL}:0",
        f"{PRODUCTION_URL}:65536",
        f"{PRODUCTION_URL}:abc",
        f"{PRODUCTION_URL}:",
    ],
)
def test_production_rejects_non_canonical_ports(raw):
    with pytest.raises(ImproperlyConfigured, match="puerto"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://[2606:4700:4700::1111]", "https://[2606:4700:4700::1111]"),
        (
            "https://[2606:4700:4700::1111]:8443",
            "https://[2606:4700:4700::1111]:8443",
        ),
    ],
)
def test_production_parses_ipv6_ports(raw, expected):
    assert normalize_frontend_url(raw, debug=False) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://[2606:4700:4700::1111]:",
        "https://[2606:4700:4700::1111]:abc",
        "https://[2606:4700:4700::1111]:+443",
    ],
)
def test_production_rejects_malformed_ipv6_ports(raw):
    with pytest.raises(ImproperlyConfigured, match="puerto"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://user:pass@agrotech.example.com:443",
        "https://@agrotech.example.com:443",
    ],
)
def test_production_rejects_credentials_before_port(raw):
    with pytest.raises(ImproperlyConfigured, match="credenciales"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://user:pass@[2606:4700:4700::1111]",
        "https://user@[2606:4700:4700::1111]:8443",
    ],
)
def test_production_rejects_credentials_with_ipv6(raw):
    with pytest.raises(ImproperlyConfigured, match="credenciales"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://agrotech.example.com:443:80",
        "https://agrotech.example.com:443:",
    ],
)
def test_production_rejects_multiple_port_segments(raw):
    with pytest.raises(ImproperlyConfigured, match="puerto"):
        normalize_frontend_url(raw, debug=False)


@pytest.mark.parametrize(
    "raw",
    [
        "https://com",
        "https://org",
    ],
)
def test_production_rejects_bare_tld(raw):
    with pytest.raises(ImproperlyConfigured, match="público"):
        normalize_frontend_url(raw, debug=False)


def test_production_accepts_max_length_label():
    host = f"{'a' * 63}.com"
    raw = f"https://{host}"
    assert normalize_frontend_url(raw, debug=False) == raw


def test_production_rejects_overlong_label():
    host = f"{'a' * 64}.com"
    with pytest.raises(ImproperlyConfigured, match="malformado"):
        normalize_frontend_url(f"https://{host}", debug=False)


def test_production_rejects_overlong_hostname():
    host = ".".join(["a" * 63] * 4)
    assert len(host) > 253
    with pytest.raises(ImproperlyConfigured, match="demasiado largo"):
        normalize_frontend_url(f"https://{host}", debug=False)


@pytest.mark.parametrize(
    "raw, expected_message",
    [
        ("https://agrotech.example.com\n", "control"),
        ("https://agrotech.example.com\t", "control"),
        ("https://agrotech.example.com /app", "espacios"),
        ("https://agrotech.example.com\\@evil.com", "barras invertidas"),
    ],
)
def test_production_rejects_unsafe_characters(raw, expected_message):
    with pytest.raises(ImproperlyConfigured, match=expected_message):
        normalize_frontend_url(raw, debug=False)


def test_production_accepts_valid_punycode():
    assert (
        normalize_frontend_url("https://xn--bcher-kva.com/", debug=False)
        == "https://xn--bcher-kva.com"
    )


def test_production_rejects_raw_unicode_host():
    with pytest.raises(ImproperlyConfigured, match="ASCII"):
        normalize_frontend_url("https://b\u00fccher.com", debug=False)


def test_production_rejects_invalid_punycode_label():
    with pytest.raises(ImproperlyConfigured, match="punycode"):
        normalize_frontend_url("https://xn--.com", debug=False)


def test_production_rejects_idna2003_non_canonical_punycode():
    with pytest.raises(ImproperlyConfigured, match="punycode"):
        normalize_frontend_url("https://xn--zca.com", debug=False)


def test_production_rejects_punycode_with_trailing_hyphen():
    with pytest.raises(ImproperlyConfigured, match="punycode"):
        normalize_frontend_url("https://xn--bcher-kva-.com", debug=False)


def test_production_normalizes_uppercase_punycode():
    assert (
        normalize_frontend_url("https://XN--BCHER-KVA.COM", debug=False)
        == "https://xn--bcher-kva.com"
    )


def test_debug_rejects_idna2003_non_canonical_punycode():
    with pytest.raises(ImproperlyConfigured, match="punycode"):
        normalize_frontend_url("http://xn--zca.com", debug=True)


def test_debug_accepts_canonical_punycode():
    assert (
        normalize_frontend_url("http://xn--bcher-kva.com/", debug=True)
        == "http://xn--bcher-kva.com"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "http://localhost:+3000",
        "http://localhost:03000",
        "http://localhost:0",
    ],
)
def test_debug_rejects_non_canonical_ports(raw):
    with pytest.raises(ImproperlyConfigured, match="puerto"):
        normalize_frontend_url(raw, debug=True)


def test_build_accept_link_uses_token_fragment():
    link = build_accept_link(PRODUCTION_URL, "token-123")
    assert link == f"{PRODUCTION_URL}/invitations/accept#token=token-123"


def test_build_accept_link_preserves_path_and_avoids_duplicate_slashes():
    link = build_accept_link(f"{PRODUCTION_URL}/app///", "tok")
    assert link == f"{PRODUCTION_URL}/app/invitations/accept#token=tok"
    assert f"{PRODUCTION_URL}/app//invitations" not in link


@pytest.mark.parametrize(
    "base",
    [
        f"{PRODUCTION_URL}?x=1",
        f"{PRODUCTION_URL}/app#frag",
    ],
)
def test_build_accept_link_rejects_query_and_fragment(base):
    with pytest.raises(ImproperlyConfigured, match="query"):
        build_accept_link(base, "tok")


def test_build_accept_link_requires_base():
    with pytest.raises(ImproperlyConfigured, match="FRONTEND_URL"):
        build_accept_link("   ", "tok")
