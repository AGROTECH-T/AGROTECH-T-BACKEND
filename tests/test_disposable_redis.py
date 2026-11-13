"""Pruebas estáticas del Redis desechable opcional del orquestador (F10.3.4-A).

No se conectan a Redis ni a MariaDB: importan la configuración desechable con
credenciales ficticias y usan un cliente Redis falso. Validan que la caché
solo apunte al destino fijo de la ejecución, que los errores de Redis no se
ignoren y que el preflight rechace un servidor que no demuestre ser el de la
ejecución.
"""
import importlib

import pytest
from django.core.exceptions import ImproperlyConfigured

from config import disposable_preflight as pf

RUN_ID = "0123456789ab"
DUMMY_DB = "dummy-disposable-value"
DUMMY_REDIS = "dummy-redis-value-7c1e"
REDIS_ENV = "AGROTECH_DISPOSABLE_REDIS"
REDIS_PASSWORD_ENV = "AGROTECH_DISPOSABLE_REDIS_PASSWORD"


@pytest.fixture(autouse=True)
def _disposable_environment(monkeypatch):
    monkeypatch.setenv("AGROTECH_DISPOSABLE_CONFIRM", "1")
    monkeypatch.setenv("AGROTECH_DISPOSABLE_RUN_ID", RUN_ID)
    monkeypatch.setenv("AGROTECH_DISPOSABLE_DB_PASSWORD", DUMMY_DB)
    monkeypatch.delenv(REDIS_ENV, raising=False)
    monkeypatch.delenv(REDIS_PASSWORD_ENV, raising=False)
    yield
    # Deja el módulo como lo esperan las demás pruebas: sin Redis.
    monkeypatch.setenv("AGROTECH_DISPOSABLE_CONFIRM", "1")
    monkeypatch.setenv("AGROTECH_DISPOSABLE_RUN_ID", RUN_ID)
    monkeypatch.setenv("AGROTECH_DISPOSABLE_DB_PASSWORD", DUMMY_DB)
    monkeypatch.delenv(REDIS_ENV, raising=False)
    monkeypatch.delenv(REDIS_PASSWORD_ENV, raising=False)
    import config.settings_test_disposable as module

    importlib.reload(module)


def _load():
    import config.settings_test_disposable as module

    return importlib.reload(module)


def _enable(monkeypatch, password=DUMMY_REDIS):
    monkeypatch.setenv(REDIS_ENV, "1")
    monkeypatch.setenv(REDIS_PASSWORD_ENV, password)


def _valid_caches(module, **option_overrides):
    options = {
        "CLIENT_CLASS": module.REDIS_CLIENT_CLASS,
        "PASSWORD": DUMMY_REDIS,
    }
    options.update(option_overrides)
    return {
        "default": {
            "BACKEND": module.REDIS_CACHE_BACKEND,
            "LOCATION": module.disposable_redis_location(),
            "OPTIONS": options,
        }
    }


class FakeRedis:
    def __init__(self, info=None, marker=RUN_ID.encode(), error=None):
        self.info_value = {"redis_version": "7.4.12", "tcp_port": 6379}
        if info:
            self.info_value.update(info)
        self.marker = marker
        self.error = error
        self.closed = False
        self.calls = []

    def info(self, section):
        self.calls.append(("info", section))
        if self.error:
            raise self.error
        return dict(self.info_value)

    def get(self, key):
        self.calls.append(("get", key))
        return self.marker

    def close(self):
        self.closed = True


# --- Configuración ----------------------------------------------------------


def test_constants_fix_the_disposable_destination():
    module = _load()
    assert module.DEFAULT_TEST_REDIS_HOST == "127.0.0.1"
    assert module.DEFAULT_TEST_REDIS_PORT == "16379"
    assert module.EXPECTED_REDIS_VERSION == "7.4.12"
    assert module.disposable_redis_location() == "redis://127.0.0.1:16379/0"
    assert module.TEST_REDIS_MARKER_DB == 1


