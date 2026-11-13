"""Usuarios que el sistema siembra al arrancar.

Propósito: dejar en el repositorio las cuentas iniciales, no en .env.example.
Contexto: cada fila se crea o actualiza en cada arranque.
@author Cristian Deysdayr Jimenez
"""

# ruff: noqa: S105
USUARIOS_INICIALES = (
    {
        "identification": "1000000001",
        "password": "SuperAdmin1!",
        "first_name": "Super",
        "last_name": "Admin",
        "correo": "",
        "phone": "",
        "is_superadmin": True,
        "is_administrador": False,
    },
)
