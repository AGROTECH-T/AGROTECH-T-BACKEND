"""Mitad operativa de la matriz de permisos.

Propósito: describir lo que cada rol puede hacer en inventario, galpón y
reportes, tal como lo valida el backend.
Contexto: el catálogo es de lectura; el rol se guarda en la membresía.
"""

from apps.farms.role_actors import (
    ADMINISTRADOR,
    CONSULTA,
    NO_PERMITIDO,
    OPERADOR,
    PERMITIDO,
)

PERMISOS_OPS = (
    {
        "id": "ver_inventario",
        "nombre": "Consultar el inventario",
        "modulo": "Inventario",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Productos, stock y movimientos."},
            OPERADOR: {"estado": PERMITIDO, "detalle": "Productos, stock y movimientos."},
            CONSULTA: {"estado": PERMITIDO, "detalle": "Productos, stock y movimientos."},
        },
    },
    {
        "id": "registrar_movimientos",
        "nombre": "Registrar entradas y salidas",
        "modulo": "Inventario",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Entradas, salidas y préstamos."},
            OPERADOR: {"estado": PERMITIDO, "detalle": "Entradas, salidas y préstamos."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "Solo lectura."},
        },
    },
    {
        "id": "crear_unidades",
        "nombre": "Crear unidades de medida",
        "modulo": "Inventario",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Unidades propias de la finca."},
            OPERADOR: {"estado": PERMITIDO, "detalle": "Unidades propias de la finca."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "Solo lectura."},
        },
    },
    {
        "id": "ajustar_stock",
        "nombre": "Ajustar el stock",
        "modulo": "Inventario",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Con motivo registrado."},
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "Solo el administrador."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "Solo el administrador."},
        },
    },
    {
        "id": "gestionar_productos",
        "nombre": "Crear y editar productos",
        "modulo": "Inventario",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Productos y sus mínimos."},
            OPERADOR: {"estado": NO_PERMITIDO, "detalle": "Solo el administrador."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "Solo el administrador."},
        },
    },
    {
        "id": "ver_reportes",
        "nombre": "Consultar y exportar reportes",
        "modulo": "Reportes",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Reportes y archivos del inventario."},
            OPERADOR: {"estado": PERMITIDO, "detalle": "Reportes y archivos del inventario."},
            CONSULTA: {"estado": PERMITIDO, "detalle": "Reportes y archivos del inventario."},
        },
    },
    {
        "id": "ver_galpon",
        "nombre": "Consultar galpones y lotes",
        "modulo": "Galpón",
        "por_rol": {
            ADMINISTRADOR: {
                "estado": PERMITIDO,
                "detalle": "Galpones, lotes, bitácora e indicadores.",
            },
            OPERADOR: {"estado": PERMITIDO, "detalle": "Galpones, lotes, bitácora e indicadores."},
            CONSULTA: {"estado": PERMITIDO, "detalle": "Galpones, lotes, bitácora e indicadores."},
        },
    },
    {
        "id": "registrar_galpon",
        "nombre": "Registrar en el galpón",
        "modulo": "Galpón",
        "por_rol": {
            ADMINISTRADOR: {
                "estado": PERMITIDO,
                "detalle": "Galpones, recepción de lotes y bitácora.",
            },
            OPERADOR: {"estado": PERMITIDO, "detalle": "Galpones, recepción de lotes y bitácora."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "Solo lectura."},
        },
    },
    {
        "id": "cerrar_ciclo",
        "nombre": "Cerrar el ciclo y sus avisos",
        "modulo": "Galpón",
        "por_rol": {
            ADMINISTRADOR: {"estado": PERMITIDO, "detalle": "Cierre del lote y avisos atendidos."},
            OPERADOR: {"estado": PERMITIDO, "detalle": "Cierre del lote y avisos atendidos."},
            CONSULTA: {"estado": NO_PERMITIDO, "detalle": "Solo lectura."},
        },
    },
)
