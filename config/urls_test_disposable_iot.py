"""HTTP routes limited to the accounts/farms/IoT disposable test profile."""
from django.urls import include, path

urlpatterns = [
    path("api/v1/", include("apps.accounts.api.urls")),
    path("api/v1/", include("apps.farms.urls")),
    path("api/v1/", include("apps.farms.api.urls")),
    path("api/v1/iot/", include("apps.iot.api.urls")),
    path("", include("apps.accounts.api.legacy_urls")),
]
