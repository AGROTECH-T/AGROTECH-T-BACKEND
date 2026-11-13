# ruff: noqa: S101, S105
"""Pruebas de F10.3.1: códigos de invitación y campo ``code_hash``.

Propósito: verificar la generación, la normalización y la huella HMAC-SHA256
de ``apps.farms.invitation_codes`` y la compatibilidad del nuevo campo con las
invitaciones existentes.
Contexto: la clave HMAC es ficticia y se genera en cada prueba con
``secrets``; no se persiste en ningún archivo. Las pruebas de modelo solo
deben ejecutarse en la base desechable del orquestador.
"""
import hashlib
import re
import secrets
from datetime import timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, transaction
from django.db.models.fields import NOT_PROVIDED
from django.utils import timezone

from apps.farms import invitation_codes as codes
from apps.farms import invitation_services
from apps.farms.models import (
    Farm,
    FarmInvitation,
    FarmMembership,
    FarmRole,
    InvitationStatus,
)

CODE_RE = re.compile(r"^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{5}-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{5}$")
EMAIL = "invitada@example.com"


@pytest.fixture
def hmac_key(settings):
    """Clave ficticia de prueba, distinta en cada ejecución."""
    key = secrets.token_urlsafe(48)
    settings.INVITATION_CODE_HMAC_KEY = key
    return key


# --- Generación -------------------------------------------------------------

def test_generated_code_format_length_and_alphabet():
    code = codes.generate_code()
    assert CODE_RE.fullmatch(code)
    raw = code.replace("-", "")
    assert len(raw) == codes.CODE_LENGTH == 10
    assert set(raw) <= set(codes.CODE_ALPHABET)


def test_alphabet_is_the_approved_one_without_ambiguous_characters():
    assert codes.CODE_ALPHABET == "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    assert not set("0O1IL") & set(codes.CODE_ALPHABET)


def test_generation_uses_secrets_choice(monkeypatch):
    calls = []

    def spy(seq):
        calls.append(seq)
        return seq[0]

    monkeypatch.setattr(codes.secrets, "choice", spy)
    assert codes.generate_code() == "AAAAA-AAAAA"
    assert len(calls) == 10
    assert all(seq == codes.CODE_ALPHABET for seq in calls)


def test_generation_produces_distinct_codes():
    generated = {codes.generate_code() for _ in range(2000)}
    assert len(generated) == 2000
    assert all(CODE_RE.fullmatch(code) for code in generated)


def test_generated_code_round_trips_through_normalization():
    code = codes.generate_code()
    assert codes.format_code(codes.normalize_code(code)) == code


# --- Normalización ----------------------------------------------------------

@pytest.mark.parametrize(
    "raw",
    [
        "ABCDE-FGHJK",
        "ABCDEFGHJK",
        "  ABCDE-FGHJK  ",
        "\tabcde-fghjk\n",
        "abcdefghjk",
        "AbCdE-fGhJk",
    ],
)
def test_normalize_accepts_presentation_variants(raw):
    assert codes.normalize_code(raw) == "ABCDEFGHJK"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "-",
        "ABCDE-FGHJ",  # 9
        "ABCDE-FGHJKM",  # 11
        "ABCDE FGHJK",  # espacio interior
        "ABCDE_FGHJK",
        "ABCDE-FGHJ0",  # cero
        "ABCDE-FGHJO",  # O
        "ABCDE-FGHJ1",  # uno
        "ABCDE-FGHJI",  # I
        "ABCDE-FGHJL",  # L
        "abcde-fghjl",  # l minúscula
    ],
)
def test_normalize_rejects_invalid_input(raw):
    with pytest.raises(codes.InvalidInvitationCode):
        codes.normalize_code(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "ABCDE-FGHJſ",  # «s» larga: upper() daría «S»
        "ABCDE-FGHJK",  # signo Kelvin, parecido a K
        "АBCDE-FGHJK",  # A cirílica
        "ＡBCDE-FGHJK",  # A de ancho completo
        "ABCDE‐FGHJK",  # guion Unicode
        "ABCDE-FGHJK​",  # espacio de ancho cero
    ],
)
def test_normalize_rejects_unicode_lookalikes(raw):
    with pytest.raises(codes.InvalidInvitationCode):
        codes.normalize_code(raw)


@pytest.mark.parametrize("raw", [None, 1234567890, b"ABCDEFGHJK", ["ABCDEFGHJK"]])
def test_normalize_rejects_non_text(raw):
    with pytest.raises(codes.InvalidInvitationCode):
        codes.normalize_code(raw)


def test_invalid_code_error_never_contains_the_input():
    secret_like = "ZZZZZ-ZZZZ0"
    with pytest.raises(codes.InvalidInvitationCode) as info:
        codes.normalize_code(secret_like)
    rendered = f"{info.value} {info.value!r} {info.value.args}"
    assert "ZZZZ" not in rendered
    assert str(info.value) == codes.INVALID_CODE_MESSAGE


