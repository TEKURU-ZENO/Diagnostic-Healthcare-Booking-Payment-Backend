from unittest.mock import MagicMock

import pytest
from django.db import connection
from rest_framework.test import APIClient


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


@pytest.mark.django_db
def test_healthz_db_failure_does_not_leak_details(monkeypatch):
    mock_cursor = MagicMock()
    mock_cursor.__enter__.side_effect = Exception(
        "psycopg.OperationalError: password=supersecret host=db.internal.net port=5432"
    )
    monkeypatch.setattr(connection, "cursor", lambda: mock_cursor)

    client = APIClient()
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "disconnected"}
    assert "detail" not in response.json()
    assert "supersecret" not in response.content.decode()

