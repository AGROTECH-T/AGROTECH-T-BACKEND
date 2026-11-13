"""Reglas transversales y pendientes del catálogo de roles.

Propósito: documentar reglas de autorización y puntos abiertos.
Contexto: catálogo funcional de Configuración / Farms.
"""

REGLAS = (
    {
        "numero": 1,
        "titulo": "Validación en el backend",
        "detalle": "Cada solicitud comprueba la autorización; no basta con ocultar botones.",
    },
    {
        "numero": 2,
        "titulo": "Aislamiento entre fincas",
        "detalle": "El rol de una finca no da acceso a otra; se valida contra la finca elegida.",
    },
    {
        "numero": 3,
        "titulo": "Un rol por membresía",
        "detalle": "Cada cuenta tiene un solo rol en cada finca; cambiarlo reemplaza el anterior.",
    },
    {
        "numero": 4,
        "titulo": "Permisos por rol, no sueltos",
        "detalle": "Los permisos vienen del rol y no se asignan a una persona por separado.",
    },
    {
        "numero": 5,
        "titulo": "Cambios efectivos",
        "detalle": "Un cambio de rol se aplica desde la siguiente solicitud al backend.",
    },
    {
        "numero": 6,
        "titulo": "Último administrador",
        "detalle": "No se retira ni degrada al último administrador activo de la finca.",
    },
    {
        "numero": 7,
        "titulo": "Solo fincas administradas",
        "detalle": "El administrador cambia roles únicamente en las fincas donde tiene ese rol.",
    },
    {
        "numero": 8,
        "titulo": "Denegación segura",
        "detalle": "Los mensajes de acceso denegado no revelan información privada de la finca.",
    },
)

PENDIENTES = (
    "Acceso temporal y limitado del soporte técnico.",
    "Suscripciones, facturación y avisos de pago.",
)
