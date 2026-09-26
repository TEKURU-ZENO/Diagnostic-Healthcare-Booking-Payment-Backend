from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.bookings.models import Booking, BookingStatus, BookingStatusHistory
from apps.catalog.models import CentreTest, DiagnosticCentre, DiagnosticTest

User = get_user_model()


@pytest.fixture
def user_a():
    return User.objects.create_user(
        username="user_a", email="a@example.com", password="Password!123"
    )


@pytest.fixture
def user_b():
    return User.objects.create_user(
        username="user_b", email="b@example.com", password="Password!123"
    )


@pytest.fixture
def centre_test():
    centre = DiagnosticCentre.objects.create(name="Central Lab", city="Mumbai", address="Main St")
    test = DiagnosticTest.objects.create(name="Thyroid Test", code="THY_100")
    return CentreTest.objects.create(centre=centre, test=test, price=Decimal("650.00"), is_active=True)


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_price_snapshot_integrity(api_client, user_a, centre_test):
    api_client.force_authenticate(user=user_a)
    future_time = timezone.now() + timedelta(days=2)

    # 1. Create booking
    res = api_client.post(
        "/api/v1/bookings/",
        {
            "centre_test": centre_test.id,
            "appointment_at": future_time.isoformat(),
            "amount": "1.00",  # Untrusted client attempt to override amount
        },
        format="json",
    )
    assert res.status_code == 201
    booking_id = res.json()["id"]

    booking = Booking.objects.get(id=booking_id)
    # Price was snapshotted from centre_test (650.00), client input of 1.00 ignored
    assert booking.amount == Decimal("650.00")

    # 2. Later, lab changes price for new customers
    centre_test.price = Decimal("850.00")
    centre_test.save()

    # 3. Existing booking remains untouched
    booking.refresh_from_db()
    assert booking.amount == Decimal("650.00")


@pytest.mark.django_db
def test_appointment_in_past_rejected(api_client, user_a, centre_test):
    api_client.force_authenticate(user=user_a)
    past_time = timezone.now() - timedelta(hours=2)

    res = api_client.post(
        "/api/v1/bookings/",
        {"centre_test": centre_test.id, "appointment_at": past_time.isoformat()},
        format="json",
    )
    assert res.status_code == 400
    assert "error" in res.json()


@pytest.mark.django_db
def test_inactive_test_rejected(api_client, user_a, centre_test):
    centre_test.is_active = False
    centre_test.save()

    api_client.force_authenticate(user=user_a)
    future_time = timezone.now() + timedelta(days=1)

    res = api_client.post(
        "/api/v1/bookings/",
        {"centre_test": centre_test.id, "appointment_at": future_time.isoformat()},
        format="json",
    )
    assert res.status_code == 400


@pytest.mark.django_db
def test_user_b_cannot_view_user_a_booking_returns_404(api_client, user_a, user_b, centre_test):
    # Create booking for user A
    booking = Booking.objects.create(
        user=user_a,
        centre_test=centre_test,
        appointment_at=timezone.now() + timedelta(days=1),
        amount=centre_test.price,
        status=BookingStatus.PENDING,
    )

    # User B queries User A's booking ID
    api_client.force_authenticate(user=user_b)
    res = api_client.get(f"/api/v1/bookings/{booking.id}/")

    # MUST return 404, not 403, to avoid leaking that the booking ID exists
    assert res.status_code == 404


@pytest.mark.django_db
def test_double_booking_prevention_constraint(user_a, centre_test):
    slot_time = timezone.now() + timedelta(days=3)

    # Booking 1 is active (PENDING)
    Booking.objects.create(
        user=user_a,
        centre_test=centre_test,
        appointment_at=slot_time,
        amount=centre_test.price,
        status=BookingStatus.PENDING,
    )

    # Booking 2 for same user, test, and slot fails due to conditional unique constraint
    with pytest.raises(IntegrityError):
        Booking.objects.create(
            user=user_a,
            centre_test=centre_test,
            appointment_at=slot_time,
            amount=centre_test.price,
            status=BookingStatus.PENDING,
        )


@pytest.mark.django_db
def test_cancelling_confirmed_booking_flags_refund(api_client, user_a, centre_test):
    future_time = timezone.now() + timedelta(days=2)
    booking = Booking.objects.create(
        user=user_a,
        centre_test=centre_test,
        appointment_at=future_time,
        amount=centre_test.price,
        status=BookingStatus.CONFIRMED,
    )

    api_client.force_authenticate(user=user_a)
    res = api_client.post(f"/api/v1/bookings/{booking.id}/cancel/")
    assert res.status_code == 200

    booking.refresh_from_db()
    assert booking.status == BookingStatus.CANCELLED
    assert booking.flagged_for_refund is True

    # Audit history check
    history = BookingStatusHistory.objects.filter(booking=booking).last()
    assert history.from_status == BookingStatus.CONFIRMED
    assert history.to_status == BookingStatus.CANCELLED
    assert history.source == "user"


@pytest.mark.django_db
def test_cannot_cancel_past_booking(api_client, user_a, centre_test):
    past_time = timezone.now() - timedelta(minutes=10)
    booking = Booking.objects.create(
        user=user_a,
        centre_test=centre_test,
        appointment_at=past_time,
        amount=centre_test.price,
        status=BookingStatus.PENDING,
    )

    api_client.force_authenticate(user=user_a)
    res = api_client.post(f"/api/v1/bookings/{booking.id}/cancel/")
    assert res.status_code == 400
