import hmac
import hashlib
import json
import threading
from decimal import Decimal
from datetime import timedelta
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import connections
from rest_framework.test import APIClient

from apps.catalog.models import DiagnosticCentre, DiagnosticTest, CentreTest
from apps.bookings.models import Booking, BookingStatus
from apps.payments.models import Payment, PaymentStatus, WebhookEvent, WebhookEventStatus

User = get_user_model()


def generate_signature(payload_dict: dict, secret: str) -> str:
    body_bytes = json.dumps(payload_dict).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}", body_bytes


@pytest.fixture
def user():
    return User.objects.create_user(username="webhook_user", email="wh@example.com", password="Password!123")


@pytest.fixture
def centre_test():
    centre = DiagnosticCentre.objects.create(name="Apex Webhook", city="Pune", address="Koregaon Park")
    test = DiagnosticTest.objects.create(name="Vitamin D", code="VITD_01")
    return CentreTest.objects.create(centre=centre, test=test, price=Decimal("1200.00"), is_active=True)


@pytest.fixture
def booking(user, centre_test):
    return Booking.objects.create(
        user=user,
        centre_test=centre_test,
        appointment_at=timezone.now() + timedelta(days=2),
        amount=centre_test.price,
        status=BookingStatus.PENDING,
    )


@pytest.fixture
def payment(user, booking):
    return Payment.objects.create(
        user=user,
        booking=booking,
        amount=booking.amount,
        status=PaymentStatus.PENDING,
        provider_ref="ref_wh_test_123",
        idempotency_key="idemp_wh_123",
    )


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_webhook_invalid_signature_returns_401(api_client, payment):
    payload = {
        "event_id": "evt_test_1",
        "provider_ref": payment.provider_ref,
        "status": "SUCCESS",
        "amount": "1200.00",
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    res = api_client.post(
        "/api/v1/payments/webhook/",
        data=body_bytes,
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE="sha256=invalid_hash_value_12345",
    )
    assert res.status_code == 401


@pytest.mark.django_db
def test_webhook_successful_confirmation(api_client, payment, booking):
    payload = {
        "event_id": "evt_success_1",
        "provider_ref": payment.provider_ref,
        "status": "SUCCESS",
        "amount": "1200.00",
    }
    sig, body_bytes = generate_signature(payload, settings.WEBHOOK_SECRET)

    res = api_client.post(
        "/api/v1/payments/webhook/",
        data=body_bytes,
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE=sig,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "processed"

    payment.refresh_from_db()
    booking.refresh_from_db()
    assert payment.status == PaymentStatus.SUCCESS
    assert booking.status == BookingStatus.CONFIRMED

    # WebhookEvent record created and processed
    event = WebhookEvent.objects.get(event_id="evt_success_1")
    assert event.status == WebhookEventStatus.PROCESSED


@pytest.mark.django_db
def test_webhook_replay_deduplication(api_client, payment, booking):
    payload = {
        "event_id": "evt_replay_1",
        "provider_ref": payment.provider_ref,
        "status": "SUCCESS",
        "amount": "1200.00",
    }
    sig, body_bytes = generate_signature(payload, settings.WEBHOOK_SECRET)

    # First delivery
    res1 = api_client.post(
        "/api/v1/payments/webhook/",
        data=body_bytes,
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE=sig,
    )
    assert res1.status_code == 200

    # Second delivery (replay)
    res2 = api_client.post(
        "/api/v1/payments/webhook/",
        data=body_bytes,
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE=sig,
    )
    assert res2.status_code == 200
    assert res2.json()["status"] == "duplicate"

    assert WebhookEvent.objects.filter(event_id="evt_replay_1").count() == 1


@pytest.mark.django_db
def test_webhook_unknown_provider_ref_returns_404_and_rolls_back(api_client):
    payload = {
        "event_id": "evt_unknown_1",
        "provider_ref": "ref_does_not_exist_yet",
        "status": "SUCCESS",
        "amount": "1200.00",
    }
    sig, body_bytes = generate_signature(payload, settings.WEBHOOK_SECRET)

    res = api_client.post(
        "/api/v1/payments/webhook/",
        data=body_bytes,
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE=sig,
    )
    # Returns 404 so provider retries
    assert res.status_code == 404

    # WebhookEvent row MUST have been rolled back so retry is not deduped!
    assert not WebhookEvent.objects.filter(event_id="evt_unknown_1").exists()


@pytest.mark.django_db
def test_webhook_amount_mismatch_ignored(api_client, payment, booking):
    payload = {
        "event_id": "evt_mismatch_1",
        "provider_ref": payment.provider_ref,
        "status": "SUCCESS",
        "amount": "999.00",  # Mismatch (booking is 1200.00)
    }
    sig, body_bytes = generate_signature(payload, settings.WEBHOOK_SECRET)

    res = api_client.post(
        "/api/v1/payments/webhook/",
        data=body_bytes,
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE=sig,
    )
    assert res.status_code == 200
    assert res.json()["status"] == "amount_mismatch"

    payment.refresh_from_db()
    booking.refresh_from_db()
    # Unchanged
    assert payment.status == PaymentStatus.PENDING
    assert booking.status == BookingStatus.PENDING

    event = WebhookEvent.objects.get(event_id="evt_mismatch_1")
    assert event.status == WebhookEventStatus.IGNORED


@pytest.mark.django_db
def test_late_failed_after_confirmed_ignored(api_client, payment, booking):
    booking.status = BookingStatus.CONFIRMED
    booking.save()
    payment.status = PaymentStatus.SUCCESS
    payment.save()

    payload = {
        "event_id": "evt_late_failed",
        "provider_ref": payment.provider_ref,
        "status": "FAILED",
        "amount": "1200.00",
    }
    sig, body_bytes = generate_signature(payload, settings.WEBHOOK_SECRET)

    res = api_client.post(
        "/api/v1/payments/webhook/",
        data=body_bytes,
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE=sig,
    )
    assert res.status_code == 200

    booking.refresh_from_db()
    # Booking stays CONFIRMED
    assert booking.status == BookingStatus.CONFIRMED


@pytest.mark.django_db
def test_late_success_after_rebooked_slot_conflict_handled(api_client, user, centre_test):
    """
    Bug 1 resolution:
    1. Booking 1 fails.
    2. User rebooks the exact same slot (Booking 2).
    3. Late SUCCESS arrives for Booking 1.
    4. Transition to CONFIRMED violates uniq_active_booking_per_slot.
    5. Handled cleanly inside savepoint: Booking 1 stays FAILED, Payment 1 flagged for refund, webhook returns 200!
    """
    slot_time = timezone.now() + timedelta(days=5)

    # 1. Booking 1 failed
    booking_1 = Booking.objects.create(
        user=user,
        centre_test=centre_test,
        appointment_at=slot_time,
        amount=centre_test.price,
        status=BookingStatus.FAILED,
    )
    payment_1 = Payment.objects.create(
        user=user,
        booking=booking_1,
        amount=booking_1.amount,
        status=PaymentStatus.PENDING,
        provider_ref="ref_slot_conflict_test",
        idempotency_key="idemp_slot_1",
    )

    # 2. User rebooks same slot
    booking_2 = Booking.objects.create(
        user=user,
        centre_test=centre_test,
        appointment_at=slot_time,
        amount=centre_test.price,
        status=BookingStatus.CONFIRMED,
    )

    # 3. Late SUCCESS arrives for Booking 1
    payload = {
        "event_id": "evt_slot_conflict_success",
        "provider_ref": payment_1.provider_ref,
        "status": "SUCCESS",
        "amount": str(booking_1.amount),
    }
    sig, body_bytes = generate_signature(payload, settings.WEBHOOK_SECRET)

    res = api_client.post(
        "/api/v1/payments/webhook/",
        data=body_bytes,
        content_type="application/json",
        HTTP_X_WEBHOOK_SIGNATURE=sig,
    )
    assert res.status_code == 200

    booking_1.refresh_from_db()
    payment_1.refresh_from_db()
    booking_2.refresh_from_db()

    # Slot remains held by booking_2; booking_1 stays FAILED; payment_1 is flagged for refund
    assert booking_2.status == BookingStatus.CONFIRMED
    assert booking_1.status == BookingStatus.FAILED
    assert payment_1.flagged_for_refund is True


from django.db import connection

@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Database concurrency and row-level locking tests require PostgreSQL engine",
)
@pytest.mark.django_db(transaction=True)
def test_concurrent_webhook_deliveries(user, booking, payment):
    """
    Fire multiple concurrent identical webhook requests in separate threads.
    Verifies that database-level uniqueness constraints eliminate race conditions:
    exactly 1 WebhookEvent row created and no 500 errors.
    """
    payload = {
        "event_id": "evt_concurrent_race_1",
        "provider_ref": payment.provider_ref,
        "status": "SUCCESS",
        "amount": "1200.00",
    }
    sig, body_bytes = generate_signature(payload, settings.WEBHOOK_SECRET)

    results = []

    def send_webhook():
        try:
            client = APIClient()
            res = client.post(
                "/api/v1/payments/webhook/",
                data=body_bytes,
                content_type="application/json",
                HTTP_X_WEBHOOK_SIGNATURE=sig,
            )
            results.append(res.status_code)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=send_webhook) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All threads must succeed (200 OK)
    assert len(results) == 5
    assert all(code == 200 for code in results)

    # Exactly 1 WebhookEvent record in database
    assert WebhookEvent.objects.filter(event_id="evt_concurrent_race_1").count() == 1

    booking.refresh_from_db()
    payment.refresh_from_db()
    assert booking.status == BookingStatus.CONFIRMED
    assert payment.status == PaymentStatus.SUCCESS
