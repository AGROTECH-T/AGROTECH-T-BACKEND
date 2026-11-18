"""Configuración de desarrollo local con envío REAL de correo por Brevo.

Propósito: probar en local la entrega real de correos (verificación e
invitaciones) sin tocar ``config.settings_local``, que sigue enviando el correo
solo a la consola.

Contexto: hereda directamente ``config.settings`` (que ya cargó ``.env``) y
NO importa ``config.settings_local``. Conserva sus garantías de desarrollo:
- ``DEBUG=True`` obligatorio.
- Redis real obligatorio (``REDIS_URL`` y ``django_redis.cache.RedisCache``).
- ``INVITATION_CODE_HMAC_KEY`` de al menos 32 caracteres y distinta de
  ``SECRET_KEY``.

A diferencia de ``config.settings_local``: no retira ``BREVO_API_KEY`` del
entorno ni redefine ningún ajuste de correo. ``apps.accounts.integrations.
delivery`` usará la API de Brevo si ``BREVO_API_KEY`` está en el entorno y, si
no, el SMTP heredado de ``config.platform``. Los correos SALEN del equipo:
usar solo con direcciones propias.

No usar en producción: exige ``DEBUG=True``.
@author Cristian Deysdayr Jimenez
"""
import os

from config.local_dev_guard import validate_local_settings
from config.settings import *  # noqa: F401,F403
from config.settings import CACHES, DEBUG, INVITATION_CODE_HMAC_KEY, SECRET_KEY

validate_local_settings(
    debug=DEBUG,
    caches=CACHES,
    hmac_key=INVITATION_CODE_HMAC_KEY,
    secret_key=SECRET_KEY,
    environ=os.environ,
)
