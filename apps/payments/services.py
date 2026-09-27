import hashlib
import hmac
import logging
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import Http404
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.core.exceptions import (
    IdempotencyPayloadMismatchError,
    PaymentConflictError,
)
from apps.mock_provider.gateway import PaymentGateway
from apps.payments.models import (
    Payment,
    PaymentStatus,
    WebhookEvent,
    WebhookEventStatus,
)

logger = logging.getLogger(__name__)


def initiate_payment(
    user,
    booking_id: int,
    idempotency_key: str,
    simulate_outcome: str = "SUCCESS",
) -> tuple[Payment, int]:
    """
    Orchestrates payment checkout initiation:
    1. Checks for previously completed idempotent request under (user, idempotency_key).
    2. Transaction 1: Locks booking, validates state/ownership, inserts PENDING payment.
    3. Calls external PaymentGateway without holding any DB locks or open transactions.
    4. Transaction 2: Settles payment outcome via single funnel apply_payment_result.
    Returns (payment, http_status_code) where http_status_code is 200 (completed) or 202 (accepted/in-flight).
    """
    # 1. Check for previously completed idempotent request first
    existing_payment = Payment.objects.filter(
        user=user, idempotency_key=idempotency_key
    ).first()
    if existing_payment:
        if existing_payment.booking_id != booking_id:
            raise IdempotencyPayloadMismatchError(
                "Idempotency-Key was already used for a different booking."
            )
        return existing_payment, 200

    # 2. Transaction 1: Lock booking, validate ownership & state, insert PENDING payment
    try:
        with transaction.atomic():
            # Lock booking row to serialize against background expiry and concurrent payments
            booking = (
                Booking.objects.select_for_update()
                .filter(id=booking_id)
                .first()
            )
            if not booking or (booking.user != user and not user.is_staff):
                # Zero existence leakage: return 404 if booking not owned by caller
                raise Http404("Booking not found.")

            if booking.status in (BookingStatus.CONFIRMED, BookingStatus.CANCELLED):
                raise PaymentConflictError(
                    f"Cannot initiate payment for booking with status '{booking.status}'."
                )

            if booking.appointment_at <= timezone.now():
                raise PaymentConflictError("Cannot pay for an appointment that is in the past.")

            if booking.status == BookingStatus.FAILED:
                booking.transition_to(BookingStatus.PENDING, source="api")

            # Generate local merchant reference
            provider_ref = f"pay_{uuid.uuid4().hex}"

            # Insert-first in nested savepoint to eliminate race conditions
            try:
                with transaction.atomic():
                    payment = Payment.objects.create(
                        user=user,
                        booking=booking,
                        amount=booking.amount,
                        idempotency_key=idempotency_key,
                        provider_ref=provider_ref,
                        status=PaymentStatus.PENDING,
                    )
            except IntegrityError:
                # Race condition check: another simultaneous request inserted first
                existing = Payment.objects.filter(
                    user=user, idempotency_key=idempotency_key
                ).first()
                if existing:
                    if existing.booking_id != booking.id:
                        raise IdempotencyPayloadMismatchError(
                            "Idempotency-Key was already used for a different booking."
                        )
                    return existing, 200

                raise PaymentConflictError(
                    "Another payment is currently in-flight for this booking."
                )
    except Http404:
        raise
    except (PaymentConflictError, IdempotencyPayloadMismatchError):
        raise

    # 3. Transaction 1 committed PENDING payment before calling gateway.
    # Call external payment gateway without holding any DB locks or open transactions.
    try:
        gateway_result = PaymentGateway.charge(
            provider_ref=payment.provider_ref,
            amount=payment.amount,
            simulate_outcome=simulate_outcome,
        )
    except Exception:
        # External gateway timed out or failed to respond; payment remains PENDING
        return payment, 202

    # 4. Transaction 2: Settle payment outcome via single funnel
    updated_payment, _ = apply_payment_result(
        payment=payment,
        status=gateway_result["status"],
        source="api",
    )

    return updated_payment, 200


