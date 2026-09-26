from django.utils import timezone
from rest_framework import serializers

from apps.bookings.models import Booking, BookingStatus, BookingStatusHistory
from apps.catalog.models import CentreTest
from apps.catalog.serializers import CentreTestSerializer


class BookingStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingStatusHistory
        fields = ("id", "from_status", "to_status", "source", "event_id", "created_at")
        read_only_fields = fields


class BookingCreateSerializer(serializers.ModelSerializer):
    centre_test = serializers.PrimaryKeyRelatedField(
        queryset=CentreTest.objects.all(),
    )
    appointment_at = serializers.DateTimeField()

    class Meta:
        model = Booking
        fields = ("id", "centre_test", "appointment_at", "amount", "status", "created_at")
        read_only_fields = ("id", "amount", "status", "created_at")

    def validate_centre_test(self, value):
        if not value.is_active:
            raise serializers.ValidationError("This diagnostic test is currently inactive at this centre.")
        return value

    def validate_appointment_at(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("Appointment time must be strictly in the future.")
        return value

    def create(self, validated_data):
        user = self.context["request"].user
        centre_test = validated_data["centre_test"]
        appointment_at = validated_data["appointment_at"]

        # Snapshot price from centre_test at creation time
        booking = Booking.objects.create(
            user=user,
            centre_test=centre_test,
            appointment_at=appointment_at,
            amount=centre_test.price,
            status=BookingStatus.PENDING,
        )

        # Record initial status creation history
        BookingStatusHistory.objects.create(
            booking=booking,
            from_status="",
            to_status=BookingStatus.PENDING,
            source="user",
        )
        return booking


class BookingDetailSerializer(serializers.ModelSerializer):
    centre_test_details = CentreTestSerializer(source="centre_test", read_only=True)
    status_history = BookingStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = Booking
        fields = (
            "id",
            "user",
            "centre_test",
            "centre_test_details",
            "appointment_at",
            "amount",
            "status",
            "flagged_for_refund",
            "created_at",
            "updated_at",
            "status_history",
        )
        read_only_fields = fields
