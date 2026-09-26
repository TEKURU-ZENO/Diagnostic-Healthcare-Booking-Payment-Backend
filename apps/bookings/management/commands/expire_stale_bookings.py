import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.payments.models import PaymentStatus

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Expire PENDING bookings older than specified minutes (default 15) using select_for_update(skip_locked=True)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes",
            type=int,
            default=15,
            help="Age threshold in minutes for considering a PENDING booking stale (default: 15)",
        )

    def handle(self, *args, **options):
        minutes = options["minutes"]
        cutoff = timezone.now() - timedelta(minutes=minutes)

        self.stdout.write(f"Scanning for PENDING bookings created before {cutoff.isoformat()}...")

        expired_count = 0

        # Run inside transaction with skip_locked to avoid blocking concurrent transactions
        with transaction.atomic():
            # Crucial: skip bookings that currently have an in-flight PENDING payment!
            # Otherwise we would race with a customer whose checkout is in progress.
            stale_bookings = (
                Booking.objects.filter(
                    status=BookingStatus.PENDING,
                    created_at__lte=cutoff,
                )
                .exclude(payments__status=PaymentStatus.PENDING)
                .select_for_update(skip_locked=True)
            )

            for booking in stale_bookings:
                booking.transition_to(
                    new_status=BookingStatus.CANCELLED,
                    source="system",
                )
                expired_count += 1
                logger.info(
                    "Expired stale booking.",
                    extra={"booking_id": booking.id},
                )

        self.stdout.write(
            self.style.SUCCESS(f"Successfully expired {expired_count} stale booking(s).")
        )