# --- HMAC -------------------------------------------------------------------

def test_hash_is_64_lowercase_hex(hmac_key):
    digest = codes.code_hash(EMAIL, "ABCDE-FGHJK")
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert len(digest) == codes.CODE_HASH_LENGTH


def test_hash_matches_reference_hmac(hmac_key):
    import hmac

    expected = hmac.new(
        hmac_key.encode(), f"{EMAIL}:ABCDEFGHJK".encode(), hashlib.sha256
    ).hexdigest()
    assert codes.code_hash(EMAIL, "ABCDE-FGHJK") == expected


def test_hash_is_not_plain_sha256(hmac_key):
    plain = hashlib.sha256(f"{EMAIL}:ABCDEFGHJK".encode()).hexdigest()
    assert codes.code_hash(EMAIL, "ABCDEFGHJK") != plain


def test_hash_is_deterministic_and_normalizes_inputs(hmac_key):
    first = codes.code_hash(EMAIL, "ABCDE-FGHJK")
    assert codes.code_hash(EMAIL, "ABCDE-FGHJK") == first
    assert codes.code_hash("  Invitada@Example.COM ", " abcdefghjk ") == first


def test_hash_changes_with_code(hmac_key):
    assert codes.code_hash(EMAIL, "ABCDE-FGHJK") != codes.code_hash(EMAIL, "ABCDE-FGHJM")


def test_hash_changes_with_email(hmac_key):
    code = "ABCDE-FGHJK"
    assert codes.code_hash(EMAIL, code) != codes.code_hash("otra@example.com", code)


def test_hash_changes_with_key(settings, hmac_key):
    first = codes.code_hash(EMAIL, "ABCDE-FGHJK")
    settings.INVITATION_CODE_HMAC_KEY = secrets.token_urlsafe(48)
    assert codes.code_hash(EMAIL, "ABCDE-FGHJK") != first


def test_hash_rejects_invalid_code_before_using_key(settings):
    settings.INVITATION_CODE_HMAC_KEY = ""
    with pytest.raises(codes.InvalidInvitationCode):
        codes.code_hash(EMAIL, "ABCDE-FGHJ0")


def test_hash_requires_email(hmac_key):
    with pytest.raises(ValueError, match="correo"):
        codes.code_hash("   ", "ABCDE-FGHJK")


@pytest.mark.parametrize("missing", ["", "   ", None])
def test_missing_key_fails_closed(settings, missing):
    settings.INVITATION_CODE_HMAC_KEY = missing
    with pytest.raises(ImproperlyConfigured, match="INVITATION_CODE_HMAC_KEY no está configurada"):
        codes.code_hash(EMAIL, "ABCDE-FGHJK")


def test_missing_setting_attribute_fails_closed(settings):
    del settings.INVITATION_CODE_HMAC_KEY
    with pytest.raises(ImproperlyConfigured):
        codes.code_hash(EMAIL, "ABCDE-FGHJK")


def test_short_key_fails_without_revealing_it(settings):
    weak = "clave-corta-" + secrets.token_hex(4)
    settings.INVITATION_CODE_HMAC_KEY = weak
    with pytest.raises(ImproperlyConfigured) as info:
        codes.code_hash(EMAIL, "ABCDE-FGHJK")
    assert weak not in str(info.value)
    assert "al menos 32" in str(info.value)


def test_secret_key_is_never_used_as_fallback(settings):
    settings.SECRET_KEY = secrets.token_urlsafe(48)
    settings.INVITATION_CODE_HMAC_KEY = ""
    with pytest.raises(ImproperlyConfigured):
        codes.code_hash(EMAIL, "ABCDE-FGHJK")


def test_key_equal_to_secret_key_is_rejected(settings):
    shared = secrets.token_urlsafe(48)
    settings.SECRET_KEY = shared
    settings.INVITATION_CODE_HMAC_KEY = shared
    with pytest.raises(ImproperlyConfigured, match="independiente de SECRET_KEY") as info:
        codes.code_hash(EMAIL, "ABCDE-FGHJK")
    assert shared not in str(info.value)


def test_key_and_code_do_not_leak_in_hash_or_errors(hmac_key):
    digest = codes.code_hash(EMAIL, "ABCDE-FGHJK")
    assert hmac_key not in digest
    assert "ABCDEFGHJK" not in digest.upper()


def test_hashes_match_is_constant_time_compare(hmac_key):
    digest = codes.code_hash(EMAIL, "ABCDE-FGHJK")
    assert codes.hashes_match(digest, codes.code_hash(EMAIL, "abcdefghjk"))
    assert not codes.hashes_match(digest, codes.code_hash(EMAIL, "ABCDE-FGHJM"))
    assert not codes.hashes_match(digest, digest[:-1] + "é")
    assert not codes.hashes_match(digest, None)


