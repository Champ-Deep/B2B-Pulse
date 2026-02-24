"""Tests for automation settings endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_default_settings(client: AsyncClient, auth_headers: dict):
    response = await client.get("/api/automation/settings", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["risk_profile"] == "safe"
    assert data["quiet_hours_start"] == "22:00"
    assert data["quiet_hours_end"] == "07:00"
    assert data["polling_interval"] == 300


@pytest.mark.asyncio
async def test_update_settings(client: AsyncClient, auth_headers: dict):
    response = await client.put(
        "/api/automation/settings",
        headers=auth_headers,
        json={
            "risk_profile": "aggro",
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "06:00",
            "polling_interval": 600,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["risk_profile"] == "aggro"
    assert data["polling_interval"] == 600


@pytest.mark.asyncio
async def test_settings_persist(client: AsyncClient, auth_headers: dict):
    # Save settings
    await client.put(
        "/api/automation/settings",
        headers=auth_headers,
        json={
            "risk_profile": "aggro",
            "quiet_hours_start": "21:00",
            "quiet_hours_end": "08:00",
            "polling_interval": 900,
        },
    )

    # Read back
    response = await client.get("/api/automation/settings", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["risk_profile"] == "aggro"
    assert data["quiet_hours_start"] == "21:00"
    assert data["quiet_hours_end"] == "08:00"
    assert data["polling_interval"] == 900


@pytest.mark.asyncio
async def test_settings_invalid_risk_profile(client: AsyncClient, auth_headers: dict):
    response = await client.put(
        "/api/automation/settings",
        headers=auth_headers,
        json={
            "risk_profile": "yolo",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "polling_interval": 300,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_settings_invalid_time_format(client: AsyncClient, auth_headers: dict):
    response = await client.put(
        "/api/automation/settings",
        headers=auth_headers,
        json={
            "risk_profile": "safe",
            "quiet_hours_start": "10pm",
            "quiet_hours_end": "07:00",
            "polling_interval": 300,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_settings_polling_interval_too_low(client: AsyncClient, auth_headers: dict):
    response = await client.put(
        "/api/automation/settings",
        headers=auth_headers,
        json={
            "risk_profile": "safe",
            "quiet_hours_start": "22:00",
            "quiet_hours_end": "07:00",
            "polling_interval": 10,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_settings_requires_auth(client: AsyncClient):
    response = await client.get("/api/automation/settings")
    assert response.status_code == 403
