from rest_framework.routers import DefaultRouter

from apps.catalog.views import (
    CentreTestViewSet,
    DiagnosticCentreViewSet,
    DiagnosticTestViewSet,
)

app_name = "catalog"

router = DefaultRouter()
router.register(r"centres", DiagnosticCentreViewSet, basename="centre")
router.register(r"tests", DiagnosticTestViewSet, basename="test")
router.register(r"centre-tests", CentreTestViewSet, basename="centre-test")

urlpatterns = router.urls
