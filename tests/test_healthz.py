import pytest
from rest_framework.test import APIClient
from django.urls import reverse


@pytest.mark.django_db
def test_healthz_endpoint():
    client = APIClient()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}
    assert "X-Request-ID" in response.headers
    assert len(response.headers["X-Request-ID"]) > 0


@pytest.mark.django_db
def test_custom_request_id_propagated():
    client = APIClient()
    custom_id = "test-request-id-12345"
    response = client.get("/healthz", HTTP_X_REQUEST_ID=custom_id)
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == custom_id