def test_without_flag_the_inherited_cache_is_kept():
    module = _load()
    assert module.disposable_redis_enabled() is False
    assert module.build_test_caches() is None
    assert module.CACHES["default"]["BACKEND"] != module.REDIS_CACHE_BACKEND
    module.validate_disposable_cache({"default": {"BACKEND": "x"}})


def test_with_flag_the_cache_is_the_fixed_redis(monkeypatch):
    _enable(monkeypatch)
    module = _load()
    default = module.CACHES["default"]
    assert set(module.CACHES) == {"default"}
    assert default["BACKEND"] == "django_redis.cache.RedisCache"
    assert default["LOCATION"] == "redis://127.0.0.1:16379/0"
    assert default["OPTIONS"]["PASSWORD"] == DUMMY_REDIS
    assert default["OPTIONS"]["SOCKET_TIMEOUT"] == 5
    assert "IGNORE_EXCEPTIONS" not in default["OPTIONS"]


def test_redis_url_is_never_used(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://10.0.0.5:6379/0")
    module = _load()
    assert module.CACHES["default"]["LOCATION"] == "redis://127.0.0.1:16379/0"


@pytest.mark.parametrize("value", ["yes", "true", "0", " 1"])
def test_ambiguous_flag_is_rejected(monkeypatch, value):
    monkeypatch.setenv(REDIS_ENV, value)
    monkeypatch.setenv(REDIS_PASSWORD_ENV, DUMMY_REDIS)
    with pytest.raises(ImproperlyConfigured):
        _load()


@pytest.mark.parametrize("password", [None, "", "   "])
def test_missing_redis_password_is_rejected(monkeypatch, password):
    monkeypatch.setenv(REDIS_ENV, "1")
    if password is None:
        monkeypatch.delenv(REDIS_PASSWORD_ENV, raising=False)
    else:
        monkeypatch.setenv(REDIS_PASSWORD_ENV, password)
    with pytest.raises(ImproperlyConfigured) as exc:
        _load()
    assert DUMMY_REDIS not in str(exc.value)


def test_redis_requires_orchestrator_confirmation(monkeypatch):
    _enable(monkeypatch)
    module = _load()
    monkeypatch.setenv("AGROTECH_DISPOSABLE_CONFIRM", "0")
    with pytest.raises(ImproperlyConfigured):
        module.build_test_caches()


def test_valid_cache_configuration_is_accepted(monkeypatch):
    _enable(monkeypatch)
    module = _load()
    module.validate_disposable_cache(_valid_caches(module))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c["default"].update(LOCATION="redis://10.0.0.5:16379/0"),
        lambda c: c["default"].update(LOCATION="redis://127.0.0.1:6379/0"),
        lambda c: c["default"].update(LOCATION="redis://127.0.0.1:16379/1"),
        lambda c: c["default"].update(BACKEND="django.core.cache.backends.locmem.LocMemCache"),
        lambda c: c["default"]["OPTIONS"].update(PASSWORD=""),
        lambda c: c["default"]["OPTIONS"].update(CLIENT_CLASS="otro.Client"),
        lambda c: c["default"]["OPTIONS"].update(IGNORE_EXCEPTIONS=True),
        lambda c: c.update(otra={"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}),
    ],
)
def test_cache_outside_the_disposable_redis_is_rejected(monkeypatch, mutate):
    _enable(monkeypatch)
    module = _load()
    caches = _valid_caches(module)
    mutate(caches)
    with pytest.raises(ImproperlyConfigured) as exc:
        module.validate_disposable_cache(caches)
    assert DUMMY_REDIS not in str(exc.value)


def test_global_ignore_exceptions_is_rejected(monkeypatch):
    _enable(monkeypatch)
    module = _load()
    with pytest.raises(ImproperlyConfigured, match="no pueden ignorarse"):
        module.validate_disposable_cache(_valid_caches(module), ignore_exceptions=True)


# --- Verificación en vivo (cliente falso) ------------------------------------


def test_live_redis_identity_is_accepted(monkeypatch):
    _enable(monkeypatch)
    module = _load()
    client = FakeRedis()
    module.validate_live_disposable_redis(client_factory=lambda: client)
    assert ("get", module.REDIS_MARKER_KEY) in client.calls
    assert client.closed


@pytest.mark.parametrize(
    "client",
    [
        FakeRedis(info={"redis_version": "7.4.11"}),
        FakeRedis(info={"redis_version": "8.2.0"}),
        FakeRedis(info={"tcp_port": 16379}),
        FakeRedis(marker=b"ffffffffffff"),
        FakeRedis(marker=None),
    ],
)
def test_live_redis_that_is_not_this_run_is_rejected(monkeypatch, client):
    _enable(monkeypatch)
    module = _load()
    with pytest.raises(ImproperlyConfigured):
        module.validate_live_disposable_redis(client_factory=lambda: client)
    assert client.closed


def test_live_redis_connection_error_is_rejected_without_details(monkeypatch):
    _enable(monkeypatch)
    module = _load()
    client = FakeRedis(error=ConnectionError(f"fallo con {DUMMY_REDIS}"))
    with pytest.raises(ImproperlyConfigured) as exc:
        module.validate_live_disposable_redis(client_factory=lambda: client)
    assert DUMMY_REDIS not in str(exc.value)
    assert "ConnectionError" in str(exc.value)
    assert client.closed


def test_default_client_uses_the_fixed_destination(monkeypatch):
    _enable(monkeypatch)
    module = _load()
    captured = {}

    class Recorder(FakeRedis):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            super().__init__()

    import redis

    monkeypatch.setattr(redis, "Redis", Recorder)
    module.validate_live_disposable_redis()
    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 16379
    assert captured["db"] == 1
    assert captured["password"] == DUMMY_REDIS
    assert captured["socket_timeout"] == 5


# --- Preflight ----------------------------------------------------------------


def _preflight(redis_validator):
    from tests.test_disposable_preflight import _run

    return _run(redis_validator=redis_validator)


def test_preflight_runs_the_redis_check_last():
    calls = []
    result = _preflight(lambda: calls.append("redis"))
    assert result.ok, result.detail
    assert [item["check"] for item in result.checks][-2:] == ["mail_blocked", "redis"]
    assert calls == ["redis"]


def test_preflight_rejects_redis_with_exit_code_10():
    def reject():
        raise ImproperlyConfigured("Redis ajeno")

    result = _preflight(reject)
    assert result.status == pf.STATUS_REDIS_NOT_ALLOWED
    assert pf.exit_code_for_status(result.status) == 10
    assert "redis" not in [item["check"] for item in result.checks]


def test_preflight_without_redis_keeps_the_original_checks():
    result = _preflight(None)
    assert result.ok, result.detail
    assert "redis" not in [item["check"] for item in result.checks]


def test_check_disposable_redis_validates_settings_and_server(monkeypatch):
    import config.settings_test_disposable as guard

    calls = []
    monkeypatch.setattr(guard, "validate_disposable_cache", lambda caches, ignore: calls.append(("cache", ignore)))
    monkeypatch.setattr(guard, "validate_live_disposable_redis", lambda: calls.append(("live",)))
    pf.check_disposable_redis()
    assert calls == [("cache", False), ("live",)]


def test_pytest_plugin_skips_redis_without_flag(monkeypatch):
    monkeypatch.setattr(pf, "check_disposable_redis", lambda: pytest.fail("no debe verificarse"))
    pf._check_redis_for_pytest(pytest)


def test_pytest_plugin_blocks_when_redis_is_not_verified(monkeypatch):
    _enable(monkeypatch)
    _load()

    def reject():
        raise ImproperlyConfigured("Redis ajeno")

    monkeypatch.setattr(pf, "check_disposable_redis", reject)
    with pytest.raises(pytest.UsageError, match="Redis ajeno"):
        pf._check_redis_for_pytest(pytest)
