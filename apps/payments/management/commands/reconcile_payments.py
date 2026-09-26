import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.mock_provider.gateway import PaymentGateway
from apps.payments.models import Payment, PaymentStatus
from apps.payments.services import apply_payment_result

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reconcile stale PENDING payments against the external payment gateway source of truth"

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes",
            type=int,
            default=15,
            help="Age threshold in minutes for PENDING payments to be reconciled (default: 15)",
        )

    def handle(self, *args, **options):
        minutes = options["minutes"]
        cutoff = timezone.now() - timedelta(minutes=minutes)

        self.stdout.write(f"Scanning for PENDING payments created before {cutoff.isoformat()}...")

        pending_payments = Payment.objects.filter(
            status=PaymentStatus.PENDING,
            created_at__lte=cutoff,
        ).select_related("booking")

        reconciled_count = 0

        for payment in pending_payments:
            gateway_data = PaymentGateway.get_status(payment.provider_ref)
            if not gateway_data:
                # We committed the PENDING row but the charge call never reached the provider
                # (crash or timeout before it recorded anything). Nothing was captured, so fail it.
                # Skipping it instead would hold the booking PENDING forever, because the expiry
                # job deliberately ignores bookings that have an in-flight payment.
                logger.warning(
                    "Reconciliation: provider has no record of %s; marking FAILED.",
                    payment.provider_ref,
                    extra={"provider_ref": payment.provider_ref, "booking_id": payment.booking_id},
                )
                provider_status = PaymentStatus.FAILED
            else:
                provider_status = gateway_data["status"]
            apply_payment_result(
                payment=payment,
                status=provider_status,
                source="reconciliation",
            )
            reconciled_count += 1
            logger.info(
                "Payment successfully reconciled to %s.",
                provider_status,
                extra={"provider_ref": payment.provider_ref, "booking_id": payment.booking_id},
            )

        self.stdout.write(
            self.style.SUCCESS(f"Reconciliation complete. Settled {reconciled_count} payment(s).")
        )
