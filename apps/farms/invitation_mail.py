# ruff: noqa: E501  (estilos HTML en línea, heredados de la plantilla anterior)
"""Cuerpo del correo de invitación a una finca (F10.3.3-A).

Propósito: armar el mensaje con el código individual ``XXXXX-XXXXX``, la finca,
el rol ofrecido y la fecha de expiración, en texto plano y HTML.

Contexto: desde F10.3.3-A el correo ya no incluye enlace de aceptación, token,
huella ni identificadores internos. El código solo existe en memoria mientras
se arma y entrega el mensaje; estas funciones no lo registran ni lo guardan.
``sensitive_variables`` lo oculta en los informes de error de Django.
"""
from datetime import datetime
from html import escape

from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from apps.farms.models import FarmRole

SUBJECT = "Tu código de invitación a AGROTECH-T"
GUEST_OPTION = "Entrar como invitado"

_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def role_label(role: str) -> str:
    """Nombre visible del rol (por ejemplo, «Operador»)."""
    try:
        return str(FarmRole(role).label)
    except ValueError:
        return str(role)


def expiry_text(expires_at: datetime) -> str:
    """Fecha de expiración en español, en la zona horaria del proyecto."""
    local = timezone.localtime(expires_at)
    offset = local.strftime("%z")
    utc = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    return (
        f"{local.day} de {_MONTHS[local.month - 1]} de {local.year} "
        f"a las {local:%H:%M} ({utc})"
    )


@sensitive_variables("code")
def plain(*, code: str, farm_name: str, role: str, expires_at: datetime) -> str:
    """Texto plano de la invitación con el código."""
    return (
        "AGROTECH-T\n"
        f"Te invitaron a unirte a la finca {farm_name} con el rol "
        f"{role_label(role)}.\n\n"
        f"Tu código de invitación: {code}\n\n"
        "Para aceptar, entra en AGROTECH-T con este mismo correo. Si aún no "
        "tienes cuenta, regístrate con él y verifícalo. Después elige "
        f"«{GUEST_OPTION}» e introduce el código.\n\n"
        f"El código vence el {expiry_text(expires_at)}.\n"
        "Es individual y de un solo uso. No lo compartas con nadie. Si no "
        "esperabas esta invitación, ignora el mensaje."
    )


@sensitive_variables("code", "safe_code")
def html(*, code: str, farm_name: str, role: str, expires_at: datetime) -> str:
    """Plantilla HTML de la invitación con el código."""
    safe_farm = escape(farm_name)
    safe_role = escape(role_label(role))
    safe_code = escape(code)
    safe_expiry = escape(expiry_text(expires_at))
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Invitación AGROTECH-T</title></head>
<body style="margin:0;padding:0;background:#eef3ec;font-family:Segoe UI,Arial,sans-serif;color:#142b1b;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#eef3ec;padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border-radius:18px;overflow:hidden;border:1px solid #d5e2d0;">
<tr><td style="padding:24px 28px 8px;">
<p style="margin:0 0 8px;font-size:18px;font-weight:700;">Te invitaron a una finca</p>
<p style="margin:0 0 20px;font-size:14px;line-height:1.5;color:#49724a;">
Te invitaron a unirte a la finca <strong>{safe_farm}</strong> con el rol
<strong>{safe_role}</strong>.
</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 22px;">
<tr><td align="center" style="padding:16px 12px;border-radius:14px;background:#f4f8f2;border:1px solid #c5d6c8;">
<p style="margin:0 0 6px;font-size:12px;color:#49724a;">Tu código de invitación</p>
<p style="margin:0;font-family:Consolas,Menlo,monospace;font-size:26px;font-weight:700;letter-spacing:4px;color:#1a3a7a;">{safe_code}</p>
</td></tr></table>
<p style="margin:0 0 12px;font-size:14px;line-height:1.5;">
Para aceptar, entra en AGROTECH-T con este mismo correo. Si aún no tienes
cuenta, regístrate con él y verifícalo. Después elige
<strong>«{GUEST_OPTION}»</strong> e introduce el código.
</p>
<p style="margin:0 0 12px;font-size:13px;color:#49724a;">El código vence el {safe_expiry}.</p>
<p style="margin:0 0 16px;font-size:13px;color:#7a5a12;">Es individual y de un solo uso. No lo compartas con nadie.</p>
</td></tr>
<tr><td style="padding:18px 28px 26px;font-size:12px;color:#819f7c;line-height:1.45;">
AGROTECH-T · mensaje automático, no respondas a este correo. Si no esperabas
esta invitación, ignóralo.
</td></tr>
</table>
</td></tr></table>
</body></html>"""
