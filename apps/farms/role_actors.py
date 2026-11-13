"""Constantes y actores del catálogo de roles Farms.

Propósito: definir estados, roles y actores especiales.
Contexto: datos de referencia para Configuración.
"""

PERMITIDO = "permitido"
NO_PERMITIDO = "no_permitido"
CONDICIONADO = "condicionado"
PENDIENTE = "pendiente"
NO_REQUIERE = "no_requiere"

ESTADOS = (PERMITIDO, NO_PERMITIDO, CONDICIONADO, PENDIENTE, NO_REQUIERE)

ADMINISTRADOR = "administrador"
OPERADOR = "operador"
CONSULTA = "consulta"

ROLES = (
    {
        "id": ADMINISTRADOR,
        "nombre": "Administrador de finca",
        "resumen": "Gestiona la finca, sus integrantes y toda la operación.",
        "funciones": (
            "Consultar sus fincas y corregir el nombre de las que administra.",
            "Administrar integrantes y cambiar el rol de cada persona.",
            "Registrar entradas, salidas y préstamos del inventario.",
            "Ajustar el stock y gestionar productos y unidades.",
            "Operar el galpón: galpones, lotes, bitácora y cierre del ciclo.",
            "Consultar y exportar los reportes.",
        ),
    },
    {
        "id": OPERADOR,
        "nombre": "Operador",
        "resumen": "Registra la operación diaria sin administrar la finca.",
        "funciones": (
            "Consultar las fincas donde tiene membresía.",
            "Registrar entradas, salidas y préstamos del inventario.",
            "Crear unidades de medida propias de la finca.",
            "Operar el galpón: galpones, lotes, bitácora y cierre del ciclo.",
            "Consultar y exportar los reportes.",
        ),
    },
    {
        "id": CONSULTA,
        "nombre": "Consulta",
        "resumen": "Revisa la información de la finca sin modificarla.",
        "funciones": (
            "Consultar las fincas donde tiene membresía.",
            "Consultar productos, stock y movimientos del inventario.",
            "Consultar galpones, lotes, bitácora e indicadores.",
            "Consultar y exportar los reportes.",
        ),
    },
)

ACTORES = (
    {
        "id": "responsable_pago",
        "nombre": "Responsable del pago",
        "resumen": "Persona asociada a la suscripción; no obtiene permisos administrativos.",
        "funciones": (
            "Consultar el estado y la vigencia de la suscripción.",
            "Recibir notificaciones de pagos, facturas y renovación.",
            "Gestionar las acciones de pago del futuro módulo de suscripciones.",
        ),
    },
    {
        "id": "soporte",
        "nombre": "Soporte de AGROTECH-T",
        "resumen": "Acceso temporal, limitado y autorizado expresamente.",
        "funciones": (
            "Atender una recuperación excepcional con verificación de identidad.",
            "Actuar solo con autorización del administrador y registro en auditoría.",
            "No convertirse automáticamente en administrador de la finca.",
        ),
    },
)

