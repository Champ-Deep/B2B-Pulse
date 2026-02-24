"""Tests for authentication endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_signup_creates_user(client: AsyncClient):
    response = await client.post(
        "/api/auth/signup",
        json={
            "email": "new@example.com",
            "password": "securepass123",
            "full_name": "New User",
            "org_name": "New Org",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_signup_duplicate_email(client: AsyncClient):
    payload = {
        "email": "dup@example.com",
        "password": "securepass123",
        "full_name": "User",
        "org_name": "Org",
    }
    await client.post("/api/auth/signup", json=payload)
    response = await client.post("/api/auth/signup", json=payload)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    # Signup first
    await client.post(
        "/api/auth/signup",
        json={
            "email": "login@example.com",
            "password": "mypass",
            "full_name": "Login User",
            "org_name": "Org",
        },
    )
    # Login
    response = await client.post(
        "/api/auth/login",
        json={"email": "login@example.com", "password": "mypass"},
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    await client.post(
        "/api/auth/signup",
        json={
            "email": "wrongpass@example.com",
            "password": "correct",
            "full_name": "User",
            "org_name": "Org",
        },
    )
    response = await client.post(
        "/api/auth/login",
        json={"email": "wrongpass@example.com", "password": "incorrect"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_me(client: AsyncClient, auth_headers: dict):
    response = await client.get("/api/auth/me", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "email" in data
    assert data["role"] == "owner"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_refresh_token(client: AsyncClient):
    signup_res = await client.post(
        "/api/auth/signup",
        json={
            "email": "refresh@example.com",
            "password": "pass",
            "full_name": "User",
            "org_name": "Org",
        },
    )
    refresh_token = signup_res.json()["refresh_token"]

    response = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


@pytest.mark.asyncio
async def test_me_without_token(client: AsyncClient):
    response = await client.get("/api/auth/me")
    assert response.status_code == 403
