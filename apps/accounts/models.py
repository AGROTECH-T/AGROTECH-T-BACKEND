"""Modelos persistentes de usuario, código y sesión.

@author Cristian Deysdayr Jimenez
"""
from django.db import models


class User(models.Model):
    """Representa una cuenta identificada por documento."""

    first_name = models.CharField(max_length=40)
    last_name = models.CharField(max_length=40)
    identification = models.CharField(max_length=15, unique=True)
    phone = models.CharField(max_length=15, blank=True)
    correo = models.EmailField(max_length=254, blank=True)
    password_hash = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(blank=True, null=True)

    @property
    def is_authenticated(self) -> bool:
        """Indica a DRF que la cuenta fue autenticada."""
        return True

    @property
    def is_anonymous(self) -> bool:
        """Indica a DRF que la cuenta no es anónima."""
        return False

    class Meta:
        db_table = "users"


class LoginCode(models.Model):
    """Conserva una sola huella OTP activa por usuario."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    channel = models.CharField(max_length=10)
    sent_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "login_codes"


class Session(models.Model):
    """Conserva la huella del token vigente y su última actividad."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)
    last_seen = models.DateTimeField()
    token_hash = models.CharField(max_length=64, unique=True)

    class Meta:
        db_table = "sessions"
