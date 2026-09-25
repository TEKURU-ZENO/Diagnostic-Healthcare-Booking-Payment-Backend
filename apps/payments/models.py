from decimal import Decimal
from django.db import models
from django.conf import settings
from apps.core.models import TimeStampedModel
from apps.bookings.models import Booking


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    SUCCESS = "SUCCESS", "Success"
    FAILED = "FAILED", "Failed"


class WebhookEventStatus(models.TextChoices):
    PROCESSED = "PROCESSED", "Processed"
    IGNORED = "IGNORED", "Ignored"


class Payment(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Authoritative charged amount matching the booking snapshot",
    )
    status = models.CharField(
        max_length=20,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PENDING,
        db_index=True,
    )
    provider_ref = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text="Merchant-generated unique reference sent to external payment gateway",
    )
    idempotency_key = models.CharField(
        max_length=255,
        db_index=True,
        help_text="Client-supplied idempotency key scoped per user",
    )
    flagged_for_refund = models.BooleanField(
        default=False,
        help_text="Set to True if customer was charged but booking could not be confirmed",
    )

    class Meta:
        constraints = [
            # Idempotency key is scoped per user to prevent collision / leakage
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                name="uniq_user_idempotency_key",
            ),
            # Enforce exactly one in-flight PENDING payment per booking
            models.UniqueConstraint(
                fields=["booking"],
                condition=models.Q(status=PaymentStatus.PENDING),
                name="one_inflight_payment_per_booking",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")),
                name="check_payment_amount_positive",
            ),
        ]

    def __str__(self):
        return f"Payment #{self.id} ({self.status}) - {self.provider_ref} for Booking #{self.booking_id}"


class WebhookEvent(models.Model):
    """
    Append-only deduplication ledger for inbound webhooks.
    Global uniqueness on event_id provides DB-level replay protection.
    """
    event_id = models.CharField(max_length=100, unique=True, db_index=True)
    payload = models.JSONField()
    status = models.CharField(
        max_length=20,
        choices=WebhookEventStatus.choices,
        default=WebhookEventStatus.PROCESSED,
        db_index=True,
    )
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"WebhookEvent {self.event_id} ({self.status})"
