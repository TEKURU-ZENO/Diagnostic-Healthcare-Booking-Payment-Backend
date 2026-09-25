import uuid
from decimal import Decimal
from datetime import timedelta
import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import DiagnosticCentre, DiagnosticTest, CentreTest
from apps.bookings.models import Booking, BookingStatus
from apps.payments.models import Payment, PaymentStatus
from apps.payments.services import apply_payment_result

User = get_user_model()


@pytest.fixture
def user_a():
    return User.objects.create_user(
        username="alice_pay", email="alice_pay@example.com", password="Password!123"
    )


@pytest.fixture
def user_b():
    return User.objects.create_user(
        username="bob_pay", email="bob_pay@example.com", password="Password!123"
    )


@pytest.fixture
def centre_test():
    centre = DiagnosticCentre.objects.create(name="Apex Centre", city="Mumbai", address="Marine Lines")
    test = DiagnosticTest.objects.create(name="Blood Sugar", code="GLUCOSE_01")
    return CentreTest.objects.create(centre=centre, test=test, price=Decimal("350.00"), is_active=True)


@pytest.fixture
def pending_booking(user_a, centre_test):
    return Booking.objects.create(
        user=user_a,
        centre_test=centre_test,
        appointment_at=timezone.now() + timedelta(days=2),
        amount=centre_test.price,
        status=BookingStatus.PENDING,
    )


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_missing_idempotency_key_header_returns_400(api_client, user_a, pending_booking):
    api_client.force_authenticate(user=user_a)
    response = api_client.post(
        "/api/v1/payments/",
        {"booking": pending_booking.id, "simulate_outcome": "SUCCESS"},
        format="json",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_idempotency_key"


@pytest.mark.django_db
def test_idempotent_payment_replay_returns_original_payment(api_client, user_a, pending_booking):
    api_client.force_authenticate(user=user_a)
    idem_key = "idemp-" + uuid.uuid4().hex

    # First attempt: succeeds
    res1 = api_client.post(
        "/api/v1/payments/",
        {"booking": pending_booking.id, "simulate_outcome": "SUCCESS"},
        HTTP_IDEMPOTENCY_KEY=idem_key,
        format="json",
    )
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "SUCCESS"
    payment_id = data1["id"]

    # Second attempt: exact same key and booking -> returns identical payment without duplicate charge
    res2 = api_client.post(
        "/api/v1/payments/",
        {"booking": pending_booking.id, "simulate_outcome": "SUCCESS"},
        HTTP_IDEMPOTENCY_KEY=idem_key,
        format="json",
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["id"] == payment_id
    assert Payment.objects.filter(booking=pending_booking).count() == 1


@pytest.mark.django_db
def test_idempotency_key_reuse_different_booking_returns_422(
    api_client, user_a, centre_test, pending_booking
):
    api_client.force_authenticate(user=user_a)
    idem_key = "shared-key-" + uuid.uuid4().hex

    # Pay for booking 1
    res1 = api_client.post(
        "/api/v1/payments/",
        {"booking": pending_booking.id, "simulate_outcome": "SUCCESS"},
        HTTP_IDEMPOTENCY_KEY=idem_key,
        format="json",
    )
    assert res1.status_code == 200

    # Create booking 2
    booking_2 = Booking.objects.create(
        user=user_a,
        centre_test=centre_test,
        appointment_at=timezone.now() + timedelta(days=3),
        amount=centre_test.price,
        status=BookingStatus.PENDING,
    )

    # Attempt to use same idempotency key for booking 2
    res2 = api_client.post(
        "/api/v1/payments/",
        {"booking": booking_2.id, "simulate_outcome": "SUCCESS"},
        HTTP_IDEMPOTENCY_KEY=idem_key,
        format="json",
    )
    assert res2.status_code == 422
    assert res2.json()["error"]["code"] == "idempotency_payload_mismatch"


@pytest.mark.django_db
def test_idempotency_keys_are_scoped_per_user(api_client, user_a, user_b, centre_test):
    # Booking A for User A, Booking B for User B
    b_a = Booking.objects.create(
        user=user_a,
        centre_test=centre_test,
        appointment_at=timezone.now() + timedelta(days=1),
        amount=centre_test.price,
        status=BookingStatus.PENDING,
    )
    b_b = Booking.objects.create(
        user=user_b,
        centre_test=centre_test,
        appointment_at=timezone.now() + timedelta(days=2),
        amount=centre_test.price,
        status=BookingStatus.PENDING,
    )

    shared_key = "common-client-key-12345"

    # User A pays with key
    api_client.force_authenticate(user=user_a)
    res_a = api_client.post(
        "/api/v1/payments/",
        {"booking": b_a.id, "simulate_outcome": "SUCCESS"},
        HTTP_IDEMPOTENCY_KEY=shared_key,
        format="json",
    )
    assert res_a.status_code == 200

    # User B pays with identical key string: does not collide or leak A's payment!
    api_client.force_authenticate(user=user_b)
    res_b = api_client.post(
        "/api/v1/payments/",
        {"booking": b_b.id, "simulate_outcome": "SUCCESS"},
        HTTP_IDEMPOTENCY_KEY=shared_key,
        format="json",
    )
    assert res_b.status_code == 200
    assert res_b.json()["id"] != res_a.json()["id"]
    assert res_b.json()["booking"] == b_b.id


@pytest.mark.django_db
def test_paying_for_confirmed_or_cancelled_booking_returns_409(
    api_client, user_a, pending_booking
):
    api_client.force_authenticate(user=user_a)

    # 1. Booking confirmed
    pending_booking.status = BookingStatus.CONFIRMED
    pending_booking.save()

    res = api_client.post(
        "/api/v1/payments/",
        {"booking": pending_booking.id, "simulate_outcome": "SUCCESS"},
        HTTP_IDEMPOTENCY_KEY="key-" + uuid.uuid4().hex,
        format="json",
    )
    assert res.status_code == 409

    # 2. Booking cancelled
    pending_booking.status = BookingStatus.CANCELLED
    pending_booking.save()

    res2 = api_client.post(
        "/api/v1/payments/",
        {"booking": pending_booking.id, "simulate_outcome": "SUCCESS"},
        HTTP_IDEMPOTENCY_KEY="key-" + uuid.uuid4().hex,
        format="json",
    )
    assert res2.status_code == 409


@pytest.mark.django_db
def test_user_b_paying_for_user_a_booking_returns_404(api_client, user_b, pending_booking):
    api_client.force_authenticate(user=user_b)
    res = api_client.post(
        "/api/v1/payments/",
        {"booking": pending_booking.id, "simulate_outcome": "SUCCESS"},
        HTTP_IDEMPOTENCY_KEY="key-" + uuid.uuid4().hex,
        format="json",
    )
    assert res.status_code == 404


@pytest.mark.django_db
def test_double_charge_on_confirmed_booking_flags_refund(user_a, pending_booking):
    # Initial payment confirms booking
    p1 = Payment.objects.create(
        user=user_a,
        booking=pending_booking,
        amount=pending_booking.amount,
        status=PaymentStatus.PENDING,
        provider_ref="ref_initial",
        idempotency_key="idemp_1",
    )
    apply_payment_result(p1, status=PaymentStatus.SUCCESS, source="api")
    pending_booking.refresh_from_db()
    assert pending_booking.status == BookingStatus.CONFIRMED

    # A second payment arrives late with SUCCESS (e.g. from an earlier retry)
    p2 = Payment.objects.create(
        user=user_a,
        booking=pending_booking,
        amount=pending_booking.amount,
        status=PaymentStatus.PENDING,
        provider_ref="ref_late_duplicate",
        idempotency_key="idemp_2",
    )
    apply_payment_result(p2, status=PaymentStatus.SUCCESS, source="webhook")

    p2.refresh_from_db()
    assert p2.status == PaymentStatus.SUCCESS
    # Verified: flagged for refund rather than corrupting state
    assert p2.flagged_for_refund is True
