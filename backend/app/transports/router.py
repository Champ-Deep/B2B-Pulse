"""
LinkedIn transport factory + composite router.

:class:`CompositeTransport` tries the mobile-API transport first and
transparently falls back to the browser when the mobile transport signals it
can't handle the action (``TransportUnavailable``) or the account hits a
verification wall (``TransportChallenge``). This is the single seam every
caller uses; neither caller nor transport knows about the other.

The factory that assembles one lives in ``app.services.account_service`` --
it needs to know how to build the browser executor, which is B2B Pulse
specific, so it does not belong in the routing layer.
"""

from __future__ import annotations

from typing import Any, Optional

from app.transports.base import (
    ACTION_METHODS,
    LinkedInTransport,
    TransportChallenge,
    TransportResult,
    TransportUnavailable,
)

_FALLBACK_ON = (TransportUnavailable, TransportChallenge)


class CompositeTransport:
    """Routes an action to ``primary`` first, falling back to ``fallback``."""

    name = "composite"

    def __init__(
        self,
        primary: LinkedInTransport,
        fallback: Optional[LinkedInTransport] = None,
        fallback_on: tuple = _FALLBACK_ON,
    ):
        self.primary = primary
        self.fallback = fallback
        self.fallback_on = fallback_on

    async def _dispatch(self, action: str, *args, **kwargs) -> TransportResult:
        try:
            return await getattr(self.primary, action)(*args, **kwargs)
        except self.fallback_on as exc:
            if self.fallback is None:
                return TransportResult(
                    success=False, action=action, via=self.primary.name, error=str(exc)
                )
            try:
                result = await getattr(self.fallback, action)(*args, **kwargs)
                if result.detail is None:
                    result.detail = {}
                result.detail["fell_back_from"] = self.primary.name
                result.detail["fallback_reason"] = str(exc)
                return result
            except self.fallback_on as exc2:
                return TransportResult(
                    success=False, action=action, via=self.fallback.name, error=str(exc2)
                )

    async def like(self, account: Any, activity_urn: str) -> TransportResult:
        return await self._dispatch("like", account, activity_urn)

    async def comment(self, account: Any, activity_urn: str, text: str) -> TransportResult:
        return await self._dispatch("comment", account, activity_urn, text)

    async def follow(self, account: Any, member_urn: str) -> TransportResult:
        return await self._dispatch("follow", account, member_urn)

    async def connect(self, account: Any, member_urn: str, note: Optional[str] = None) -> TransportResult:
        return await self._dispatch("connect", account, member_urn, note)

    async def send_message(self, account: Any, member_urn: str, text: str) -> TransportResult:
        return await self._dispatch("send_message", account, member_urn, text)

    async def create_post(self, account: Any, body: str, media: Any = None) -> TransportResult:
        return await self._dispatch("create_post", account, body, media)

    async def fetch_activity(self, account: Any, member_urn: str) -> TransportResult:
        return await self._dispatch("fetch_activity", account, member_urn)

    async def fetch_inbox(self, account: Any, since: Any = None) -> TransportResult:
        return await self._dispatch("fetch_inbox", account, since)

    async def fetch_profile(self, account: Any, public_id: str) -> TransportResult:
        return await self._dispatch("fetch_profile", account, public_id)

    async def fetch_connections(self, account: Any, since: Any = None) -> TransportResult:
        return await self._dispatch("fetch_connections", account, since)

    async def whoami(self, account: Any) -> TransportResult:
        return await self._dispatch("whoami", account)


# Ensure the composite implements the full action surface (guards drift).
assert all(hasattr(CompositeTransport, m) for m in ACTION_METHODS)
