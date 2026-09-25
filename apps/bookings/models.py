from decimal import Decimal
from django.db import models
from django.conf import settings
from django.utils import timezone
from apps.core.models import TimeStampedModel
from apps.core.exceptions import InvalidStateTransitionError
from apps.catalog.models import CentreTest


class BookingStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    CONFIRMED = "CONFIRMED", "Confirmed"
    FAILED = "FAILED", "Failed"
    CANCELLED = "CANCELLED", "Cancelled"


class Booking(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="bookings",
    )
    centre_test = models.ForeignKey(
        CentreTest,
        on_delete=models.PROTECT,
        related_name="bookings",
    )
    appointment_at = models.DateTimeField(db_index=True)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Snapshotted amount at the time of booking creation",
    )
    status = models.CharField(
        max_length=20,
        choices=BookingStatus.choices,
        default=BookingStatus.PENDING,
        db_index=True,
    )
    flagged_for_refund = models.BooleanField(
        default=False,
        help_text="Indicates this booking requires a refund to be issued or reconciled",
    )

    class Meta:
        constraints = [
            # Enforce single active booking per user for a specific test & slot
            models.UniqueConstraint(
                fields=["user", "centre_test", "appointment_at"],
                condition=~models.Q(status__in=[BookingStatus.FAILED, BookingStatus.CANCELLED]),
                name="uniq_active_booking_per_slot",
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gt=Decimal("0.00")),
                name="check_booking_amount_positive",
            ),
        ]

    def __str__(self):
        return f"Booking #{self.id} ({self.status}) - {self.user.username}"

    def transition_to(self, new_status: str, source: str, event_id: str | None = None) -> None:
        """
        Enforce finite state machine rules and append an audit record to BookingStatusHistory.
        Valid transitions:
        - PENDING -> CONFIRMED, FAILED, CANCELLED
        - FAILED -> PENDING (user retry), CONFIRMED (late success via webhook/reconciliation), CANCELLED
        - CONFIRMED -> CANCELLED (triggers refund flag)
        - CANCELLED is terminal
        """
        curr = self.status
        if curr == new_status:
            return  # Idempotent no-op

        valid = False

        if curr == BookingStatus.PENDING:
            if new_status in (BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED):
                valid = True

        elif curr == BookingStatus.FAILED:
            if new_status == BookingStatus.PENDING:
                valid = True
            elif new_status == BookingStatus.CONFIRMED and source in ("webhook", "reconciliation"):
                valid = True
            elif new_status == BookingStatus.CANCELLED:
                valid = True

        elif curr == BookingStatus.CONFIRMED:
            if new_status == BookingStatus.CANCELLED:
                valid = True
                self.flagged_for_refund = True

        if not valid:
            raise InvalidStateTransitionError(
                f"Illegal state transition from {curr} to {new_status} via source '{source}'."
            )

        from_status = self.status
        self.status = new_status
        self.save(update_fields=["status", "flagged_for_refund", "updated_at"])

        # Append to audit trail in same transaction
        BookingStatusHistory.objects.create(
            booking=self,
            from_status=from_status,
            to_status=new_status,
            source=source,
            event_id=event_id,
        )


class BookingStatusHistory(models.Model):
    """
    Append-only audit trail recording every status change for compliance,
    dispute resolution, and distributed tracing.
    """
    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name="status_history",
    )
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    source = models.CharField(max_length=50)  # e.g. 'api', 'webhook', 'reconciliation', 'system', 'user'
    event_id = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"History #{self.id} Booking #{self.booking_id}: {self.from_status} -> {self.to_status} ({self.source})"
