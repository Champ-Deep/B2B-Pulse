"""
Playwright transport — the fallback path, bound to B2B Pulse's browser actions.

This is the piece the merge unlocks on both sides.

Social Bot had the transport abstraction and a Voyager (mobile-API) primary,
but its Playwright fallback was never bound to a real executor — so when a
Voyager endpoint shape drifted, the action simply failed. B2B Pulse had working
Playwright automation but no lower-risk primary and no fallback structure. Put
together: Voyager first because it is far less detectable than driving headless
Chromium, and these battle-tested browser actions underneath so a drifted
endpoint costs throughput rather than the action.

The adapter is thin on purpose. ``app/automation/linkedin_actions.py`` keys off
``user_id`` and post URLs; the transport contract speaks account objects and
URNs. Everything here is translation between those two vocabularies — no
automation logic lives in this file, so improvements to the browser actions
benefit both callers automatically.
"""

from __future__ import annotations

import logging
from typing import Any

from app.transports.base import TransportResult, TransportUnavailable

logger = logging.getLogger(__name__)


def _activity_url(reference: str) -> str:
    """
    Turn a URN or a URL into something a browser can navigate to.

    Voyager talks in ``urn:li:activity:<id>``; Playwright needs a permalink.
    """
    text = str(reference or "")
    if text.startswith("http"):
        return text
    if text.startswith("urn:li:"):
        activity_id = text.rsplit(":", 1)[-1]
        return f"https://www.linkedin.com/feed/update/urn:li:activity:{activity_id}/"
    return f"https://www.linkedin.com/feed/update/urn:li:activity:{text}/"


def _profile_url(reference: str) -> str:
    text = str(reference or "")
    if text.startswith("http"):
        return text
    handle = text.rsplit(":", 1)[-1] if ":" in text else text
    return f"https://www.linkedin.com/in/{handle}"


def _owner_id(account: Any) -> str:
    """
    The id ``linkedin_actions`` keys its browser session on.

    Prefer the owning user, because that is what the existing cookie lookup
    uses; fall back to the account id so a duck-typed account still works.
    """
    for attribute in ("user_id", "owner_id", "id"):
        value = getattr(account, attribute, None)
        if value:
            return str(value)
    raise TransportUnavailable("account has no identifier for a browser session")


class PlaywrightTransport:
    """Drives LinkedIn through a real browser session."""

    name = "playwright"

    def __init__(self, actions: Any = None):
        # Injectable so tests don't need a browser.
        self._actions = actions

    def _load(self):
        if self._actions is not None:
            return self._actions
        from app.automation import linkedin_actions

        self._actions = linkedin_actions
        return self._actions

    async def _call(self, name: str, *args) -> Any:
        actions = self._load()
        fn = getattr(actions, name, None)
        if fn is None:
            raise TransportUnavailable(f"browser automation has no {name}")
        try:
            return await fn(*args)
        except Exception as exc:  # a browser failure must not escape as a crash
            raise TransportUnavailable(f"{name} failed in the browser: {exc}") from exc

    # --- Actions --------------------------------------------------------

    async def like(self, account: Any, activity_urn: str) -> TransportResult:
        ok = await self._call("like_post", _owner_id(account), _activity_url(activity_urn))
        return TransportResult(
            success=bool(ok), action="like", via=self.name,
            error=None if ok else "browser reported the like did not land",
        )

    async def comment(self, account: Any, activity_urn: str, text: str) -> TransportResult:
        ok = await self._call(
            "comment_on_post", _owner_id(account), _activity_url(activity_urn), text
        )
        return TransportResult(
            success=bool(ok), action="comment", via=self.name,
            error=None if ok else "browser reported the comment did not land",
        )

    async def fetch_activity(self, account: Any, member_urn: str) -> TransportResult:
        posts = await self._call(
            "scrape_profile_posts", _profile_url(member_urn), None
        )
        return TransportResult(
            success=True, action="fetch_activity", via=self.name,
            detail={"posts": posts or []},
        )

    async def whoami(self, account: Any) -> TransportResult:
        """
        Session validity only.

        The browser path can confirm the session works but doesn't cheaply
        yield the member URN, so identity resolution stays with the mobile
        transport. That's an acceptable asymmetry: whoami's job here is
        "is this session alive", which is exactly what it can answer.
        """
        valid = await self._call("check_session_valid", _owner_id(account))
        return TransportResult(
            success=bool(valid), action="whoami", via=self.name,
            detail={"session_valid": bool(valid)},
            error=None if valid else "LinkedIn session is no longer valid",
        )

    # --- Not available through a browser (cleanly, yet) -----------------
    #
    # These raise rather than returning failure, so the composite router
    # reports "no transport could do this" instead of a silent no-op.

    async def follow(self, account: Any, member_urn: str) -> TransportResult:
        raise TransportUnavailable("follow is not implemented in the browser path")

    async def connect(self, account: Any, member_urn: str, note: str | None = None) -> TransportResult:
        raise TransportUnavailable("connect is not implemented in the browser path")

    async def send_message(self, account: Any, member_urn: str, text: str) -> TransportResult:
        raise TransportUnavailable("send_message is not implemented in the browser path")

    async def create_post(self, account: Any, body: str, media: Any = None) -> TransportResult:
        raise TransportUnavailable("create_post is not implemented in the browser path")

    async def fetch_inbox(self, account: Any, since: Any = None) -> TransportResult:
        raise TransportUnavailable("fetch_inbox is not implemented in the browser path")

    async def fetch_profile(self, account: Any, public_id: str) -> TransportResult:
        raise TransportUnavailable("fetch_profile is not implemented in the browser path")

    async def fetch_connections(self, account: Any, since: Any = None) -> TransportResult:
        raise TransportUnavailable("fetch_connections is not implemented in the browser path")
