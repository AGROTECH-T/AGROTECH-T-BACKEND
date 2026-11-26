# ruff: noqa: S101
"""Cliente de Cloudflare R2, sin red ni base.

@author Cristian Deysdayr Jimenez
"""

import json

from apps.accounts.errors import AppError
from apps.inventario.services.image_storage import CloudflareR2, ImageMissing


class _Response:
    def __init__(self, status, body, content_type="application/json"):
        self.status = status
        self._body = body
        self._type = content_type

    def read(self):
        return self._body

    def getheader(self, name, default=""):
        if name.lower() == "content-type":
            return self._type
        return default


class _Connection:
    def __init__(self, script, calls=None):
        self.script = script
        self.calls = calls

    def request(self, method, path, body=None, headers=None):
        if self.calls is not None:
            self.calls.append(method)
        if not path.startswith("/accounts/") and not path.startswith("/client/"):
            raise AssertionError("ruta inesperada")

    def getresponse(self):
        return self.script.pop(0)

    def close(self):
        return None


def test_put_get_y_borrado(monkeypatch):
    """Sube, lee y borra usando respuestas falsas de Cloudflare."""
    calls = []
    script = [
        _Response(200, json.dumps({"success": True, "result": {"id": "product-1"}}).encode()),
        _Response(200, b"jpeg", "image/jpeg"),
        _Response(200, json.dumps({"success": True}).encode()),
    ]
    monkeypatch.setattr(
        "apps.inventario.services.image_storage.http.client.HTTPSConnection",
        lambda host, timeout=30: _Connection(script, calls),
    )
    store = CloudflareR2("cuenta", "token", "agrotech-productos")
    store.put("product-1", b"jpeg", "image/jpeg")
    assert store.get("product-1") == (b"jpeg", "image/jpeg")
    store.remove("product-1")
    assert calls == ["POST", "GET", "DELETE"]


def test_put_rechazado(monkeypatch):
    """Un rechazo de R2 no se guarda como foto."""
    script = [_Response(403, b'{"success":false,"errors":[{"message":"no"}]}')]
    monkeypatch.setattr(
        "apps.inventario.services.image_storage.http.client.HTTPSConnection",
        lambda host, timeout=30: _Connection(script),
    )
    try:
        store = CloudflareR2("cuenta", "token", "agrotech-productos")
        store.put("product-1", b"jpeg", "image/jpeg")
    except AppError as exc:
        assert exc.status_code == 502
    else:
        raise AssertionError("debio rechazar la foto")


def test_lectura_ausente(monkeypatch):
    """Un 404 de Cloudflare se traduce en foto inexistente."""
    script = [_Response(404, b"")]
    monkeypatch.setattr(
        "apps.inventario.services.image_storage.http.client.HTTPSConnection",
        lambda host, timeout=30: _Connection(script),
    )
    try:
        CloudflareR2("cuenta", "token", "agrotech-productos").get("product-1")
    except ImageMissing as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("debio faltar la foto")


def test_sin_configuracion(monkeypatch):
    """Sin variables no se intenta llamar a Cloudflare."""
    from apps.inventario.services import image_storage

    monkeypatch.setattr(image_storage.settings, "CLOUDFLARE_ACCOUNT_ID", "", raising=False)
    monkeypatch.setattr(image_storage.settings, "CLOUDFLARE_API_TOKEN", "", raising=False)
    try:
        image_storage.configured_store()
    except AppError as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError("debio faltar la configuracion")
