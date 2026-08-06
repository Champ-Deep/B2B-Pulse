"""
Turning an ``IntegrationAccount`` into something the transports can drive.

Transports duck-type on ``id``/``auth_blob``/``device_fingerprint``/``proxy``.
``LiveAccount`` is the short-lived object that satisfies that contract with the
credentials decrypted — deliberately *not* the ORM row, so plaintext can never
be accidentally flushed back to the database.

B2B Pulse stores LinkedIn cookies as an encrypted JSON list (Playwright's cookie
format). The Voyager transport wants ``{"li_at": ..., "jsessionid": ...}``. The
translation lives here so neither side has to know about the other's format.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_value
from app.models.integration import IntegrationAccount, Platform
from app.transports.fingerprints import generate_fingerprint, is_stale

logger = logging.getLogger(__name__)


class AccountError(Exception):
    """An account operation failed in a way the caller should surface."""


class LiveAccount:
    """An integration account with its credentials decrypted, for one call."""

    __slots__ = ("id", "user_id", "org_id", "auth_blob", "device_fingerprint", "proxy", "record")

    def __init__(self, record: IntegrationAccount, auth_blob: Any, org_id: uuid.UUID | None = None):
        self.id = str(record.id)
        self.user_id = str(record.user_id)
        self.org_id = org_id
        self.auth_blob = auth_blob
        self.device_fingerprint = (
            record.device_fingerprint
            if not is_stale(record.device_fingerprint)
            else generate_fingerprint(str(record.id))
        )
        self.proxy = record.proxy
        self.record = record

    def __repr__(self) -> str:  # keep credentials out of logs and tracebacks
        return f"<LiveAccount {self.id} credentials=redacted>"


def cookies_to_auth_blob(cookies: Any) -> dict:
    """
    Extract the two cookies Voyager needs from B2B Pulse's stored cookie jar.

    ``li_at`` authenticates. ``JSESSIONID`` is the CSRF token source — without
    it reads succeed and every write silently fails, which is a miserable thing
    to debug, so it is worth pulling out explicitly.
    """
    if not cookies:
        return {}

    if isinstance(cookies, str):
        try:
            cookies = json.loads(cookies)
        except json.JSONDecodeError:
            # A bare li_at value.
            return {"li_at": cookies.strip()}

    if isinstance(cookies, dict):
        return {
            "li_at": str(cookies.get("li_at") or "").strip().strip('"'),
            "jsessionid": str(
                cookies.get("jsessionid") or cookies.get("JSESSIONID") or ""
            ).strip().strip('"'),
        }

    blob: dict = {}
    for cookie in cookies or []:
        if not isinstance(cookie, dict):
            continue
        name = cookie.get("name")
        value = str(cookie.get("value") or "").strip().strip('"')
        if name == "li_at":
            blob["li_at"] = value
        elif name == "JSESSIONID":
            blob["jsessionid"] = value
    return blob


def decrypt_cookies(record: IntegrationAccount) -> Any:
    """Decrypt the stored cookie jar, tolerating both encrypted and plain rows."""
    raw = record.session_cookies
    if not raw:
        return None
    if isinstance(raw, str):
        try:
            return json.loads(decrypt_value(raw))
        except Exception:
            # Older rows may be stored unencrypted.
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
    return raw


async def load_live_account(
    db: AsyncSession, account_id: str | uuid.UUID, org_id: uuid.UUID | None = None
) -> LiveAccount:
    """Load an integration account with its credentials ready for a transport."""
    record = await get_account(db, account_id, org_id)
    if record is None:
        raise AccountError("account not found")

    blob = cookies_to_auth_blob(decrypt_cookies(record))
    if not blob.get("li_at"):
        raise AccountError("account has no usable LinkedIn session")
    return LiveAccount(record, blob, org_id)


async def get_account(
    db: AsyncSession, account_id: str | uuid.UUID, org_id: uuid.UUID | None = None
) -> IntegrationAccount | None:
    """Fetch one integration account, scoped to an org when given."""
    try:
        key = uuid.UUID(str(account_id))
    except (ValueError, TypeError, AttributeError):
        return None

    stmt = select(IntegrationAccount).where(IntegrationAccount.id == key)
    if org_id is not None:
        from app.models.user import User

        stmt = stmt.join(User, User.id == IntegrationAccount.user_id).where(
            User.org_id == org_id
        )
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_org_accounts(
    db: AsyncSession, org_id: uuid.UUID, platform: Platform = Platform.LINKEDIN
) -> list[IntegrationAccount]:
    """
    Every connected account in an org.

    This is the query the admin view is built on: one row per real person's
    LinkedIn account, across the whole organization.
    """
    from app.models.user import User

    stmt = (
        select(IntegrationAccount)
        .join(User, User.id == IntegrationAccount.user_id)
        .where(
            User.org_id == org_id,
            IntegrationAccount.platform == platform,
            IntegrationAccount.is_active.is_(True),
        )
        .order_by(IntegrationAccount.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


def ensure_fingerprint(record: IntegrationAccount) -> dict:
    """
    Give an account a stable device identity, generating one on first use.

    Derived from the account id rather than randomly, so it survives restarts
    and is identical whether it was persisted yet or not. Device consistency is
    something LinkedIn ties trust to, so this is never regenerated on a whim.

    The one exception is a fingerprint from a superseded catalogue version:
    those are the incoherent ones, and keeping a consistent-but-detectable
    identity is worse than the one-time change of presenting a coherent one.
    """
    if is_stale(record.device_fingerprint):
        record.device_fingerprint = generate_fingerprint(str(record.id))
    return record.device_fingerprint


def get_transport(account: Any, *, actions: Any = None, mobile_session_factory: Any = None):
    """
    Build the transport for an account: Voyager first, browser underneath.

    Honors ``MOBILE_TRANSPORT_ENABLED`` so an operator can pin to the browser
    path if a Voyager change starts causing trouble.
    """
    import os

    from app.transports.mobile import MobileAPITransport
    from app.transports.playwright import PlaywrightTransport
    from app.transports.router import CompositeTransport

    browser = PlaywrightTransport(actions=actions)
    if os.getenv("MOBILE_TRANSPORT_ENABLED", "true").lower() == "false":
        return browser

    mobile = MobileAPITransport(session_factory=mobile_session_factory)
    return CompositeTransport(primary=mobile, fallback=browser)
