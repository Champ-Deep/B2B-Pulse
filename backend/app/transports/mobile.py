"""
Mobile/native-API transport (primary).

Talks to LinkedIn's internal Voyager API — the same JSON API the first-party
web and mobile clients use — over a TLS-fingerprinted HTTP session
(``curl_cffi`` impersonation) carrying a stable per-account device fingerprint.
This is the low-ban-risk path that replaces headless-browser automation.

Honest status
-------------
Voyager is an **unofficial, undocumented** API. Endpoint paths and request
bodies drift without notice, and LinkedIn ships several generations of the same
endpoint concurrently. Every action below therefore tries the current-generation
("dash") endpoint first and falls back to the legacy one, and any action that
still fails to find a working shape raises :class:`TransportUnavailable` so the
:class:`CompositeTransport` transparently hands off to Playwright. That
belt-and-braces layering is deliberate: a Voyager change degrades throughput,
it never takes the product down.

Auth material
-------------
``account.auth_blob`` (decrypted upstream) is either a raw ``li_at`` value or a
JSON object ``{"li_at": ..., "jsessionid": ...}``. Voyager requires both: the
``li_at`` session cookie *and* a ``csrf-token`` header whose value equals the
``JSESSIONID`` cookie.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional, Tuple

from app.transports.base import (
    TransportChallenge,
    TransportResult,
    TransportUnavailable,
)
from app.transports.fingerprints import generate_fingerprint, is_stale, li_track

logger = logging.getLogger(__name__)

VOYAGER_BASE = "https://www.linkedin.com/voyager/api"

# Voyager rejects requests that don't look like a first-party client.
_BASE_HEADERS = {
    "accept": "application/vnd.linkedin.normalized+json+2.1",
    "x-restli-protocol-version": "2.0.0",
    "x-li-lang": "en_US",
    "referer": "https://www.linkedin.com/feed/",
}


def parse_auth_blob(auth_blob: Any) -> dict:
    """
    Normalize decrypted auth material into ``{"li_at": ..., "jsessionid": ...}``.

    Accepts a JSON object, a raw ``li_at`` value, or a full cookie string.
    """
    if not auth_blob:
        return {}
    if isinstance(auth_blob, dict):
        creds = auth_blob
    else:
        text = str(auth_blob).strip()
        creds = {}
        if text.startswith("{"):
            try:
                creds = json.loads(text)
            except json.JSONDecodeError:
                creds = {}
        if not creds:
            if "li_at=" in text:
                # A pasted cookie string: pull the pieces we need out of it.
                for part in text.split(";"):
                    if "=" not in part:
                        continue
                    name, _, value = part.strip().partition("=")
                    if name in ("li_at", "JSESSIONID"):
                        creds["jsessionid" if name == "JSESSIONID" else "li_at"] = value
            else:
                creds = {"li_at": text}

    li_at = str(creds.get("li_at") or "").strip().strip('"')
    jsessionid = str(creds.get("jsessionid") or creds.get("JSESSIONID") or "").strip().strip('"')
    return {"li_at": li_at, "jsessionid": jsessionid}


class MobileAPITransport:
    """Voyager-API transport for a connected account."""

    name = "mobile"

    def __init__(self, session_factory=None, timeout: int = 20):
        # session_factory injectable for tests; defaults to a curl_cffi session.
        self._session_factory = session_factory
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Session construction
    # ------------------------------------------------------------------

    def _fingerprint(self, account: Any) -> dict:
        """
        The account's client identity.

        A fingerprint persisted by an older catalogue version is regenerated
        rather than used: the v1 blobs paired a native-app user-agent with a
        browser TLS handshake, so serving one is worse than having none.
        Regeneration is deterministic from the account id, so the replacement is
        itself stable.
        """
        fp = getattr(account, "device_fingerprint", None)
        if fp and not is_stale(fp):
            return fp
        if fp:
            logger.info(
                "Regenerating a stale device fingerprint for account %s",
                getattr(account, "id", "unknown"),
            )
        return generate_fingerprint(str(getattr(account, "id", "unknown")))

    def build_session(self, account: Any):
        """
        Construct a TLS-fingerprinted HTTP session for the account.

        Applies the device user-agent, Voyager headers, the CSRF token derived
        from ``JSESSIONID``, TLS impersonation profile, proxy and cookies.
        """
        if self._session_factory is not None:
            return self._session_factory(account)

        # Lazy import so environments without curl_cffi (e.g. unit tests that
        # inject a session_factory) don't require it.
        from curl_cffi import requests as cffi_requests  # type: ignore

        fp = self._fingerprint(account)
        creds = parse_auth_blob(getattr(account, "auth_blob", None))
        if not creds.get("li_at"):
            raise TransportUnavailable("account has no li_at session cookie")

        headers = dict(_BASE_HEADERS)
        headers.update(
            {
                "user-agent": fp["user_agent"],
                "accept-language": fp.get(
                    "accept_language", fp.get("locale", "en_US").replace("_", "-")
                ),
                "x-li-device-id": fp["device_id"],
                "x-li-track": json.dumps(li_track(fp), separators=(",", ":")),
            }
        )
        # Chromium sends UA client hints; Safari sends none. Faking them for
        # Safari, or omitting them for Chrome, contradicts the user-agent.
        headers.update(fp.get("client_hints") or {})
        # Voyager's CSRF check: header must equal the JSESSIONID cookie value.
        if creds.get("jsessionid"):
            headers["csrf-token"] = creds["jsessionid"]

        cookies = {"li_at": creds["li_at"]}
        if creds.get("jsessionid"):
            cookies["JSESSIONID"] = f'"{creds["jsessionid"]}"'

        proxies = None
        proxy = getattr(account, "proxy", None)
        if proxy and isinstance(proxy, dict) and proxy.get("url"):
            proxies = {"http": proxy["url"], "https": proxy["url"]}

        return cffi_requests.Session(
            # No default: a fingerprint without a TLS target is a bug, and
            # silently falling back to some other browser's handshake is
            # exactly the incoherence this module exists to prevent.
            impersonate=fp["tls_impersonate"],
            headers=headers,
            cookies=cookies,
            proxies=proxies,
        )

    # ------------------------------------------------------------------
    # Request plumbing
    # ------------------------------------------------------------------

    async def _request(
        self, account: Any, method: str, path: str, **kwargs
    ) -> Tuple[int, Any]:
        """
        Issue one Voyager request off the event loop.

        curl_cffi is synchronous, so the call runs in a worker thread. Returns
        ``(status_code, parsed_body)``; the body is parsed JSON when possible,
        otherwise raw text.

        Raises:
            TransportChallenge: session expired / checkpoint / rate limited --
                the composite router treats these as "hand off or back off",
                never as a plain failure.
        """
        session = self.build_session(account)
        url = path if path.startswith("http") else f"{VOYAGER_BASE}{path}"
        kwargs.setdefault("timeout", self._timeout)

        def _do():
            return session.request(method, url, **kwargs)

        try:
            response = await asyncio.to_thread(_do)
        except Exception as exc:  # network/TLS failure -> let the fallback try
            raise TransportUnavailable(f"voyager request failed: {exc}") from exc

        status = response.status_code
        if status in (401, 403):
            raise TransportChallenge(
                f"session rejected ({status}) -- cookie expired or checkpoint"
            )
        if status in (429, 999):
            raise TransportChallenge(f"rate limited by LinkedIn ({status})")

        try:
            body = response.json()
        except Exception:
            body = getattr(response, "text", "")
        return status, body

    @staticmethod
    def _ok(status: int) -> bool:
        return 200 <= status < 300

    async def _try_shapes(self, account: Any, action: str, shapes) -> TransportResult:
        """
        Try each candidate endpoint shape until one succeeds.

        ``shapes`` is a sequence of ``(label, method, path, kwargs)``. This is
        how we survive Voyager's concurrent endpoint generations: the modern
        shape first, the legacy shape as a same-transport fallback, and only if
        *all* of them fail do we raise ``TransportUnavailable`` and let
        Playwright take over.
        """
        errors = []
        for label, method, path, kwargs in shapes:
            try:
                status, body = await self._request(account, method, path, **kwargs)
            except TransportChallenge:
                raise  # auth/rate problems are not "try the next shape"
            except TransportUnavailable as exc:
                errors.append(f"{label}: {exc}")
                continue

            if self._ok(status):
                return TransportResult(
                    success=True,
                    action=action,
                    via=self.name,
                    detail={"shape": label, "status": status, "response": _trim(body)},
                )
            errors.append(f"{label}: HTTP {status}")

        raise TransportUnavailable(f"{action} failed on all voyager shapes: {'; '.join(errors)}")

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    async def whoami(self, account: Any) -> TransportResult:
        """Verify the session and return the authenticated member's identity."""
        status, body = await self._request(account, "GET", "/me")
        if not self._ok(status):
            raise TransportUnavailable(f"whoami returned HTTP {status}")

        mini = (body or {}).get("miniProfile") or {}
        # The normalized+json envelope nests the profile under "included".
        if not mini and isinstance(body, dict):
            for item in body.get("included", []) or []:
                if "publicIdentifier" in item:
                    mini = item
                    break

        first = mini.get("firstName") or ""
        last = mini.get("lastName") or ""
        public_id = mini.get("publicIdentifier")
        return TransportResult(
            success=True,
            action="whoami",
            via=self.name,
            detail={
                "member_urn": mini.get("entityUrn") or (body or {}).get("plainId"),
                "public_id": public_id,
                "display_name": f"{first} {last}".strip() or None,
                "headline": mini.get("occupation"),
                "profile_url": f"https://www.linkedin.com/in/{public_id}" if public_id else None,
            },
        )

    async def fetch_profile(self, account: Any, public_id: str) -> TransportResult:
        """Resolve a public profile handle to its member URN and headline."""
        status, body = await self._request(
            account, "GET", f"/identity/profiles/{public_id}/profileView"
        )
        if not self._ok(status):
            raise TransportUnavailable(f"fetch_profile returned HTTP {status}")

        profile = (body or {}).get("profile") or {}
        return TransportResult(
            success=True,
            action="fetch_profile",
            via=self.name,
            detail={
                "member_urn": profile.get("entityUrn"),
                "public_id": public_id,
                "display_name": " ".join(
                    filter(None, [profile.get("firstName"), profile.get("lastName")])
                )
                or None,
                "headline": profile.get("headline"),
                "location": (profile.get("geoLocationName") or profile.get("locationName")),
                "industry": profile.get("industryName"),
            },
        )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def connect(
        self, account: Any, member_urn: str, note: Optional[str] = None
    ) -> TransportResult:
        """
        Send a connection invitation, optionally with a personalized note.

        LinkedIn caps noted invitations tightly; the note is only attached when
        one was drafted and approved upstream.
        """
        profile_id = _profile_id(member_urn)

        dash_body: dict = {"inviteeProfileUrn": f"urn:li:fsd_profile:{profile_id}"}
        if note:
            dash_body["customMessage"] = note

        legacy_body: dict = {
            "invitee": {
                "com.linkedin.voyager.growth.invitation.InviteeProfile": {
                    "profileId": profile_id
                }
            },
        }
        if note:
            legacy_body["message"] = note

        return await self._try_shapes(
            account,
            "connect",
            [
                (
                    "dash",
                    "POST",
                    "/voyagerRelationshipsDashMemberRelationships"
                    "?action=verifyQuotaAndCreateV2",
                    {"json": dash_body},
                ),
                ("legacy", "POST", "/growth/normInvitations", {"json": legacy_body}),
            ],
        )

    async def send_message(self, account: Any, member_urn: str, text: str) -> TransportResult:
        """Send a direct message, creating the conversation if needed."""
        profile_id = _profile_id(member_urn)
        legacy_body = {
            "keyVersion": "LEGACY_INBOX",
            "conversationCreate": {
                "eventCreate": {
                    "value": {
                        "com.linkedin.voyager.messaging.create.MessageCreate": {
                            "body": text,
                            "attachments": [],
                            "attributedBody": {"text": text, "attributes": []},
                            "mediaAttachments": [],
                        }
                    }
                },
                "recipients": [profile_id],
                "subtype": "MEMBER_TO_MEMBER",
            },
        }
        return await self._try_shapes(
            account,
            "message",
            [
                (
                    "legacy",
                    "POST",
                    "/messaging/conversations?action=create",
                    {"json": legacy_body},
                ),
            ],
        )

    async def like(self, account: Any, activity_urn: str) -> TransportResult:
        """React to a post with a plain LIKE."""
        urn = _activity_urn(activity_urn)
        encoded = urn.replace(":", "%3A")
        return await self._try_shapes(
            account,
            "like",
            [
                (
                    "dash",
                    "POST",
                    f"/voyagerSocialDashReactions?threadUrn={encoded}",
                    {"json": {"reactionType": "LIKE"}},
                ),
                (
                    "legacy",
                    "POST",
                    f"/social/normLikes?action=like&threadUrn={encoded}",
                    {"json": {}},
                ),
            ],
        )

    async def comment(self, account: Any, activity_urn: str, text: str) -> TransportResult:
        """Post a comment on an activity."""
        urn = _activity_urn(activity_urn)
        encoded = urn.replace(":", "%3A")
        body = {"commentary": {"text": text, "attributes": []}}
        return await self._try_shapes(
            account,
            "comment",
            [
                ("dash", "POST", f"/socialDashNormComments?threadUrn={encoded}", {"json": body}),
                ("legacy", "POST", f"/social/normComments?threadUrn={encoded}", {"json": body}),
            ],
        )

    async def follow(self, account: Any, member_urn: str) -> TransportResult:
        profile_id = _profile_id(member_urn)
        return await self._try_shapes(
            account,
            "follow",
            [
                (
                    "legacy",
                    "POST",
                    "/feed/follows?action=followV2",
                    {"json": {"urn": f"urn:li:fs_followingInfo:{profile_id}"}},
                ),
            ],
        )

    async def create_post(self, account: Any, body: str, media: Any = None) -> TransportResult:
        """Publish a text post as the account."""
        if media:
            # Media upload is a multi-step register/upload/attach dance; until
            # it is implemented the browser path handles it.
            raise TransportUnavailable("create_post with media not implemented on mobile")

        payload = {
            "visibleToConnectionsOnly": False,
            "externalAudienceProviders": [],
            "commentaryV2": {"text": body, "attributes": []},
            "origin": "FEED",
            "allowedCommentersScope": "ALL",
            "postState": "PUBLISHED",
            "media": [],
        }
        return await self._try_shapes(
            account,
            "post",
            [("legacy", "POST", "/contentcreation/normShares", {"json": payload})],
        )

    async def fetch_activity(self, account: Any, member_urn: str) -> TransportResult:
        """Fetch a member's recent posts (the raw feed of things to engage with)."""
        profile_id = _profile_id(member_urn)
        status, body = await self._request(
            account,
            "GET",
            f"/identity/profileUpdatesV2?profileUrn=urn%3Ali%3Afsd_profile%3A{profile_id}&count=10",
        )
        if not self._ok(status):
            raise TransportUnavailable(f"fetch_activity returned HTTP {status}")
        return TransportResult(
            success=True,
            action="fetch_activity",
            via=self.name,
            detail={"posts": _extract_activities(body)},
        )

    async def fetch_connections(self, account: Any, since: Any = None) -> TransportResult:
        """
        List the account's connections.

        Used to detect which invitations were accepted, which drives both the
        follow-up sequence and the acceptance-rate governor. Returns a flat list
        of member URNs rather than full profiles — the sync only needs identity.
        """
        status, body = await self._request(
            account,
            "GET",
            "/relationships/connections?count=100&sortType=RECENTLY_ADDED",
        )
        if not self._ok(status):
            raise TransportUnavailable(f"fetch_connections returned HTTP {status}")

        urns = []
        for element in (body or {}).get("elements", []) or []:
            mini = element.get("miniProfile") or element.get("connectedMemberResolutionResult") or {}
            urn = mini.get("entityUrn") or mini.get("publicIdentifier")
            if urn:
                urns.append(urn)

        return TransportResult(
            success=True,
            action="fetch_connections",
            via=self.name,
            detail={"member_urns": urns, "count": len(urns)},
        )

    async def fetch_inbox(self, account: Any, since: Any = None) -> TransportResult:
        """Fetch recent conversations."""
        path = "/messaging/conversations?keyVersion=LEGACY_INBOX"
        if since:
            path += f"&createdBefore={int(since)}"
        status, body = await self._request(account, "GET", path)
        if not self._ok(status):
            raise TransportUnavailable(f"fetch_inbox returned HTTP {status}")
        return TransportResult(
            success=True,
            action="fetch_inbox",
            via=self.name,
            detail={"conversations": (body or {}).get("elements", [])},
        )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _profile_id(member_urn: str) -> str:
    """Extract the bare profile id from any URN flavor or a plain id."""
    if not member_urn:
        return ""
    text = str(member_urn)
    if ":" in text:
        return text.rsplit(":", 1)[-1].strip("()")
    return text


