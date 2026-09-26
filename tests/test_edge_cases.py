"""Edge cases found in review. Each asserts the correct behaviour and failed on the original code."""
import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bookings.models import Booking, BookingStatus
from apps.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest
from apps.payments.models import Payment, PaymentStatus
from tests.test_webhooks import generate_signature

User = get_user_model()


@pytest.fixture
def user():
    return User.objects.create_user(username="probe", email="p@example.com", password="Password!123")


@pytest.fixture
def ct():
    c = DiagnosticCentre.objects.create(name="Probe Lab", city="Chennai", address="Vadapalani")
    t = DiagnosticTest.objects.create(name="CBC", code="CBC_P")
    return CentreTest.objects.create(centre=c, test=t, price=Decimal("499.00"), is_active=True)


@pytest.fixture
def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def make_booking(user, ct, status=BookingStatus.PENDING, days=2):
    return Booking.objects.create(
        user=user, centre_test=ct, appointment_at=timezone.now() + timedelta(days=days),
        amount=ct.price, status=status,
    )


def make_payment(user, booking, status=PaymentStatus.PENDING, ref="ref_probe"):
    return Payment.objects.create(
        user=user, booking=booking, amount=booking.amount, status=status,
        provider_ref=ref, idempotency_key=ref,
    )


def send(client, event_id, ref, status, amount="499.00"):
    payload = {"event_id": event_id, "provider_ref": ref, "status": status, "amount": amount}
    sig, body = generate_signature(payload, settings.WEBHOOK_SECRET)
    return client.post("/api/v1/payments/webhook/", data=body, content_type="application/json",
                       HTTP_X_WEBHOOK_SIGNATURE=sig)


@pytest.mark.django_db
def test_late_failed_must_not_overwrite_successful_payment(client, user, ct):
    b = make_booking(user, ct)
    p = make_payment(user, b)
    send(client, "e1", p.provider_ref, "SUCCESS")
    send(client, "e2", p.provider_ref, "FAILED")
    p.refresh_from_db()
    b.refresh_from_db()
    assert b.status == BookingStatus.CONFIRMED
    assert p.status == PaymentStatus.SUCCESS, f"money was captured but payment row now says {p.status}"


@pytest.mark.django_db
def test_unknown_status_must_not_fail_booking(client, user, ct):
    b = make_booking(user, ct)
    p = make_payment(user, b)
    res = send(client, "e1", p.provider_ref, "REFUNDED")
    b.refresh_from_db()
    p.refresh_from_db()
    assert b.status == BookingStatus.PENDING, f"unknown status {res.json()} moved booking to {b.status}"


@pytest.mark.django_db
def test_api_double_booking_returns_4xx_not_500(client, user, ct):
    when = (timezone.now() + timedelta(days=3)).isoformat()
    r1 = client.post("/api/v1/bookings/", {"centre_test": ct.id, "appointment_at": when}, format="json")
    assert r1.status_code == 201
    r2 = client.post("/api/v1/bookings/", {"centre_test": ct.id, "appointment_at": when}, format="json")
    assert 400 <= r2.status_code < 500, f"got {r2.status_code}"


@pytest.mark.django_db
def test_cancel_with_stale_read_must_flag_refund(client, user, ct):
    """A webhook confirms the booking after the cancel view has read it but before it saves."""
    from unittest import mock

    from apps.bookings.views import BookingViewSet

    b = make_booking(user, ct)
    p = make_payment(user, b)
    stale = Booking.objects.get(id=b.id)  # what get_object() returned, still PENDING
    send(APIClient(), "e1", p.provider_ref, "SUCCESS")  # webhook confirms meanwhile
    with mock.patch.object(BookingViewSet, "get_object", return_value=stale):
        r = client.post(f"/api/v1/bookings/{b.id}/cancel/")
    assert r.status_code == 200
    b.refresh_from_db()
    assert b.status == BookingStatus.CANCELLED
    assert b.flagged_for_refund, "customer paid, booking cancelled, nobody told to refund"


@pytest.mark.django_db
def test_payment_unknown_to_provider_does_not_hold_booking_forever(user, ct):
    """Gateway call crashed before the provider recorded anything: payment PENDING, provider has no row."""
    b = make_booking(user, ct)
    p = make_payment(user, b)
    old = timezone.now() - timedelta(hours=2)
    Booking.objects.filter(id=b.id).update(created_at=old)
    Payment.objects.filter(id=p.id).update(created_at=old)
    call_command("reconcile_payments")
    call_command("expire_stale_bookings")
    p.refresh_from_db()
    b.refresh_from_db()
    assert not (p.status == PaymentStatus.PENDING and b.status == BookingStatus.PENDING), \
        "payment and booking stuck PENDING forever: reconcile skips it, expiry skips it"


@pytest.mark.django_db
def test_pay_for_past_appointment_rejected(client, user, ct):
    b = make_booking(user, ct)
    Booking.objects.filter(id=b.id).update(appointment_at=timezone.now() - timedelta(days=1))
    r = client.post("/api/v1/payments/", {"booking": b.id, "simulate_outcome": "SUCCESS"},
                    format="json", HTTP_IDEMPOTENCY_KEY="k1")
    assert r.status_code >= 400, f"charged {r.status_code} for an appointment that already happened"


@pytest.mark.django_db
def test_non_object_json_webhook_is_4xx(client):
    body = json.dumps(["x"]).encode()
    import hashlib
    import hmac
    sig = "sha256=" + hmac.new(settings.WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    r = client.post("/api/v1/payments/webhook/", data=body, content_type="application/json",
                    HTTP_X_WEBHOOK_SIGNATURE=sig)
    assert r.status_code == 400


@pytest.mark.django_db
def test_happy_path_pay_then_normal_webhook_must_not_flag_refund(client, user, ct):
    """The most common flow in production: sync API success, then the provider's own SUCCESS webhook."""
    b = make_booking(user, ct)
    r = client.post("/api/v1/payments/", {"booking": b.id, "simulate_outcome": "SUCCESS"},
                    format="json", HTTP_IDEMPOTENCY_KEY="happy")
    assert r.status_code == 200
    ref = r.json()["provider_ref"]
    send(client, "evt_normal", ref, "SUCCESS")
    p = Payment.objects.get(provider_ref=ref)
    assert not p.flagged_for_refund, "the only payment for this booking was flagged for refund"


@pytest.mark.django_db
def test_payment_endpoint_does_not_reveal_which_booking_ids_exist(client, ct):
    other = User.objects.create_user(username="other", email="o@example.com", password="Password!123")
    theirs = make_booking(other, ct)
    r_theirs = client.post("/api/v1/payments/", {"booking": theirs.id}, format="json", HTTP_IDEMPOTENCY_KEY="a")
    r_missing = client.post("/api/v1/payments/", {"booking": 999999}, format="json", HTTP_IDEMPOTENCY_KEY="b")
    assert r_theirs.status_code == r_missing.status_code, \
        f"someone else's booking -> {r_theirs.status_code}, nonexistent -> {r_missing.status_code}"
