"""Rutas del dominio de fincas.

Propósito: publicar los recursos de fincas bajo el prefijo versionado.
Contexto: se incluyen desde config.api_urls junto a los demás dominios.
"""
from django.urls import path

from apps.farms.views import (
    FarmDetailView,
    FarmHistoryView,
    FarmInvitationAcceptView,
    FarmInvitationCodeAcceptView,
    FarmInvitationCodePreviewView,
    FarmInvitationDetailView,
    FarmInvitationListCreateView,
    FarmInvitationResendView,
    FarmLeaveView,
    FarmListCreateView,
    FarmMemberDetailView,
    FarmMemberHistoryView,
    FarmMemberListView,
    FarmReactivateView,
    FarmTransferView,
)

app_name = "farms"

urlpatterns = [
    path("farms/", FarmListCreateView.as_view(), name="farm-list-create"),
    path("farms/<int:farm_id>/", FarmDetailView.as_view(), name="farm-detail"),
    path("farms/<int:farm_id>/history/", FarmHistoryView.as_view(), name="farm-history"),
    path(
        "farms/<int:farm_id>/reactivate/",
        FarmReactivateView.as_view(),
        name="farm-reactivate",
    ),
    path("farms/<int:farm_id>/members/", FarmMemberListView.as_view(), name="farm-members"),
    path(
        "farms/<int:farm_id>/members/<int:membership_id>/",
        FarmMemberDetailView.as_view(),
        name="farm-member-detail",
    ),
    path(
        "farms/<int:farm_id>/members/<int:membership_id>/transfer/",
        FarmTransferView.as_view(),
        name="farm-member-transfer",
    ),
    path(
        "farms/<int:farm_id>/members/<int:membership_id>/history/",
        FarmMemberHistoryView.as_view(),
        name="farm-member-history",
    ),
    path("farms/<int:farm_id>/leave/", FarmLeaveView.as_view(), name="farm-leave"),
    path(
        "farms/<int:farm_id>/invitations/",
        FarmInvitationListCreateView.as_view(),
        name="farm-invitations",
    ),
    path(
        "farms/<int:farm_id>/invitations/<int:invitation_id>/",
        FarmInvitationDetailView.as_view(),
        name="farm-invitation-detail",
    ),
    path(
        "farms/<int:farm_id>/invitations/<int:invitation_id>/resend/",
        FarmInvitationResendView.as_view(),
        name="farm-invitation-resend",
    ),
    path("invitations/accept/", FarmInvitationAcceptView.as_view(), name="invitation-accept"),
    path(
        "invitations/code/preview/",
        FarmInvitationCodePreviewView.as_view(),
        name="invitation-code-preview",
    ),
    path(
        "invitations/code/accept/",
        FarmInvitationCodeAcceptView.as_view(),
        name="invitation-code-accept",
    ),
]
