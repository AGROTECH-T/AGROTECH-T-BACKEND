"""Prueba el cuerpo HTML del correo OTP.

@author Cristian Deysdayr Jimenez
"""

from apps.accounts.integrations.otp_mail_body import html, plain


def test_otp_mail_includes_code_expiry_and_warning():
    """El correo muestra código grande, vigencia y precaución."""
    text = plain("654321", 5)
    rich = html("654321", 5)
    assert "654321" in text
    assert "5 minutos" in text
    assert "No lo compartas" in text
    assert "654321" in rich
    assert "Caduca en 5 minutos" in rich
    assert "Precaución" in rich
    assert "AGROTECH" in rich
    assert "<img" not in rich.lower()


def test_otp_mail_escapes_unexpected_input():
    """Solo se publican dígitos seguros en el HTML."""
    rich = html("<script>12</script>3456", 3)
    assert "<script>" not in rich
    assert "123456" in rich
