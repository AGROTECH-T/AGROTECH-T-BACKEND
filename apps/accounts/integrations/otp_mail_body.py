"""Cuerpo HTML del código enviado solo por correo.

Propósito: armar un mensaje claro, seguro y con la marca AGROTECH-T.
Contexto: Brevo/SMTP; no aplica a SMS ni WhatsApp.
@author Cristian Deysdayr Jimenez
"""

from html import escape


def plain(digits: str, minutes: int) -> str:
    """Texto plano de respaldo para el mismo correo.

    @param digits: Código de seis cifras.
    @param minutes: Minutos de vigencia.
    @returns Mensaje sin formato.
    """
    return (
        f"AGROTECH-T\n"
        f"Tu código de acceso es: {digits}\n"
        f"Caduca en {minutes} minutos.\n"
        "No lo compartas con nadie. Si no pediste este código, ignora el mensaje."
    )


def html(digits: str, minutes: int) -> str:
    """Plantilla HTML del correo de verificación.

    @param digits: Código de seis cifras.
    @param minutes: Minutos de vigencia.
    @returns Documento HTML seguro para clientes de correo.
    """
    code = escape("".join(ch for ch in digits if ch.isdigit())[:6])
    wait = max(1, int(minutes))
    mark = _mark_svg()
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Código AGROTECH-T</title></head>
<body style="margin:0;padding:0;background:#eef3ec;font-family:Segoe UI,Arial,sans-serif;color:#142b1b;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#eef3ec;padding:28px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border-radius:18px;overflow:hidden;border:1px solid #d5e2d0;">
<tr><td style="padding:0;background:#1a3a7a;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">
<tr><td style="height:6px;background:#2c9b45;font-size:0;line-height:0;">&nbsp;</td>
<td style="height:6px;background:#1db5e0;font-size:0;line-height:0;">&nbsp;</td></tr>
</table>
<table role="presentation" cellspacing="0" cellpadding="0" style="padding:20px 24px 16px;">
<tr>
<td style="vertical-align:middle;padding-right:14px;">{mark}</td>
<td style="vertical-align:middle;">
<div style="font-size:22px;font-weight:800;letter-spacing:0.04em;line-height:1.1;">
<span style="color:#ffffff;">AGRO</span><span style="color:#d9ecff;">TECH</span><span style="color:#7fe3ff;">-T</span>
</div>
<div style="margin-top:4px;color:#e8f5ff;font-size:10px;letter-spacing:0.08em;text-transform:uppercase;">
Agricultura con tecnología trabajando juntas
</div>
</td></tr></table>
</td></tr>
<tr><td style="padding:28px 28px 8px;">
<p style="margin:0 0 8px;font-size:18px;font-weight:700;color:#142b1b;">Tu código de acceso</p>
<p style="margin:0 0 22px;font-size:14px;line-height:1.5;color:#49724a;">
Úsalo solo en AGROTECH-T para recuperar el acceso. Nadie del equipo te pedirá este código.
</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin:0 0 22px;">
<tr><td align="center" style="padding:18px 12px;border-radius:14px;background:#f4f8f2;border:1px solid #c5d6c8;">
<div style="font-size:42px;line-height:1.2;font-weight:800;letter-spacing:0.42em;color:#142b1b;font-family:Consolas,Segoe UI,monospace;">
{code}
</div>
</td></tr></table>
<p style="margin:0 0 18px;text-align:center;font-size:14px;color:#1a3a7a;font-weight:600;">
Caduca en {wait} minutos
</p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0">
<tr><td style="padding:14px 16px;border-radius:12px;background:#fff7e8;border:1px solid #f0d9a8;color:#7a5a12;font-size:13px;line-height:1.5;">
<strong>Precaución:</strong> no compartas este código con nadie. Si no solicitaste recuperarlo, ignora este correo.
</td></tr></table>
</td></tr>
<tr><td style="padding:18px 28px 26px;font-size:12px;color:#819f7c;line-height:1.45;">
AGROTECH-T · mensaje automático, no respondas a este correo.
</td></tr>
</table>
</td></tr></table>
</body></html>"""


def _mark_svg() -> str:
    """Marca compacta en SVG (inspirada en el emblema, no una foto pegada)."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="52" height="52" viewBox="0 0 64 64" role="img" aria-label="AGROTECH-T">'
        '<circle cx="32" cy="32" r="30" fill="#0f2a1c"/>'
        '<path d="M32 14v28M24 42c4-10 8-14 8-14s4 4 8 14" fill="none" stroke="#3ecf5a" stroke-width="3" stroke-linecap="round"/>'
        '<path d="M34 22h10M34 28h14M34 34h8" stroke="#2ea8d8" stroke-width="2" stroke-linecap="round"/>'
        '<circle cx="44" cy="22" r="2" fill="#7fe3ff"/><circle cx="48" cy="28" r="2" fill="#7fe3ff"/>'
        '<path d="M22 20c-2 3-3 6-2 9 3-1 6-4 7-8-2 0-4-1-5-1z" fill="#6edc67"/>'
        "</svg>"
    )
