from django.urls import path

from apps.mock_provider.views import MockChargeView, MockStatusView

app_name = "mock_provider"

urlpatterns = [
    path("charge/", MockChargeView.as_view(), name="mock_charge"),
    path("payments/<str:provider_ref>/", MockStatusView.as_view(), name="mock_status"),
]
