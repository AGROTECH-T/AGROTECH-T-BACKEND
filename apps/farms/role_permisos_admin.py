"""Mitad administrativa de la matriz de permisos.

Propósito: describir lo que cada rol puede hacer en fincas, integrantes y
configuración, tal como lo valida el backend.
Contexto: el catálogo es de lectura; el rol se guarda en la membresía.
"""

from apps.farms.role_actors import (
    ADMINISTRADOR,
    CONSULTA,
    NO_PERMITIDO,
    OPERADOR,
    PERMITIDO,
)

PERMISOS_ADMIN = (
    {
        "id": "crear_finca",
        "nombre": "Crear una finca",
        "modulo": "Fincas",
        "por_rol": {
            ADMINISTRADOR: {
                "estado": PERMITIDO,
                "detalle": "Solo el administrador nombrado por el super administrador.",
            },
            OPERADOR: {
                "estado": NO_PERMITIDO,
                "detalle": "Solo el administrador nombrado por el super administrador.",
            },
            CONSULTA: {
                "estado": NO_PERMITIDO,
                "detalle": "Solo el administrador nombrado por el super administrador.",
            },
        },
    },
    {
        "id": "consultar_fincas",
        "nombre": "Consultar sus fincas",
        "modulo": "Fincas",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Solo las fincas con membresía."},
            OPERADOR: {"estado": PERMITIDO, "detalle": "Solo las fincas con membresía."},
            CONSULTA: {"estado": PERMITIDO, "detalle": "Solo las fincas con membresía."},
        },
    },
    {
        "id": "editar_finca",
        "nombre": "Corregir los datos de la finca",
        "modulo": "Fincas",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Nombre de la finca."},
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "Solo el administrador."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "Solo el administrador."},
        },
    },
    {
        "id": "eliminar_finca",
        "nombre": "Eliminar la finca",
        "modulo": "Fincas",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Borra también sus membresías."},
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "Solo el administrador."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "Solo el administrador."},
        },
    },
    {
        "id": "consultar_personas",
        "nombre": "Consultar las personas de la finca",
        "modulo": "Configuración",
        "por_rol": {
            ADMINISTRADOR: {
                "estado": PERMITIDO,
                "detalle": "Personas de las fincas que administra.",
            },
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "La API responde sin personas."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "La API responde sin personas."},
        },
    },
    {
        "id": "cambiar_roles",
        "nombre": "Cambiar el rol de una persona",
        "modulo": "Configuración",
        "por_rol": {
            ADMINISTRADOR: {
                "estado": PERMITIDO,
                "detalle": "Solo en fincas que administra y conservando un administrador activo.",
            },
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "Solo el administrador de la finca."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "Solo el administrador de la finca."},
        },
    },
)
