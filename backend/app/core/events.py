import json
import logging
from typing import Any

import redis.asyncio as redis_async

from app.config import settings

logger = logging.getLogger(__name__)


async def broadcast_event(org_id: str | Any, event_type: str, payload: dict):
    """
    Publish an event to the Redis channel for a specific organization.
    WebSocket clients subscribed to `events:org:<org_id>` will receive it.

    Args:
        org_id: The UUID or string of the organization.
        event_type: A string identifier for the event (e.g., 'activity_feed', 'poll_status').
        payload: A dictionary of data related to the event.
    """
    try:
        r = redis_async.from_url(settings.redis_url)
        channel = f"events:org:{str(org_id)}"

        message = {
            "type": event_type,
            "payload": payload
        }

        await r.publish(channel, json.dumps(message))
    except Exception as e:
        logger.error(f"Failed to broadcast event {event_type} to {org_id}: {e}")
    finally:
        try:
            await r.close()
        except Exception:
            pass
