"""HTTP routes limited to the accounts/farms/IoT disposable test profile."""
from django.urls import include, path

urlpatterns = [path("api/v1/iot/", include("apps.iot.api.urls"))]
