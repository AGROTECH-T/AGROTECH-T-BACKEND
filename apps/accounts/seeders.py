"""Usuarios que el sistema siembra al arrancar.

Propósito: dejar en el repositorio las cuentas iniciales, no en .env.example.
Contexto: cada fila se crea o actualiza en cada arranque. El superadmin
nombra administradores de plataforma; administrador/operador/consulta/
invitado alimentan la finca demo K11 (ver seed_demo_farm).
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
    {
        "identification": "1000000002",
        "password": "AdminK11!1",
        "first_name": "Admin",
        "last_name": "Sena",
        "correo": "admin.k11@example.com",
        "phone": "3001000002",
        "is_superadmin": False,
        "is_administrador": True,
    },
    {
        "identification": "1000000003",
        "password": "OperadorK11!1",
        "first_name": "Operador",
        "last_name": "Sena",
        "correo": "operador.k11@example.com",
        "phone": "3001000003",
        "is_superadmin": False,
        "is_administrador": False,
    },
    {
        "identification": "1000000004",
        "password": "ConsultaK11!1",
        "first_name": "Consulta",
        "last_name": "Sena",
        "correo": "consulta.k11@example.com",
        "phone": "3001000004",
        "is_superadmin": False,
        "is_administrador": False,
    },
    {
        "identification": "1000000005",
        "password": "InvitadoK11!1",
        "first_name": "Invitado",
        "last_name": "Sena",
        "correo": "invitado.k11@example.com",
        "phone": "3001000005",
        "is_superadmin": False,
        "is_administrador": False,
    },
)

# Documentos de las cuentas de la finca demo (el invitado no se asocia).
DEMO_ADMIN_ID = "1000000002"
DEMO_OPERADOR_ID = "1000000003"
DEMO_CONSULTA_ID = "1000000004"
DEMO_INVITADO_ID = "1000000005"
