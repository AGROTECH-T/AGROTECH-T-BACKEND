"""Organiza los endpoints oficiales y versionados de cuentas.

@author Cristian Deysdayr Jimenez
"""

from django.urls import path

from apps.accounts.api.confirm_view import ConfirmCodeView
from apps.accounts.api.views import (
    LoginView,
    LogoutView,
    MeView,
    RecoverView,
    RegisterView,
    RenewView,
    ResetView,
)

app_name = "accounts"

urlpatterns = [
    path("accounts/", RegisterView.as_view(), name="account-create"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/password/recovery/", RecoverView.as_view(), name="password-recovery"),
    path("auth/password/confirm/", ConfirmCodeView.as_view(), name="password-confirm"),
    path("auth/password/reset/", ResetView.as_view(), name="password-reset"),
    path("auth/session/renew/", RenewView.as_view(), name="session-renew"),
]
