"""Configuración de desarrollo local para pruebas manuales en el navegador (cierre F10.3).

Propósito: arrancar el backend contra la base local ``agrotech_t`` con Redis
real y sin enviar correos a nadie.

Contexto: hereda ``config.settings`` (que ya cargó ``.env``) y añade tres
garantías para desarrollo:
- **Redis obligatorio.** Sin ``REDIS_URL`` la caché sería ``LocMemCache`` y el
  contador de intentos y los throttles no se compartirían entre procesos; aquí
  eso es un error de arranque, no una degradación silenciosa.
- **Clave HMAC obligatoria.** ``INVITATION_CODE_HMAC_KEY`` debe existir, tener
  al menos 32 caracteres y ser distinta de ``SECRET_KEY``.
- **Correo solo en consola.** ``apps.accounts.integrations.delivery`` usa Brevo
  si ``BREVO_API_KEY`` está en el entorno del proceso y, si no, SMTP con
  ``EMAIL_BACKEND``. Aquí se retira ``BREVO_API_KEY`` del entorno de ESTE
  proceso (no de ``.env``) y el backend de correo es el de consola: los correos
  de verificación e invitación, con su código, se escriben en la consola de
  ``runserver`` y no salen del equipo.

No usar en producción: exige ``DEBUG=True``.
@author Cristian Deysdayr Jimenez
"""
import os

from config.local_dev_guard import disable_external_email, validate_local_settings
from config.settings import *  # noqa: F401,F403
from config.settings import CACHES, DEBUG, INVITATION_CODE_HMAC_KEY, SECRET_KEY

LOCAL_CONSOLE_EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
LOCAL_EMAIL_USER_PLACEHOLDER = "consola-local"

validate_local_settings(
    debug=DEBUG,
    caches=CACHES,
    hmac_key=INVITATION_CODE_HMAC_KEY,
    secret_key=SECRET_KEY,
    environ=os.environ,
)
disable_external_email(os.environ)

EMAIL_BACKEND = LOCAL_CONSOLE_EMAIL_BACKEND
# delivery._deliver_smtp exige EMAIL_HOST y EMAIL_HOST_USER; con el backend de
# consola no se abre ninguna conexión ni se usa contraseña.
EMAIL_HOST_USER = LOCAL_EMAIL_USER_PLACEHOLDER
EMAIL_HOST_PASSWORD = ""
