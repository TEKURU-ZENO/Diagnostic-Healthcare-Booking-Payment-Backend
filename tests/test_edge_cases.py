"""Edge cases found in review. Each asserts the correct behaviour and failed on the original code."""
import json
import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, connections
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


@pytest.mark.django_db
def test_simulate_outcome_rejected_when_mock_payments_disabled(client, user, ct):
    b = make_booking(user, ct)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(settings, "MOCK_PAYMENTS_ENABLED", False)
        r = client.post(
            "/api/v1/payments/",
            {"booking": b.id, "simulate_outcome": "SUCCESS"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="mock_dis_key",
        )
        assert r.status_code == 400
        assert "Simulating payment outcomes is disabled" in str(r.json())


@pytest.mark.django_db
def test_catalog_invalid_filter_centre_id_not_500(client):
    r = client.get("/api/v1/catalog/centre-tests/?centre=abc")
    assert r.status_code == 200
    assert r.json()["results"] == []


@pytest.mark.django_db
def test_catalog_protected_error_returns_409(ct, user):
    admin_user = User.objects.create_superuser(
        username="admin_user", email="admin_user@example.com", password="Password!123"
    )
    admin_client = APIClient()
    admin_client.force_authenticate(admin_user)
    make_booking(user, ct)
    # Attempting to delete centre_test that is referenced by a booking
    r = admin_client.delete(f"/api/v1/catalog/centre-tests/{ct.id}/")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "resource_protected"


@pytest.mark.django_db
def test_request_id_sanitization_rejects_malicious_or_long_header(client):
    # Malicious injection
    r1 = client.get("/healthz", HTTP_X_REQUEST_ID="<script>bad()</script>")
    assert r1.status_code == 200
    assert r1.headers["X-Request-ID"] != "<script>bad()</script>"
    # Length > 64 chars
    long_id = "a" * 65
    r2 = client.get("/healthz", HTTP_X_REQUEST_ID=long_id)
    assert r2.status_code == 200
    assert r2.headers["X-Request-ID"] != long_id
    # Valid ID preserved
    valid_id = "req_test_12345"
    r3 = client.get("/healthz", HTTP_X_REQUEST_ID=valid_id)
    assert r3.status_code == 200
    assert r3.headers["X-Request-ID"] == valid_id


@pytest.mark.django_db
def test_signup_case_insensitive_and_generic_error():
    client = APIClient()
    User.objects.create_user(username="Alice", email="Alice@example.com", password="Password123!")
    # Attempt with lowercase username
    r1 = client.post(
        "/api/v1/auth/signup/",
        {
            "username": "alice",
            "email": "diff@example.com",
            "password": "StrongPassword!2026",
        },
        format="json",
    )
    assert r1.status_code == 400
    assert "already exists" in str(r1.json())

    # Attempt with uppercase email
    r2 = client.post(
        "/api/v1/auth/signup/",
        {
            "username": "diff_user",
            "email": "ALICE@EXAMPLE.COM",
            "password": "StrongPassword!2026",
        },
        format="json",
    )
    assert r2.status_code == 400
    assert "already exists" in str(r2.json())


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Database concurrency and row-level locking tests require PostgreSQL engine",
)
@pytest.mark.django_db(transaction=True)
def test_concurrent_checkout_twenty_requests(user, ct):
    b = make_booking(user, ct)
    status_codes = []

    def attempt_checkout(idx):
        try:
            cl = APIClient()
            cl.force_authenticate(user)
            res = cl.post(
                "/api/v1/payments/",
                {"booking": b.id, "simulate_outcome": "SUCCESS"},
                format="json",
                HTTP_IDEMPOTENCY_KEY=f"key_race_{idx}",
            )
            status_codes.append(res.status_code)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt_checkout, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(status_codes) == 20
    # Exactly 1 success (200) and 19 conflicts (409)
    assert status_codes.count(200) == 1
    assert status_codes.count(409) == 19
    # Exactly one payment row was created
    assert Payment.objects.filter(booking=b).count() == 1


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="Database concurrency and row-level locking tests require PostgreSQL engine",
)
@pytest.mark.django_db(transaction=True)
def test_concurrent_double_booking(user, ct):
    slot = (timezone.now() + timedelta(days=5)).isoformat()
    status_codes = []

    def attempt_booking():
        try:
            cl = APIClient()
            cl.force_authenticate(user)
            res = cl.post(
                "/api/v1/bookings/",
                {"centre_test": ct.id, "appointment_at": slot},
                format="json",
            )
            status_codes.append(res.status_code)
        finally:
            connections.close_all()

    threads = [threading.Thread(target=attempt_booking) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(status_codes) == 10
    assert status_codes.count(201) == 1
    assert status_codes.count(409) == 9
    assert Booking.objects.filter(centre_test=ct, appointment_at=slot).count() == 1