@transaction.atomic
def apply_payment_result(
    payment: Payment,
    status: str,
    source: str,
    event_id: str | None = None,
) -> tuple[Payment, Booking]:
    """
    Canonical single-funnel function for applying payment outcomes.
    Serializes concurrent modifications with select_for_update() on the Booking row.
    """
    # Lock order is always booking, then payment, so this can't deadlock with POST /payments/.
    booking = Booking.objects.select_for_update().get(id=payment.booking_id)
    payment = Payment.objects.select_for_update().get(id=payment.id)  # re-read: caller's copy may be stale

    # 1. Payment-level state machine. The same result arriving again (sync API response followed
    #    by the provider's own webhook, or reconciliation after a webhook) is a no-op.
    if payment.status == status:
        return payment, booking
    if payment.status == PaymentStatus.SUCCESS:
        # Money was captured. A later FAILED for the same payment never un-captures it.
        logger.warning(
            "Ignoring %s for a payment that already succeeded.",
            status,
            extra={"booking_id": booking.id, "provider_ref": payment.provider_ref, "event_id": event_id},
        )
        return payment, booking

    payment.status = status
    payment.save(update_fields=["status", "updated_at"])

    # 2. Evaluate booking state transitions. From here on, `payment` has just changed state,
    #    so a SUCCESS on an already CONFIRMED booking really is a second capture.
    if booking.status == BookingStatus.CONFIRMED:
        if status == PaymentStatus.FAILED:
            # Late FAILED webhook after booking is already CONFIRMED: log and ignore
            logger.warning(
                "Late FAILED payment received for already CONFIRMED booking.",
                extra={"booking_id": booking.id, "provider_ref": payment.provider_ref, "event_id": event_id},
            )
        elif status == PaymentStatus.SUCCESS:
            # Duplicate payment received (e.g. earlier payment 2 succeeded, now late payment 1 succeeds)
            # Customer was charged twice -> flag payment for refund
            payment.flagged_for_refund = True
            payment.save(update_fields=["flagged_for_refund", "updated_at"])
            logger.warning(
                "Duplicate SUCCESS payment received for already CONFIRMED booking. Flagged for refund.",
                extra={"booking_id": booking.id, "provider_ref": payment.provider_ref, "event_id": event_id},
            )

    elif booking.status == BookingStatus.FAILED:
        if status == PaymentStatus.SUCCESS:
            # Late SUCCESS after booking was marked FAILED -> funds were captured!
            # Attempt to confirm booking, but protect against slot conflict if rebooked.
            try:
                with transaction.atomic():
                    booking.transition_to(BookingStatus.CONFIRMED, source=source, event_id=event_id)
            except IntegrityError:
                # Slot was rebooked in the meantime; keep booking FAILED and flag payment for refund
                payment.flagged_for_refund = True
                payment.save(update_fields=["flagged_for_refund", "updated_at"])
                logger.warning(
                    "Late SUCCESS captured but slot was already rebooked. Flagged payment for refund.",
                    extra={"booking_id": booking.id, "provider_ref": payment.provider_ref, "event_id": event_id},
                )

    elif booking.status == BookingStatus.CANCELLED:
        if status == PaymentStatus.SUCCESS:
            # Payment captured after cancellation -> flag for refund
            payment.flagged_for_refund = True
            payment.save(update_fields=["flagged_for_refund", "updated_at"])
            logger.warning(
                "SUCCESS payment received for CANCELLED booking. Flagged for refund.",
                extra={"booking_id": booking.id, "provider_ref": payment.provider_ref, "event_id": event_id},
            )

    elif booking.status == BookingStatus.PENDING:
        target_status = (
            BookingStatus.CONFIRMED
            if status == PaymentStatus.SUCCESS
            else BookingStatus.FAILED
        )
        booking.transition_to(target_status, source=source, event_id=event_id)

    return payment, booking


