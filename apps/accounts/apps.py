"""Dominio de cuentas y autenticación de AGROTECH-T.

Propósito: registrar usuarios, sesiones y recuperación de acceso.
Contexto: autenticación propia, aparte del usuario interno de Django admin.
@author Cristian Deysdayr Jimenez
"""
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Declara el dominio de cuentas."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Accounts"
