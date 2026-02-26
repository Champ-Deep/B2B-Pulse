"""Redis-based distributed locking for preventing concurrent browser actions.

Usage in Celery tasks (sync):
    from app.core.locks import acquire_user_lock, ReleaseLock

    lock = acquire_user_lock(user_id, "linkedin_action")
    if not lock:
        raise Exception("User is busy, retry later")  # Celery will retry

    try:
        # Do work
        await like_post(user_id, post_url)
    finally:
        lock.release()

Usage with context manager:
    from app.core.locks import user_lock_sync

    with user_lock_sync(user_id, "linkedin_action"):
        # Do work
        pass
"""

import logging
import uuid
from contextlib import contextmanager
from typing import Generator, Optional

import redis as sync_redis

from app.config import settings

logger = logging.getLogger(__name__)

LOCK_PREFIX = "autoengage:user_lock:"
LOCK_TTL = 120  # Lock expires after 2 minutes if not released


def get_redis() -> sync_redis.Redis:
    """Get Redis client."""
    return sync_redis.from_url(settings.redis_url)


class UserLock:
    """Redis-based lock to prevent concurrent actions for a user.

    This is a sync implementation that works in Celery tasks.
    """

    def __init__(self, user_id: str, action: str, ttl: int = LOCK_TTL):
        self.user_id = user_id
        self.action = action
        self.ttl = ttl
        self.lock_key = f"{LOCK_PREFIX}{action}:{user_id}"
        self._redis: Optional[sync_redis.Redis] = None
        self._lock_id = str(uuid.uuid4())
        self._acquired = False

    @property
    def redis(self) -> sync_redis.Redis:
        if self._redis is None:
            self._redis = get_redis()
        return self._redis

    def acquire(self, blocking: bool = False, timeout: int = 30) -> bool:
        """Attempt to acquire the lock.

        Args:
            blocking: If True, wait for lock to become available
            timeout: Max seconds to wait if blocking

        Returns:
            True if lock acquired, False otherwise
        """
        if blocking:
            import time

            start_time = time.time()
            while time.time() - start_time < timeout:
                if self._try_acquire():
                    return True
                time.sleep(1)
            return False
        else:
            return self._try_acquire()

    def _try_acquire(self) -> bool:
        """Try to acquire lock using Redis SETNX."""
        result = self.redis.set(
            self.lock_key,
            self._lock_id,
            nx=True,  # Only set if not exists
            ex=self.ttl,  # Expire after TTL
        )
        if result:
            self._acquired = True
            logger.debug(f"Acquired lock {self.lock_key}")
        return bool(result)

    def release(self) -> bool:
        """Release the lock if we own it.

        Returns:
            True if lock was released, False if we didn't own it
        """
        if not self._acquired:
            return False

        # Lua script to atomically check and delete
        # This prevents releasing a lock that was expired and re-acquired by another process
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            result = self.redis.eval(lua_script, 1, self.lock_key, self._lock_id)
            self._acquired = not bool(result)
            if result:
                logger.debug(f"Released lock {self.lock_key}")
            return bool(result)
        except Exception as e:
            logger.warning(f"Error releasing lock {self.lock_key}: {e}")
            return False

    def extend(self, additional_time: int = 30) -> bool:
        """Extend the lock TTL if we own it.

        Returns:
            True if extended, False otherwise
        """
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("expire", KEYS[1], ARGV[2])
        else
            return 0
        end
        """
        try:
            result = self.redis.eval(
                lua_script, 1, self.lock_key, self._lock_id, self.ttl + additional_time
            )
            return bool(result)
        except Exception as e:
            logger.warning(f"Error extending lock {self.lock_key}: {e}")
            return False


def acquire_user_lock(
    user_id: str, action: str, blocking: bool = False, timeout: int = 30
) -> Optional[UserLock]:
    """Try to acquire a user lock.

    Returns:
        UserLock if acquired, None if couldn't acquire
    """
    lock = UserLock(user_id, action)
    if lock.acquire(blocking=blocking, timeout=timeout):
        return lock
    return None


@contextmanager
def user_lock_sync(
    user_id: str, action: str, blocking: bool = False, timeout: int = 30
) -> Generator[UserLock, None, None]:
    """Context manager for user lock (sync version for Celery).

    Usage:
        with user_lock_sync(user_id, "linkedin_like"):
            await like_post(user_id, post_url)

    Raises:
        RuntimeError: If lock cannot be acquired
    """
    lock = acquire_user_lock(user_id, action, blocking=blocking, timeout=timeout)
    if not lock:
        raise RuntimeError(f"Could not acquire lock for {user_id}:{action}")

    try:
        yield lock
    finally:
        lock.release()


def is_user_locked(user_id: str, action: str) -> bool:
    """Check if a user lock exists without acquiring it.

    Returns:
        True if lock exists, False otherwise
    """
    lock = UserLock(user_id, action)
    return lock.redis.exists(lock.lock_key) > 0
