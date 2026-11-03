"""Segunda mitad de la matriz de permisos Farms.

Propósito: acciones operativas, financieras y de soporte.
Contexto: catálogo funcional de Configuración.
"""

from apps.farms.role_actors import (
    ADMINISTRADOR,
    CONDICIONADO,
    CONSULTA,
    NO_PERMITIDO,
    OPERADOR,
    PENDIENTE,
    PERMITIDO,
)

PERMISOS_OPS = (
    {
        "id": "gestionar_actividades",
        "nombre": "Gestionar actividades productivas",
        "por_rol": {
            ADMINISTRADOR: {"estado": PENDIENTE, "detalle": "Pendiente de delimitar."},
            OPERADOR: {"estado": PENDIENTE, "detalle": "Pendiente de delimitar."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
    {
        "id": "crear_registros",
        "nombre": "Crear registros operativos",
        "por_rol": {
            ADMINISTRADOR: {"estado": CONDICIONADO, "detalle": "Según los permisos del módulo."},
            OPERADOR: {"estado": PERMITIDO, "detalle": "En módulos autorizados."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
    {
        "id": "actualizar_registros",
        "nombre": "Actualizar registros operativos",
        "por_rol": {
            ADMINISTRADOR: {"estado": CONDICIONADO, "detalle": "Según los permisos del módulo."},
            OPERADOR: {"estado": PERMITIDO, "detalle": "En módulos autorizados."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
    {
        "id": "eliminar_registros",
        "nombre": "Eliminar registros",
        "por_rol": {
            ADMINISTRADOR: {
                "estado": NO_PERMITIDO,
                "detalle": "No concedido por defecto; requiere definición específica.",
            },
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "No concedido por defecto."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
    {
        "id": "consultar_historial",
        "nombre": "Consultar historial operativo",
        "por_rol": {
            ADMINISTRADOR: {"estado": CONDICIONADO, "detalle": "Según permisos y estado."},
            OPERADOR: {"estado": CONDICIONADO, "detalle": "Según permisos y estado."},
            CONSULTA: {"estado": CONDICIONADO, "detalle": "Según permisos y estado."},
        },
    },
    {
        "id": "consultar_auditoria",
        "nombre": "Consultar auditoría administrativa",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Como administrador actual."},
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
    {
        "id": "exportar_informacion_general",
        "nombre": "Exportar información general",
        "por_rol": {
            ADMINISTRADOR: {"estado": CONDICIONADO, "detalle": "Permiso independiente."},
            OPERADOR: {"estado": CONDICIONADO, "detalle": "Permiso independiente."},
            CONSULTA: {"estado": CONDICIONADO, "detalle": "Permiso independiente."},
        },
    },
    {
        "id": "exportar_informacion_financiera",
        "nombre": "Exportar información financiera",
        "por_rol": {
            ADMINISTRADOR: {
                "estado": CONDICIONADO,
                "detalle": "Solo administrador, según propuesta.",
            },
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "No, según propuesta."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No, según propuesta."},
        },
    },
    {
        "id": "consultar_suscripcion",
        "nombre": "Consultar estado de suscripción",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Estado y vigencia de la suscripción."},
            OPERADOR: {"estado": PENDIENTE, "detalle": "Pendiente de definición."},
            CONSULTA: {"estado": PENDIENTE, "detalle": "Pendiente de definición."},
        },
    },
    {
        "id": "cambiar_responsable_pago",
        "nombre": "Cambiar responsable del pago",
        "por_rol": {
            ADMINISTRADOR: {
                "estado": CONDICIONADO,
                "detalle": "Sujeto a la definición del módulo de facturación.",
            },
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
    {
        "id": "autorizar_soporte",
        "nombre": "Autorizar acceso temporal del soporte",
        "por_rol": {
            ADMINISTRADOR: {"estado": CONDICIONADO, "detalle": "Como administrador autorizado."},
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "No concedido."},
        },
    },
)
