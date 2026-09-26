from rest_framework import serializers

from apps.payments.models import Payment


class PaymentCreateSerializer(serializers.Serializer):
    # Plain integer, not PrimaryKeyRelatedField: a related field answers 400 "does not exist" for
    # unknown IDs, while the view answers 404 for other users' IDs, which would leak existence.
    booking = serializers.IntegerField(min_value=1)
    simulate_outcome = serializers.ChoiceField(
        choices=[
            ("SUCCESS", "Success"),
            ("FAILED", "Failed"),
            ("ASYNC_SUCCESS", "Provider succeeds, response lost (settled by webhook)"),
            ("ASYNC_FAILED", "Provider fails, response lost (settled by webhook)"),
        ],
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
