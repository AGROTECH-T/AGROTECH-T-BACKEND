"""Dominio accounts (esqueleto, sin lógica).

Responsabilidad futura: autenticación de AGROTECH-T adaptada a Django
(responsable: Cristian). No implementar login/registro/OTP en esta fase.
"""
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Accounts"
