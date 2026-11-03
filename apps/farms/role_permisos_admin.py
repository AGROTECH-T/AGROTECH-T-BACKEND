"""Primera mitad de la matriz de permisos Farms.

Propósito: acciones administrativas y de membresía.
Contexto: catálogo funcional de Configuración.
"""

from apps.farms.role_actors import (
    ADMINISTRADOR,
    CONDICIONADO,
    CONSULTA,
    NO_PERMITIDO,
    NO_REQUIERE,
    OPERADOR,
    PENDIENTE,
    PERMITIDO,
)

PERMISOS_ADMIN = (
    {
        "id": "crear_finca",
        "nombre": "Crear una finca",
        "por_rol": {
            ADMINISTRADOR: {
                "estado": PERMITIDO,
                "detalle": "Con cuenta registrada y correo verificado.",
            },
            OPERADOR: {"estado": NO_REQUIERE, "detalle": "No requiere rol de finca para crearla."},
            CONSULTA: {"estado": NO_REQUIERE, "detalle": "No requiere rol de finca para crearla."},
        },
    },
    {
        "id": "consultar_fincas",
        "nombre": "Consultar fincas propias",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Con membresía activa."},
            OPERADOR: {"estado": PERMITIDO, "detalle": "Con membresía activa."},
            CONSULTA: {"estado": PERMITIDO, "detalle": "Con membresía activa."},
        },
    },
    {
        "id": "consultar_datos_generales",
        "nombre": "Consultar datos generales",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Datos generales de la finca."},
            OPERADOR: {"estado": PENDIENTE, "detalle": "Pendiente de delimitar."},
            CONSULTA: {"estado": CONDICIONADO, "detalle": "Solo información autorizada."},
        },
    },
    {
        "id": "editar_datos_generales",
        "nombre": "Editar nombre y ubicación",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Datos generales de la finca."},
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
    {
        "id": "administrar_integrantes",
        "nombre": "Administrar integrantes",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Integrantes de su finca."},
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
    {
        "id": "gestionar_invitaciones",
        "nombre": "Crear o cancelar invitaciones",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Invitaciones de su finca."},
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
    {
        "id": "cambiar_roles",
        "nombre": "Cambiar roles",
        "por_rol": {
            ADMINISTRADOR: {
                "estado": PERMITIDO,
                "detalle": "Respetando el último administrador activo.",
            },
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
    {
        "id": "retirar_integrantes",
        "nombre": "Retirar integrantes",
        "por_rol": {
            ADMINISTRADOR: {"estado": CONDICIONADO, "detalle": "Con restricciones."},
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
    {
        "id": "cambiar_estado_finca",
        "nombre": "Cambiar el estado de la finca",
        "por_rol": {
            ADMINISTRADOR: {"estado": CONDICIONADO, "detalle": "Sujeto a restricciones."},
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
    {
        "id": "reactivar_finca",
        "nombre": "Reactivar una finca",
        "por_rol": {
            ADMINISTRADOR: {
                "estado": CONDICIONADO,
                "detalle": "Si cumple las condiciones y confirma la acción.",
            },
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
)
