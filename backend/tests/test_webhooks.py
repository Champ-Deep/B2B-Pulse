"""Tests for WhatsApp webhook endpoint."""

import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import Platform
from app.models.org import Org
from app.models.post import Post
from app.models.tracked_page import TrackedPage


async def _create_tracked_page(db: AsyncSession) -> TrackedPage:
    """Helper: create an org and tracked page for testing."""
    org = Org(name="Test Org")
    db.add(org)
    await db.flush()

    page = TrackedPage(
        org_id=org.id,
        platform=Platform.LINKEDIN,
        external_id="johndoe",
        url="https://www.linkedin.com/in/johndoe",
        name="John Doe",
        page_type="personal",
        active=True,
    )
    db.add(page)
    await db.commit()
    return page


@pytest.mark.asyncio
@patch("app.api.webhooks.schedule_staggered_engagements")
async def test_webhook_creates_post_and_enqueues(mock_task, client: AsyncClient, db: AsyncSession):
    page = await _create_tracked_page(db)

    response = await client.post(
        "/api/webhooks/whatsapp-link",
        json={
            "url": "https://www.linkedin.com/posts/johndoe_test-post-7123456789-abcd",
            "group_name": "Marketing Team",
            "sender": "Alice",
            "timestamp": "2025-01-01T12:00:00Z",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "matched"
    assert "post_id" in data

    # Verify Post was created
    result = await db.execute(select(Post))
    post = result.scalar_one()
    assert post.tracked_page_id == page.id
    assert post.platform == Platform.LINKEDIN

    # Verify Celery task was called
    mock_task.delay.assert_called_once_with(str(post.id), str(page.id))


@pytest.mark.asyncio
@patch("app.api.webhooks.schedule_staggered_engagements")
async def test_webhook_deduplicates(mock_task, client: AsyncClient, db: AsyncSession):
    await _create_tracked_page(db)
    url = "https://www.linkedin.com/posts/johndoe_same-post-7123456789-abcd"

    # First call
    res1 = await client.post(
        "/api/webhooks/whatsapp-link",
        json={"url": url, "group_name": "Group", "sender": "Bob", "timestamp": "2025-01-01T12:00:00Z"},
    )
    assert res1.json()["status"] == "matched"

    # Second call with same URL
    res2 = await client.post(
        "/api/webhooks/whatsapp-link",
        json={"url": url, "group_name": "Group", "sender": "Bob", "timestamp": "2025-01-01T12:01:00Z"},
    )
    assert res2.json()["status"] == "duplicate"

    # Celery should only have been called once
    assert mock_task.delay.call_count == 1


@pytest.mark.asyncio
async def test_webhook_ignores_non_social_url(client: AsyncClient):
    response = await client.post(
        "/api/webhooks/whatsapp-link",
        json={
            "url": "https://www.google.com/search?q=test",
            "group_name": "Group",
            "sender": "Eve",
            "timestamp": "2025-01-01T12:00:00Z",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


@pytest.mark.asyncio
async def test_webhook_unmatched_url(client: AsyncClient, db: AsyncSession):
    # No tracked pages — URL should be unmatched
    response = await client.post(
        "/api/webhooks/whatsapp-link",
        json={
            "url": "https://www.linkedin.com/posts/unknown_test-7123456789-abcd",
            "group_name": "Group",
            "sender": "Eve",
            "timestamp": "2025-01-01T12:00:00Z",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "unmatched"
