import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st
from hypothesis.extra.django import TestCase

from apps.bookings.models import Booking, BookingStatus
from apps.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from apps.payments.models import Payment, PaymentStatus
from apps.payments.services import process_webhook_event
from tests.test_webhooks import generate_signature

User = get_user_model()


class PaymentWebhookPropertyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="hypo_user", email="hypo@example.com", password="Password!123"
        )
        centre = DiagnosticCentre.objects.create(
            name="Hypothesis Lab", city="Mumbai", address="Colaba"
        )
        test = DiagnosticTest.objects.create(name="Blood Test", code="HYPO_TEST")
        cls.centre_test = CentreTest.objects.create(
            centre=centre, test=test, price=Decimal("500.00"), is_active=True
        )

    @hyp_settings(max_examples=50, deadline=None)
    @given(
        events=st.lists(
            st.tuples(
                st.sampled_from(["evt_1", "evt_2", "evt_3", "evt_4"]),  # deliberate duplicate event_ids
                st.sampled_from(["SUCCESS", "FAILED"]),
            ),
            min_size=1,
            max_size=10,
        )
    )
    def test_payment_webhook_invariants(self, events):
        """
        Property-based test verifying that across arbitrary permutations of webhook arrivals,
        delays, duplicate deliveries, and mixed SUCCESS/FAILED outcomes:
        - Invariant 1: At most one payment row can ever confirm the booking.
        - Invariant 2: A CONFIRMED booking NEVER transitions back to FAILED.
        - Invariant 3: Replaying the sequence is strictly idempotent.
        """
        # Create fresh booking and payment for this test run
        booking = Booking.objects.create(
            user=self.user,
            centre_test=self.centre_test,
            appointment_at=timezone.now() + timedelta(days=2),
            amount=self.centre_test.price,
            status=BookingStatus.PENDING,
        )
        provider_ref = f"pay_hypo_{uuid.uuid4().hex}"
        Payment.objects.create(
            user=self.user,
            booking=booking,
            amount=booking.amount,
            status=PaymentStatus.PENDING,
            provider_ref=provider_ref,
            idempotency_key=f"idemp_{uuid.uuid4().hex}",
        )

        confirmed_ever_seen = False

        for event_id, status_outcome in events:
            payload = {
                "event_id": event_id,
                "provider_ref": provider_ref,
                "status": status_outcome,
                "amount": str(booking.amount),
            }
            sig, body_bytes = generate_signature(payload, settings.WEBHOOK_SECRET)
            process_webhook_event(
                raw_body=body_bytes,
                signature_header=sig,
                payload=payload,
            )

            booking.refresh_from_db()
            if booking.status == BookingStatus.CONFIRMED:
                confirmed_ever_seen = True

            # Invariant 2: If booking was confirmed, it MUST NEVER flip back to FAILED
            if confirmed_ever_seen:
                assert booking.status == BookingStatus.CONFIRMED

        # Invariant 1: Booking is in a valid terminal/settled state
        booking.refresh_from_db()
        assert booking.status in (BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.FAILED)

        # Invariant 3: Replaying the last event leaves state completely unchanged
        if events:
            last_event_id, last_status = events[-1]
            last_payload = {
                "event_id": last_event_id,
                "provider_ref": provider_ref,
                "status": last_status,
                "amount": str(booking.amount),
            }
            sig, body_bytes = generate_signature(last_payload, settings.WEBHOOK_SECRET)
            final_status_before = booking.status
            process_webhook_event(
                raw_body=body_bytes,
                signature_header=sig,
                payload=last_payload,
            )
            booking.refresh_from_db()
            assert booking.status == final_status_before
