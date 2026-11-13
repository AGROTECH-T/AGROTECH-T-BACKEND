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
    email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(blank=True, null=True)
    # Capacidad global de la cuenta (DEC-11): independiente del rol de cada
    # membresía. Solo se concede o retira con los comandos de gestión.
    can_create_farms = models.BooleanField(default=False)

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


class OTPPurpose(models.TextChoices):
    """Propósitos de un código OTP."""

    RECOVERY = "recovery", "Recuperación"
    VERIFICATION = "verification", "Verificación"


class LoginCode(models.Model):
    """Conserva una huella OTP por usuario y propósito."""

    id = models.BigAutoField(primary_key=True, auto_created=True)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="login_codes",
    )
    purpose = models.CharField(
        max_length=20,
        choices=OTPPurpose.choices,
        default=OTPPurpose.RECOVERY,
    )
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    channel = models.CharField(max_length=10)
    sent_at = models.DateTimeField(auto_now=True)
    delivery_pending = models.BooleanField(default=False)
    delivery_reserved_at = models.DateTimeField(blank=True, null=True)
    delivery_confirmed_at = models.DateTimeField(blank=True, null=True)
    delivery_nonce = models.UUIDField(blank=True, null=True)

    class Meta:
        db_table = "login_codes"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "purpose"],
                name="uniq_login_code_user_purpose",
            ),
        ]


class Session(models.Model):
    """Conserva la huella del token vigente y su última actividad."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    session_key = models.CharField(max_length=40, null=True, unique=True)
    token_hash = models.CharField(max_length=64, null=True, unique=True)
    last_seen = models.DateTimeField()
    created_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sessions"
        indexes = [
            models.Index(fields=["user", "closed_at"], name="sessions_user_closed_idx"),
        ]
