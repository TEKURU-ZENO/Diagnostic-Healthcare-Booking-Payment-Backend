from rest_framework import serializers

from apps.bookings.models import Booking
from apps.payments.models import Payment


class PaymentCreateSerializer(serializers.Serializer):
    booking = serializers.PrimaryKeyRelatedField(queryset=Booking.objects.all())
    simulate_outcome = serializers.ChoiceField(
        choices=[("SUCCESS", "Success"), ("FAILED", "Failed")],
        default="SUCCESS",
        required=False,
    )


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = (
            "id",
            "booking",
            "amount",
            "status",
            "provider_ref",
            "idempotency_key",
            "flagged_for_refund",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class WebhookPayloadSerializer(serializers.Serializer):
    event_id = serializers.CharField(max_length=100)
    provider_ref = serializers.CharField(max_length=100)
    status = serializers.CharField(max_length=20)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
