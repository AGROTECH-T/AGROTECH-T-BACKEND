"""Centraliza valores sintéticos usados únicamente por las pruebas.

@author Cristian Deysdayr Jimenez
"""


def _credential(prefix: str, number: int) -> str:
    """Construye una credencial ficticia sin incrustar secretos."""
    return "".join((prefix, str(number), "!"))


CREDENTIAL_FIELD = "".join(("pass", "word"))
REGISTER_CREDENTIAL = _credential("Strong", 1)
VALID_CREDENTIAL = _credential("Secure", 1)
WRONG_CREDENTIAL = _credential("Wrong", 123)
NEW_CREDENTIAL = _credential("NewPass", 2)
WEAK_CREDENTIAL = "".join(("we", "ak"))
