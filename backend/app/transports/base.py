"""
LinkedIn transport interface.

A *transport* performs a LinkedIn action for a connected account. Two exist:
``MobileAPITransport`` (primary; simulated mobile client, low ban risk) and
``PlaywrightTransport`` (fallback; drives the existing browser automation). The
:class:`CompositeTransport` in ``api_client.py`` routes between them.

Transports signal "I can't handle this, try the fallback" by raising
``TransportUnavailable`` (or ``TransportChallenge`` for a verification wall).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable


class ActionType(str, Enum):
    LIKE = "like"
    COMMENT = "comment"
    FOLLOW = "follow"
    CONNECT = "connect"
    MESSAGE = "message"
    POST = "post"
    FETCH_ACTIVITY = "fetch_activity"
    FETCH_INBOX = "fetch_inbox"
    FETCH_PROFILE = "fetch_profile"
    FETCH_CONNECTIONS = "fetch_connections"
    WHOAMI = "whoami"


@dataclass
class TransportResult:
    """Normalized outcome of a transport action."""
    success: bool
    action: str
    via: str = ""               # "mobile" | "playwright"
    detail: Optional[dict] = None
    error: Optional[str] = None

    def __bool__(self) -> bool:
        return self.success


class TransportError(Exception):
    """Base transport error."""


class TransportUnavailable(TransportError):
    """This transport cannot handle the action/account -> try the fallback."""


class TransportChallenge(TransportError):
    """The account hit a verification/challenge wall -> fallback or safety."""


@runtime_checkable
class LinkedInTransport(Protocol):
    """The contract both transports satisfy. ``account`` is duck-typed with
    ``id``, ``auth_blob``, ``device_fingerprint``, ``proxy`` attributes."""

    name: str

    async def like(self, account: Any, activity_urn: str) -> TransportResult: ...
    async def comment(self, account: Any, activity_urn: str, text: str) -> TransportResult: ...
    async def follow(self, account: Any, member_urn: str) -> TransportResult: ...
    async def connect(self, account: Any, member_urn: str, note: Optional[str] = None) -> TransportResult: ...
    async def send_message(self, account: Any, member_urn: str, text: str) -> TransportResult: ...
    async def create_post(self, account: Any, body: str, media: Any = None) -> TransportResult: ...
    async def fetch_activity(self, account: Any, member_urn: str) -> TransportResult: ...
    async def fetch_inbox(self, account: Any, since: Any = None) -> TransportResult: ...
    async def fetch_profile(self, account: Any, public_id: str) -> TransportResult: ...
    async def fetch_connections(self, account: Any, since: Any = None) -> TransportResult: ...
    async def whoami(self, account: Any) -> TransportResult: ...


# The action methods every transport implements, in one place so the composite
# router and tests can iterate them.
ACTION_METHODS = (
    "like",
    "comment",
    "follow",
    "connect",
    "send_message",
    "create_post",
    "fetch_activity",
    "fetch_inbox",
    "fetch_profile",
    "fetch_connections",
    "whoami",
)
