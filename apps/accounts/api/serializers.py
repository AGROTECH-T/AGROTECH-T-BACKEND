"""Define los contratos de entrada y salida de la API de cuentas.

@author Cristian Deysdayr Jimenez
"""

from rest_framework import serializers

from apps.accounts.validators import (
    normalize_identification,
    normalize_name,
    normalize_phone,
    validate_password_strength,
)


class RegisterSerializer(serializers.Serializer):
    """Valida los datos requeridos para crear una cuenta."""

    first_name = serializers.CharField(max_length=40)
    last_name = serializers.CharField(max_length=40)
    identification = serializers.CharField(max_length=30)
    phone = serializers.CharField(max_length=30, allow_blank=True, required=False, default="")
    correo = serializers.EmailField(allow_blank=True, required=False, default="")
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs: dict) -> dict:
        """Normaliza la cuenta y exige un canal de recuperación."""
        attrs["first_name"] = normalize_name(attrs["first_name"], "primer nombre")
        attrs["last_name"] = normalize_name(attrs["last_name"], "primer apellido")
        attrs["identification"] = normalize_identification(attrs["identification"])
        attrs["phone"] = normalize_phone(attrs["phone"])
        attrs["correo"] = attrs["correo"].strip().lower()
        attrs["password"] = validate_password_strength(attrs["password"])
        if not attrs["phone"] and not attrs["correo"]:
            raise serializers.ValidationError(
                "coloca un celular o un correo para recuperar la cuenta"
            )
        return attrs


class LoginSerializer(serializers.Serializer):
    """Valida credenciales de inicio de sesión."""

    identification = serializers.CharField(max_length=30)
    password = serializers.CharField(trim_whitespace=False, max_length=72)

    def validate_identification(self, value: str) -> str:
        """Normaliza la identificación."""
        return normalize_identification(value)


class RecoverSerializer(serializers.Serializer):
    """Valida una solicitud de recuperación."""

    identification = serializers.CharField(max_length=30)
    method = serializers.ChoiceField(choices=("whatsapp", "correo"))

    def validate_identification(self, value: str) -> str:
        """Normaliza la identificación."""
        return normalize_identification(value)


class ResetSerializer(serializers.Serializer):
    """Valida el OTP y la contraseña nueva."""

    identification = serializers.CharField(max_length=30)
    code = serializers.RegexField(r".*\d{6}.*", max_length=30)
    password = serializers.CharField(trim_whitespace=False)

    def validate_identification(self, value: str) -> str:
        """Normaliza la identificación."""
        return normalize_identification(value)

    def validate_password(self, value: str) -> str:
        """Valida la contraseña nueva."""
        return validate_password_strength(value)


class TokenResponseSerializer(serializers.Serializer):
    """Documenta la respuesta que contiene un token."""

    token = serializers.CharField()


class AccountResponseSerializer(serializers.Serializer):
    """Documenta los datos visibles de la cuenta autenticada."""

    id = serializers.IntegerField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()
    identification = serializers.CharField()
    phone = serializers.CharField()
    correo = serializers.EmailField()


class SuccessResponseSerializer(serializers.Serializer):
    """Documenta una operación exitosa."""

    ok = serializers.BooleanField()


class RecoveryResponseSerializer(SuccessResponseSerializer):
    """Documenta el envío y vigencia del OTP."""

    wait = serializers.IntegerField()