def _activity_urn(activity: str) -> str:
    """Normalize an activity reference to a full ``urn:li:activity:<id>``."""
    text = str(activity or "")
    if text.startswith("urn:li:"):
        return text
    # Accept a post permalink and pull the activity id out of it.
    if "activity-" in text:
        tail = text.split("activity-")[-1]
        digits = "".join(c for c in tail if c.isdigit())
        if digits:
            return f"urn:li:activity:{digits}"
    return f"urn:li:activity:{text}"


def _trim(body: Any, limit: int = 2000) -> Any:
    """Keep response detail small enough to store on a task row."""
    if isinstance(body, str):
        return body[:limit]
    try:
        text = json.dumps(body)
    except (TypeError, ValueError):
        return str(body)[:limit]
    return body if len(text) <= limit else {"truncated": text[:limit]}


def _extract_activities(body: Any) -> list:
    """Pull a light summary out of a profile-updates payload."""
    if not isinstance(body, dict):
        return []
    posts = []
    for element in body.get("elements", []) or []:
        urn = element.get("entityUrn") or element.get("updateMetadata", {}).get("urn")
        commentary = element.get("commentary") or {}
        text = commentary.get("text")
        if isinstance(text, dict):
            text = text.get("text")
        posts.append({"urn": urn, "text": text})
    return posts
