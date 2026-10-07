"""Define errores seguros del dominio de cuentas.

@author Cristian Deysdayr Jimenez
"""


class AppError(Exception):
    """Representa un error esperado que puede mostrarse al cliente."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        **extra,
    ):
        """Inicializa el error con estado HTTP y datos adicionales."""
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.extra = extra