def test_hashes_match_uses_hmac_compare_digest(monkeypatch):
    calls = []
    monkeypatch.setattr(codes.hmac, "compare_digest", lambda a, b: calls.append((a, b)) or True)
    assert codes.hashes_match("a" * 64, "b" * 64)
    assert calls == [("a" * 64, "b" * 64)]


def test_new_code_for_returns_code_and_matching_hash(hmac_key):
    code, digest = codes.new_code_for(EMAIL)
    assert CODE_RE.fullmatch(code)
    assert digest == codes.code_hash(EMAIL, code)
    assert code.replace("-", "") not in digest.upper()


# --- Modelo -----------------------------------------------------------------

def _farm() -> Farm:
    return Farm.objects.create(name="Finca códigos")


def _invitation(farm, email=EMAIL, **fields) -> FarmInvitation:
    status = fields.pop("status", InvitationStatus.PENDING)
    return FarmInvitation.objects.create(
        farm=farm,
        email=email,
        pending_email=email if status == InvitationStatus.PENDING else None,
        role=fields.pop("role", FarmRole.OPERADOR),
        status=status,
        token_hash=fields.pop("token_hash", hashlib.sha256(email.encode()).hexdigest()),
        expires_at=timezone.now() + timedelta(days=7),
        **fields,
    )


def test_field_definition():
    field = FarmInvitation._meta.get_field("code_hash")
    assert field.max_length == 64
    assert field.null is True
    assert field.blank is True
    assert field.unique is True
    assert field.default is NOT_PROVIDED  # sin valor por defecto: NULL


@pytest.mark.django_db
def test_legacy_invitations_keep_null_and_several_nulls_coexist():
    farm = _farm()
    first = _invitation(farm, "uno@example.com")
    second = _invitation(farm, "dos@example.com")
    other_farm = _invitation(_farm(), "uno@example.com")
    for invitation in (first, second, other_farm):
        invitation.refresh_from_db()
        assert invitation.code_hash is None
    assert FarmInvitation.objects.filter(code_hash__isnull=True).count() == 3


@pytest.mark.django_db
def test_listing_does_not_generate_codes():
    farm = _farm()
    _invitation(farm)
    list(FarmInvitation.objects.all())
    assert not FarmInvitation.objects.filter(code_hash__isnull=False).exists()


@pytest.mark.django_db
def test_valid_hash_is_stored(hmac_key):
    _code, digest = codes.new_code_for(EMAIL)
    invitation = _invitation(_farm(), code_hash=digest)
    invitation.refresh_from_db()
    assert invitation.code_hash == digest
    assert len(invitation.code_hash) == 64


@pytest.mark.django_db(transaction=True)
def test_duplicate_hash_is_rejected(hmac_key):
    _code, digest = codes.new_code_for(EMAIL)
    farm = _farm()
    _invitation(farm, "uno@example.com", code_hash=digest)
    with pytest.raises(IntegrityError), transaction.atomic():
        _invitation(_farm(), "dos@example.com", code_hash=digest)
    assert FarmInvitation.objects.filter(code_hash=digest).count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("status", InvitationStatus.values)
def test_existing_statuses_accept_null_and_hash(hmac_key, status):
    farm = _farm()
    legacy = _invitation(farm, "legado@example.com", status=status)
    _code, digest = codes.new_code_for("nuevo@example.com")
    coded = _invitation(farm, "nuevo@example.com", status=status, code_hash=digest)
    legacy.refresh_from_db()
    coded.refresh_from_db()
    assert legacy.status == coded.status == status
    assert legacy.code_hash is None
    assert coded.code_hash == digest


@pytest.mark.django_db
def test_code_hash_coexists_with_token_hash(hmac_key):
    token_hash = hashlib.sha256(b"token-legado").hexdigest()
    _code, digest = codes.new_code_for(EMAIL)
    invitation = _invitation(_farm(), token_hash=token_hash, code_hash=digest)
    invitation.refresh_from_db()
    assert invitation.token_hash == token_hash
    assert invitation.code_hash == digest
    assert invitation.token_hash != invitation.code_hash


@pytest.mark.django_db
def test_legacy_link_acceptance_still_works(make_user):
    admin = make_user("70000001")
    guest = make_user("70000002", correo=EMAIL)
    farm = _farm()
    FarmMembership.objects.create(farm=farm, user=admin, role=FarmRole.ADMINISTRADOR)
    token = "token-de-enlace-legado"
    invitation = _invitation(farm, token_hash=hashlib.sha256(token.encode()).hexdigest())
    assert invitation.code_hash is None

    membership = invitation_services.accept_invitation(user=guest, token=token)

    invitation.refresh_from_db()
    assert membership.role == FarmRole.OPERADOR
    assert invitation.status == InvitationStatus.ACCEPTED
    assert invitation.token_hash == ""
    assert invitation.code_hash is None
