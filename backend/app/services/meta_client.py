"""Shared Meta (Facebook/Instagram) Graph API constants and client factory."""

import httpx

from app.config import HTTP_TIMEOUT

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


def get_graph_client() -> httpx.AsyncClient:
    """Create an httpx client configured for Meta Graph API requests."""
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT)
