"""Dominio transversal de permisos configurables de AGROTECH-T.

Propósito: centralizar el catálogo de permisos operativos y las reglas de
techos por rol, sin persistencia propia en esta etapa.

Contexto: los permisos estructurales y de autogestión quedan fuera del catálogo
operativo; se gestionan en sus servicios de dominio.
"""
from django.apps import AppConfig


class PermissionsConfig(AppConfig):
    """Declara el dominio de permisos."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.permissions"
    label = "permissions"
    verbose_name = "Permissions"
