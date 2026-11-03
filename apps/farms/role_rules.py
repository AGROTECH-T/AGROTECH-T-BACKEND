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
        "titulo": "Rol por membresía",
        "detalle": "El rol pertenece a la relación entre cuenta y finca, no a la cuenta global.",
    },
    {
        "numero": 4,
        "titulo": "Cambios efectivos",
        "detalle": "Un cambio de rol se aplica desde la siguiente solicitud al backend.",
    },
    {
        "numero": 5,
        "titulo": "Membresía desactivada",
        "detalle": "Pierde el acceso y se conserva la atribución de sus acciones previas.",
    },
    {
        "numero": 6,
        "titulo": "Último administrador",
        "detalle": "No se retira ni degrada al último administrador activo de la finca.",
    },
    {
        "numero": 7,
        "titulo": "Sin eliminación permanente ordinaria",
        "detalle": "Las operaciones normales no borran definitivamente los registros históricos.",
    },
    {
        "numero": 8,
        "titulo": "Confirmaciones y auditoría",
        "detalle": "Los cambios sensibles piden confirmación y dejan trazabilidad.",
    },
    {
        "numero": 9,
        "titulo": "Denegación segura",
        "detalle": "Los mensajes de acceso denegado no revelan información privada de la finca.",
    },
    {
        "numero": 10,
        "titulo": "Estado y suscripción separados",
        "detalle": "La autorización considera estado de la finca, suscripción, membresía y rol.",
    },
)

PENDIENTES = (
    "Alcance exacto de la consulta de datos generales por parte del operador.",
    "Permisos concretos para agregar y retirar actividades productivas.",
    "Permisos de creación y actualización de registros por módulo.",
    "Excepciones a la regla de no eliminación de registros.",
    "Consulta del estado de suscripción por parte del operador y del rol Consulta.",
    "Permisos de exportación de información no financiera.",
    "Permisos específicos de edición financiera.",
    "Procedimiento técnico de autorización temporal y recuperación excepcional por soporte.",
    "Política de conservación de auditorías y notificaciones.",
)


