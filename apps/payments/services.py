import hashlib
import hmac
import logging
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.bookings.models import Booking, BookingStatus
from apps.payments.models import (
    Payment,
    PaymentStatus,
    WebhookEvent,
    WebhookEventStatus,
)

logger = logging.getLogger(__name__)


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
    booking = Booking.objects.select_for_update().get(id=payment.booking_id)

    # 1. Update payment status
    payment.status = status
    payment.save(update_fields=["status", "updated_at"])

    # 2. Evaluate booking state transitions
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

    # 5. Apply payment result
    outcome = (
        PaymentStatus.SUCCESS
        if status_str == PaymentStatus.SUCCESS
        else PaymentStatus.FAILED
    )
    apply_payment_result(
        payment=payment,
        status=outcome,
        source="webhook",
        event_id=event_id,
    )

    event.status = WebhookEventStatus.PROCESSED
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "processed_at"])

    return True, "processed", 200
