import asyncio
import logging
import uuid

import redis.asyncio as redis_async
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import decode_token
from app.database import get_db
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["websockets"])


async def get_ws_current_user(token: str, db: AsyncSession) -> User | None:
    """Validate a token for WebSocket connections."""
    try:
        payload = decode_token(token)
        if payload is None or payload.get("type") != "access":
            return None

        user_id = payload.get("sub")
        if not user_id:
            return None

        result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()

        if user is None or not user.is_active:
            return None

        return user
    except Exception as e:
        logger.warning(f"WebSocket auth failed: {e}")
        return None


@router.websocket("/events")
async def websocket_events(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token"),
    db: AsyncSession = Depends(get_db),
):
    """
    WebSocket endpoint for real-time events.
    Clients subscribe to receive events for their organization (e.g. recent activity, poll statuses).
    """
    # Accept the connection early to be able to send custom close codes or error messages if desired,
    # but since token is a query parameter, we can also fail early.
    user = await get_ws_current_user(token, db)
    if not user:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    logger.info(f"WebSocket connected for user {user.id} (org {user.org_id})")

    # Initialize redis connection
    r = redis_async.from_url(settings.redis_url)
    pubsub = r.pubsub()

    channel_name = f"events:org:{user.org_id}"
    await pubsub.subscribe(channel_name)
    logger.info(f"Subscribed to Redis channel: {channel_name}")

    redis_task = None
    try:
        # A task to listen for Redis messages and forward them to the WebSocket
        async def listen_redis():
            try:
                async for message in pubsub.listen():
                    if message["type"] == "message":
                        try:
                            data = message["data"].decode("utf-8")
                            await websocket.send_text(data)
                        except Exception as e:
                            logger.error(f"Error sending message to websocket: {e}")
            except Exception as e:
                 logger.error(f"Redis listener task error: {e}")

        redis_task = asyncio.create_task(listen_redis())

        # Keep the connection open and listen for client disconnects
        while True:
            # We are currently only expecting server-to-client events,
            # but we need to listen to detect disconnects.
            await websocket.receive_text()

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for user {user.id}")
    except Exception as e:
        logger.error(f"WebSocket error for user {user.id}: {e}")
    finally:
        if redis_task:
            redis_task.cancel()
        await pubsub.unsubscribe(channel_name)
        await pubsub.close()
        await r.close()
