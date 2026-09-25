from decimal import Decimal
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from rest_framework.test import APIClient
from apps.catalog.models import DiagnosticCentre, DiagnosticTest, CentreTest

User = get_user_model()


@pytest.fixture
def regular_user():
    return User.objects.create_user(
        username="john", email="john@example.com", password="Password!123"
    )


@pytest.fixture
def admin_user():
    return User.objects.create_superuser(
        username="admin", email="admin@example.com", password="AdminPassword!123"
    )


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_non_admin_cannot_create_centre(api_client, regular_user):
    api_client.force_authenticate(user=regular_user)
    payload = {"name": "Metro Diagnostics", "city": "Mumbai", "address": "123 Marine Drive"}
    response = api_client.post("/api/v1/catalog/centres/", payload, format="json")
    assert response.status_code == 403


@pytest.mark.django_db
def test_admin_can_create_catalog_entities(api_client, admin_user):
    api_client.force_authenticate(user=admin_user)

    # 1. Create Centre
    centre_res = api_client.post(
        "/api/v1/catalog/centres/",
        {"name": "Apex Lab", "city": "Bengaluru", "address": "45 MG Road"},
        format="json",
    )
    assert centre_res.status_code == 201
    centre_id = centre_res.json()["id"]

    # 2. Create Test
    test_res = api_client.post(
        "/api/v1/catalog/tests/",
        {"name": "Complete Blood Count", "code": "CBC_01", "description": "Routine blood test"},
        format="json",
    )
    assert test_res.status_code == 201
    test_id = test_res.json()["id"]

    # 3. Create CentreTest pricing
    pricing_res = api_client.post(
        "/api/v1/catalog/centre-tests/",
        {"centre": centre_id, "test": test_id, "price": "499.00", "is_active": True},
        format="json",
    )
    assert pricing_res.status_code == 201
    assert pricing_res.json()["price"] == "499.00"
    assert pricing_res.json()["centre_city"] == "Bengaluru"


@pytest.mark.django_db
def test_unique_centre_test_constraint():
    centre = DiagnosticCentre.objects.create(
        name="Centre A", city="Delhi", address="Connaught Place"
    )
    test = DiagnosticTest.objects.create(name="Lipid Profile", code="LIPID_01")
    CentreTest.objects.create(centre=centre, test=test, price=Decimal("799.00"))

    # Attempting duplicate centre-test pair raises IntegrityError at DB level
    with pytest.raises(IntegrityError):
        CentreTest.objects.create(centre=centre, test=test, price=Decimal("899.00"))


@pytest.mark.django_db
def test_non_positive_price_rejected(api_client, admin_user):
    api_client.force_authenticate(user=admin_user)
    centre = DiagnosticCentre.objects.create(
        name="Centre B", city="Delhi", address="Karol Bagh"
    )
    test = DiagnosticTest.objects.create(name="Thyroid Panel", code="THY_01")

    res = api_client.post(
        "/api/v1/catalog/centre-tests/",
        {"centre": centre.id, "test": test.id, "price": "-10.00"},
        format="json",
    )
    assert res.status_code == 400


@pytest.mark.django_db
def test_filter_centres_by_city(api_client, regular_user):
    api_client.force_authenticate(user=regular_user)
    DiagnosticCentre.objects.create(name="Centre North", city="Mumbai", address="Andheri")
    DiagnosticCentre.objects.create(name="Centre South", city="Pune", address="Kothrud")

    res = api_client.get("/api/v1/catalog/centres/?city=Mumbai")
    assert res.status_code == 200
    data = res.json()
    results = data["results"] if "results" in data else data
    assert len(results) == 1
    assert results[0]["name"] == "Centre North"
