"""Agrupa alias temporales de clientes que usan rutas anteriores.

@author Cristian Deysdayr Jimenez
"""

from django.urls import path

from apps.accounts.api.views import LoginView, RecoverView, RegisterView, RenewView, ResetView

urlpatterns = [
    path("api/v1/auth/register/", RegisterView.as_view()),
    path("api/v1/registro", RegisterView.as_view()),
    path("api/v1/login", LoginView.as_view()),
    path("api/v1/recuperar", RecoverView.as_view()),
    path("api/v1/recuperar/codigo", ResetView.as_view()),
    path("api/v1/sesion", RenewView.as_view()),
    path("registro", RegisterView.as_view()),
    path("login", LoginView.as_view()),
    path("recuperar", RecoverView.as_view()),
    path("recuperar/codigo", ResetView.as_view()),
    path("sesion", RenewView.as_view()),
]
