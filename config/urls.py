from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.core.views import HealthCheckView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", HealthCheckView.as_view(), name="health_check"),
    path("healthz/", HealthCheckView.as_view(), name="health_check_slash"),
    # OpenAPI Documentation
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    # Domain APIs
    path("api/v1/auth/", include("apps.accounts.urls", namespace="accounts")),
    path("api/v1/catalog/", include("apps.catalog.urls", namespace="catalog")),
    path("api/v1/bookings/", include("apps.bookings.urls", namespace="bookings")),
    path("api/v1/payments/", include("apps.payments.urls", namespace="payments")),
]

if getattr(settings, "MOCK_PAYMENTS_ENABLED", False):
    urlpatterns.append(
        path("api/v1/mock-provider/", include("apps.mock_provider.urls", namespace="mock_provider"))
    )
