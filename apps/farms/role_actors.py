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
        "resumen": "Gestiona la administración, los integrantes y la operación de su finca.",
        "funciones": (
            "Consultar y corregir los datos generales de la finca.",
            "Administrar integrantes, invitaciones y roles.",
            "Desactivar la finca y solicitar su reactivación.",
            "Gestionar las actividades productivas habilitadas.",
            "Consultar el historial administrativo y la auditoría de su finca.",
            "Consultar la información financiera según las reglas aprobadas.",
            "Consultar la suscripción y recibir avisos administrativos.",
            "Autorizar el acceso temporal y limitado del soporte.",
        ),
    },
    {
        "id": OPERADOR,
        "nombre": "Operador",
        "resumen": "Registra y actualiza la operación diaria en los módulos habilitados.",
        "funciones": (
            "Consultar la información autorizada de la finca.",
            "Crear registros operativos en los módulos habilitados.",
            "Actualizar registros autorizados conservando la trazabilidad.",
            "Consultar el historial operativo permitido.",
            "Consultar solo los datos financieros necesarios para su operación.",
        ),
    },
    {
        "id": CONSULTA,
        "nombre": "Consulta",
        "resumen": "Revisa información autorizada sin modificarla.",
        "funciones": (
            "Consultar la información autorizada de la finca.",
            "Consultar registros e historiales permitidos.",
            "Consultar sus propias notificaciones.",
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