def verify_webhook_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """
    Validate HMAC-SHA256 signature against request.body raw bytes.
    Accepts format 'sha256=<hex>' or raw hex.
    """
    if not signature_header:
        return False

    secret = settings.WEBHOOK_SECRET.encode("utf-8")
    expected_hex = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()

    given_sig = signature_header.strip()
    if given_sig.startswith("sha256="):
        given_sig = given_sig[7:].strip()

    return hmac.compare_digest(expected_hex, given_sig)


@transaction.atomic
def process_webhook_event(
    raw_body: bytes,
    signature_header: str | None,
    payload: dict,
) -> tuple[bool, str, int]:
    """
    Inbound webhook processing service:
    1. Cryptographically verifies HMAC-SHA256 signature (401 on failure).
    2. Inserts WebhookEvent in a nested savepoint for replay deduplication (200 on duplicate).
    3. Looks up Payment by provider_ref. If not found, rolls back and returns 404
       so payment provider retries until local payment row is committed.
    4. Validates payload amount matches payment.amount (marks IGNORED + 200 on mismatch).
    5. Funnels into apply_payment_result.
    Returns (success, message, http_status_code).
    """
    # 1. HMAC check
    if not verify_webhook_signature(raw_body, signature_header):
        logger.warning("Webhook received with invalid HMAC signature.")
        return False, "invalid_signature", 401

    if not isinstance(payload, dict):
        return False, "malformed_payload", 400

    event_id = str(payload.get("event_id", ""))
    provider_ref = str(payload.get("provider_ref", ""))
    status_str = str(payload.get("status", "")).upper()
    amount_raw = payload.get("amount")

    if not event_id or not provider_ref or not status_str or amount_raw is None:
        return False, "malformed_payload", 400

    # 2. Insert WebhookEvent inside nested savepoint
    try:
        with transaction.atomic():
            event = WebhookEvent.objects.create(event_id=event_id, payload=payload)
    except IntegrityError:
        # Replay detected: unique constraint on event_id caught duplicate
        logger.info("Webhook duplicate event_id replayed; no-op.", extra={"event_id": event_id})
        return True, "duplicate", 200

    # 3. Lookup Payment by provider_ref
    payment = Payment.objects.filter(provider_ref=provider_ref).first()
    if not payment:
        # Premature webhook race: payment row not yet committed.
        # Must roll back transaction so WebhookEvent does NOT persist,
        # and return 404 so provider retries with exponential backoff.
        transaction.set_rollback(True)
        logger.warning(
            "Webhook received for unknown provider_ref; rolling back for provider retry.",
            extra={"provider_ref": provider_ref, "event_id": event_id},
        )
        return False, "unknown_provider_ref", 404

    # 4. Precision amount validation
    try:
        payload_amount = Decimal(str(amount_raw))
    except Exception:
        event.status = WebhookEventStatus.IGNORED
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "processed_at"])
        return True, "invalid_amount_format", 200

    if payload_amount != payment.amount:
        logger.error(
            "Webhook amount mismatch. Expected %s, got %s.",
            payment.amount,
            payload_amount,
            extra={"provider_ref": provider_ref, "event_id": event_id},
        )
        event.status = WebhookEventStatus.IGNORED
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "processed_at"])
        return True, "amount_mismatch", 200

    # 5. Only SUCCESS and FAILED are final outcomes. Anything else (PENDING, REFUNDED, a typo)
    #    must not be read as FAILED, or an unrelated event would fail a live booking.
    if status_str not in (PaymentStatus.SUCCESS, PaymentStatus.FAILED):
        logger.warning(
            "Webhook with unsupported status %s ignored.",
            status_str,
            extra={"provider_ref": provider_ref, "event_id": event_id},
        )
        event.status = WebhookEventStatus.IGNORED
        event.processed_at = timezone.now()
        event.save(update_fields=["status", "processed_at"])
        return True, "unsupported_status", 200

    # 6. Apply payment result
    apply_payment_result(
        payment=payment,
        status=status_str,
        source="webhook",
        event_id=event_id,
    )

    event.status = WebhookEventStatus.PROCESSED
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "processed_at"])

    return True, "processed", 200
