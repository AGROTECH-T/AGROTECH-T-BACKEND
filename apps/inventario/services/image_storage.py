"""Fotos de insumos en Cloudflare R2.

Propósito: guardar el archivo fuera de la base y del disco del servidor.
Contexto: la columna del producto solo conserva la clave del objeto.
@author Cristian Deysdayr Jimenez
"""

import http.client
import json
from urllib.parse import urlparse

from django.conf import settings

from apps.accounts.errors import AppError

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
DEFAULT_BUCKET = "agrotech-productos"


class ImageMissing(AppError):
    """La foto ya no está en Cloudflare."""

    def __init__(self):
        super().__init__("este producto no tiene foto", 404)


class MemoryImageStore:
    """Almacén en memoria para pruebas. No habla con Cloudflare."""

    def __init__(self):
        self.files: dict[str, tuple[bytes, str]] = {}

    def put(self, image_id: str, content: bytes, content_type: str) -> None:
        self.files[image_id] = (content, content_type)

    def get(self, image_id: str) -> tuple[bytes, str]:
        found = self.files.get(image_id)
        if found is None:
            raise ImageMissing()
        return found

    def remove(self, image_id: str) -> None:
        self.files.pop(image_id, None)


class CloudflareR2:
    """Cliente de objetos R2. El token no sale en los errores."""

    def __init__(self, account_id: str, token: str, bucket: str):
        self.account_id = account_id
        self.token = token
        self.bucket = bucket

    def put(self, image_id: str, content: bytes, content_type: str) -> None:
        status, _, raw = self._call(
            "PUT",
            self._url(image_id),
            data=content,
            content_type=content_type,
        )
        if status != 200 or not _success(raw):
            raise AppError("no se pudo guardar la foto", 502)

    def get(self, image_id: str) -> tuple[bytes, str]:
        status, content_type, body = self._call(
            "GET",
            self._url(image_id),
            fail_message="no se pudo leer la foto",
        )
        if status == 404:
            raise ImageMissing()
        if status != 200:
            raise AppError("no se pudo leer la foto", 502)
        kind = content_type.split(";")[0].strip().lower() or "image/jpeg"
        return body, kind

    def remove(self, image_id: str) -> None:
        status, _, raw = self._call(
            "DELETE",
            self._url(image_id),
            fail_message="no se pudo quitar la foto",
        )
        if status == 404:
            return
        if status != 200 or not _success(raw):
            raise AppError("no se pudo quitar la foto", 502)

    def _url(self, image_id: str) -> str:
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account_id}/r2/buckets/{self.bucket}/objects/{image_id}"
        )

    def _call(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        content_type: str | None = None,
        fail_message: str = "no se pudo guardar la foto",
    ) -> tuple[int, str, bytes]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "api.cloudflare.com":
            raise AppError(fail_message, 502)
        headers = {"Authorization": f"Bearer {self.token}"}
        if content_type:
            headers["Content-Type"] = content_type
        path = parsed.path or "/"
        connection = http.client.HTTPSConnection("api.cloudflare.com", timeout=30)
        try:
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            kind = response.getheader("Content-Type", "")
            return response.status, kind, response.read()
        except (http.client.HTTPException, TimeoutError, OSError):
            raise AppError(fail_message, 502) from None
        finally:
            connection.close()


def configured_store() -> CloudflareR2:
    """Arma el cliente con las variables de entorno. Falla si faltan."""
    account_id = getattr(settings, "CLOUDFLARE_ACCOUNT_ID", "") or ""
    token = getattr(settings, "CLOUDFLARE_API_TOKEN", "") or ""
    bucket = getattr(settings, "CLOUDFLARE_R2_BUCKET", "") or DEFAULT_BUCKET
    if not account_id or not token:
        raise AppError("el almacenamiento de fotos no está configurado", 503)
    return CloudflareR2(account_id, token, bucket)


def product_image_id(product_id: int) -> str:
    """Clave estable de la foto de un insumo dentro del bucket."""
    return f"product-{product_id}"


def _success(raw: bytes) -> bool:
    try:
        payload = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        return False
    return bool(payload.get("success"))
