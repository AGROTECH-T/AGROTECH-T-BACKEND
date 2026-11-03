"""Matriz completa de permisos por acción y rol.

Propósito: unir las mitades administrativas y operativas.
Contexto: catálogo funcional de Configuración / Farms.
"""

from apps.farms.role_permisos_admin import PERMISOS_ADMIN
from apps.farms.role_permisos_ops import PERMISOS_OPS

PERMISOS = PERMISOS_ADMIN + PERMISOS_OPS
