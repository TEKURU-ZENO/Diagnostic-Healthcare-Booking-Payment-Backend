from rest_framework import viewsets

from apps.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from apps.catalog.permissions import IsAdminOrReadOnly
from apps.catalog.serializers import (
    CentreTestSerializer,
    DiagnosticCentreSerializer,
    DiagnosticTestSerializer,
)


class DiagnosticCentreViewSet(viewsets.ModelViewSet):
    queryset = DiagnosticCentre.objects.all().order_by("name")
    serializer_class = DiagnosticCentreSerializer
    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        city = self.request.query_params.get("city")
        if city:
            qs = qs.filter(city__iexact=city.strip())
        return qs


class DiagnosticTestViewSet(viewsets.ModelViewSet):
    queryset = DiagnosticTest.objects.all().order_by("name")
    serializer_class = DiagnosticTestSerializer
    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        code = self.request.query_params.get("code")
        if code:
            qs = qs.filter(code__iexact=code.strip())
        return qs


class CentreTestViewSet(viewsets.ModelViewSet):
    queryset = CentreTest.objects.select_related("centre", "test").order_by("centre", "test")
    serializer_class = CentreTestSerializer
    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        centre_id = self.request.query_params.get("centre")
        test_id = self.request.query_params.get("test")
        active_only = self.request.query_params.get("active")

        if centre_id:
            if centre_id.isdecimal():
                qs = qs.filter(centre_id=int(centre_id))
            else:
                qs = qs.none()
        if test_id:
            if test_id.isdecimal():
                qs = qs.filter(test_id=int(test_id))
            else:
                qs = qs.none()
        if active_only and active_only.lower() in ("true", "1"):
            qs = qs.filter(is_active=True)
        return qs
