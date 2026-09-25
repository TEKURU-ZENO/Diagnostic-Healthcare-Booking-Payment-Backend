from decimal import Decimal
from rest_framework import serializers
from apps.catalog.models import DiagnosticCentre, DiagnosticTest, CentreTest


class DiagnosticCentreSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticCentre
        fields = ("id", "name", "city", "address", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class DiagnosticTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiagnosticTest
        fields = ("id", "name", "code", "description", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class CentreTestSerializer(serializers.ModelSerializer):
    centre_name = serializers.CharField(source="centre.name", read_only=True)
    centre_city = serializers.CharField(source="centre.city", read_only=True)
    test_name = serializers.CharField(source="test.name", read_only=True)
    test_code = serializers.CharField(source="test.code", read_only=True)

    class Meta:
        model = CentreTest
        fields = (
            "id",
            "centre",
            "centre_name",
            "centre_city",
            "test",
            "test_name",
            "test_code",
            "price",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_price(self, value):
        if value <= Decimal("0.00"):
            raise serializers.ValidationError("Price must be strictly positive.")
        return value
