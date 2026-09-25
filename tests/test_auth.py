import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_successful_signup(api_client):
    payload = {
        "username": "alice",
        "email": "alice@example.com",
        "password": "StrongPassword!2026",
    }
    response = api_client.post("/api/v1/auth/signup/", payload, format="json")
    assert response.status_code == 201
    data = response.json()
    assert data["user"]["username"] == "alice"
    assert data["user"]["email"] == "alice@example.com"
    assert "tokens" in data
    assert "access" in data["tokens"]
    assert "refresh" in data["tokens"]

    # Verify user saved in DB
    user = User.objects.get(username="alice")
    assert user.email == "alice@example.com"
    assert user.check_password("StrongPassword!2026")


@pytest.mark.django_db
def test_signup_duplicate_email(api_client):
    User.objects.create_user(
        username="existing", email="alice@example.com", password="Password123!"
    )
    payload = {
        "username": "new_alice",
        "email": "alice@example.com",
        "password": "StrongPassword!2026",
    }
    response = api_client.post("/api/v1/auth/signup/", payload, format="json")
    assert response.status_code == 400
    assert "error" in response.json()


@pytest.mark.django_db
def test_signup_duplicate_username(api_client):
    User.objects.create_user(
        username="alice", email="first@example.com", password="Password123!"
    )
    payload = {
        "username": "alice",
        "email": "second@example.com",
        "password": "StrongPassword!2026",
    }
    response = api_client.post("/api/v1/auth/signup/", payload, format="json")
    assert response.status_code == 400
    assert "error" in response.json()


@pytest.mark.django_db
def test_signup_weak_password(api_client):
    # Short password (< 8 chars)
    payload = {
        "username": "bob",
        "email": "bob@example.com",
        "password": "123",
    }
    response = api_client.post("/api/v1/auth/signup/", payload, format="json")
    assert response.status_code == 400
    assert "error" in response.json()


@pytest.mark.django_db
def test_login_success(api_client):
    User.objects.create_user(
        username="charlie", email="charlie@example.com", password="SecurePassword!123"
    )
    response = api_client.post(
        "/api/v1/auth/login/",
        {"username": "charlie", "password": "SecurePassword!123"},
        format="json",
    )
    assert response.status_code == 200
    data = response.json()
    assert "access" in data
    assert "refresh" in data


@pytest.mark.django_db
def test_login_invalid_credentials(api_client):
    User.objects.create_user(
        username="charlie", email="charlie@example.com", password="SecurePassword!123"
    )
    response = api_client.post(
        "/api/v1/auth/login/",
        {"username": "charlie", "password": "WrongPassword!"},
        format="json",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_protected_me_endpoint(api_client):
    # Unauthenticated
    res = api_client.get("/api/v1/auth/me/")
    assert res.status_code == 401

    # Authenticated
    user = User.objects.create_user(
        username="david", email="david@example.com", password="Password!123"
    )
    api_client.force_authenticate(user=user)
    res = api_client.get("/api/v1/auth/me/")
    assert res.status_code == 200
    assert res.json()["username"] == "david"
    assert res.json()["email"] == "david@example.com"
