"""
Clerk authentication.

Verifies a Clerk-issued session JWT (RS256) against Clerk's JWKS and resolves
the local tenancy context, just-in-time provisioning the Org and User on first
sight.

Why this replaced LinkedIn-OAuth-as-login
-----------------------------------------
Signing in with LinkedIn had a real advantage: one click both authenticated the
user and connected their integration. Moving to Clerk splits those into two
steps, and that cost is accepted deliberately — it buys a login that survives a
LinkedIn session expiring, works for admins who never connect an account, and
keeps identity independent of the platform we automate.

**LinkedIn OAuth is not removed.** It stays as an integration-connect flow
(``/api/integrations/linkedin/*``); it just no longer issues our session token.

Design notes
------------
- The JWKS is fetched from ``CLERK_JWKS_URL`` and cached by ``PyJWKClient``.
  Tests inject a JWKS dict so verification runs end-to-end without network.
- ``RequestContext`` carries both the Clerk ids and the resolved local
  ``user_id``/``org_id`` used to scope every query.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from jwt import PyJWKClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.org import Org
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)


@dataclass
class ClerkConfig:
    jwks_url: str = ""
    issuer: str = ""
    audience: str = ""
    # Skip signature verification for local dev ONLY (never in production).
    dev_unsafe_accept_unsigned: bool = False

    @classmethod
    def from_env(cls) -> "ClerkConfig":
        return cls(
            jwks_url=os.getenv("CLERK_JWKS_URL", ""),
            issuer=os.getenv("CLERK_ISSUER", ""),
            audience=os.getenv("CLERK_AUDIENCE", ""),
            dev_unsafe_accept_unsigned=os.getenv("CLERK_DEV_UNSAFE", "").lower() == "true",
        )


@dataclass
class RequestContext:
    """Resolved tenancy context for an authenticated request."""

    user: User
    org_id: uuid.UUID
    clerk_user_id: str
    clerk_org_id: str | None
    email: str | None


class ClerkVerifier:
    """Verifies Clerk session JWTs and returns their claims."""

    def __init__(self, config: ClerkConfig, jwks: dict | None = None):
        self.config = config
        self._jwks = jwks  # injectable for tests
        self._jwk_client: PyJWKClient | None = None

    def _signing_key(self, token: str):
        if self._jwks is not None:
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            for key in self._jwks.get("keys", []):
                if key.get("kid") == kid:
                    return jwt.algorithms.RSAAlgorithm.from_jwk(key)
            raise HTTPException(status_code=401, detail="Unknown signing key")

        if not self.config.jwks_url:
            raise HTTPException(status_code=500, detail="CLERK_JWKS_URL not configured")
        if self._jwk_client is None:
            self._jwk_client = PyJWKClient(self.config.jwks_url)
        return self._jwk_client.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> dict:
        options = {"verify_aud": bool(self.config.audience)}
        try:
            if self.config.dev_unsafe_accept_unsigned:
                claims = jwt.decode(token, options={"verify_signature": False})
            else:
                claims = jwt.decode(
                    token,
                    self._signing_key(token),
                    algorithms=["RS256"],
                    audience=self.config.audience or None,
                    issuer=self.config.issuer or None,
                    options=options,
                )
        except HTTPException:
            raise
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError as exc:
            raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")

        if "exp" in claims and claims["exp"] < int(time.time()):
            raise HTTPException(status_code=401, detail="Token expired")
        return claims


# Module-level default verifier, lazily built from env. Overridable in tests.
_verifier: ClerkVerifier | None = None


def get_verifier() -> ClerkVerifier:
    global _verifier
    if _verifier is None:
        _verifier = ClerkVerifier(ClerkConfig.from_env())
    return _verifier


def set_verifier(verifier: ClerkVerifier | None) -> None:
    """Inject a verifier (tests) or reset to the env-based default with None."""
    global _verifier
    _verifier = verifier


def _claim(claims: dict, *keys: str) -> str | None:
    for key in keys:
        if claims.get(key):
            return claims[key]
    return None


def extract_bearer(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return parts[1]


async def resolve_context(db: AsyncSession, claims: dict) -> RequestContext:
    """
    Resolve (and provision) the local Org + User for a set of Clerk claims.

    A Clerk token with an active organization maps to that Org; a token without
    one gets a personal workspace. Membership is kept in sync when the token's
    active org changes, so switching orgs in Clerk switches tenancy here.
    """
    clerk_user_id = _claim(claims, "sub", "user_id")
    if not clerk_user_id:
        raise HTTPException(status_code=401, detail="Token missing subject")

    clerk_org_id = _claim(claims, "org_id")
    if not clerk_org_id and isinstance(claims.get("o"), dict):
        clerk_org_id = claims["o"].get("id")

    email = _claim(claims, "email", "email_address", "primary_email_address")
    full_name = _claim(claims, "name", "full_name") or (email or clerk_user_id)

    user = (
        await db.execute(select(User).where(User.clerk_user_id == clerk_user_id))
    ).scalar_one_or_none()

    if user is None:
        org = await _get_or_create_org(db, clerk_org_id, claims)
        user = User(
            id=uuid.uuid4(),
            clerk_user_id=clerk_user_id,
            org_id=org.id,
            email=email or f"{clerk_user_id}@clerk.local",
            full_name=full_name,
            # The first person into a personal workspace owns it; someone
            # joining a Clerk org inherits whatever role the token carries.
            role=UserRole.ADMIN if not clerk_org_id else _role_from(claims),
        )
        db.add(user)
        await db.flush()
    else:
        if clerk_org_id:
            org = (
                await db.execute(select(Org).where(Org.id == user.org_id))
            ).scalar_one_or_none()
            if org is None or org.clerk_org_id != clerk_org_id:
                org = await _get_or_create_org(db, clerk_org_id, claims)
                user.org_id = org.id
                await db.flush()
        if email and user.email != email:
            user.email = email

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is deactivated")

    await db.commit()
    await db.refresh(user)

    return RequestContext(
        user=user,
        org_id=user.org_id,
        clerk_user_id=clerk_user_id,
        clerk_org_id=clerk_org_id,
        email=email,
    )


def _role_from(claims: dict) -> UserRole:
    raw = str(_claim(claims, "org_role", "role") or "").lower()
    if "admin" in raw or "owner" in raw:
        return UserRole.ADMIN
    return UserRole.MEMBER


async def _get_or_create_org(db: AsyncSession, clerk_org_id: str | None, claims: dict) -> Org:
    if clerk_org_id:
        existing = (
            await db.execute(select(Org).where(Org.clerk_org_id == clerk_org_id))
        ).scalar_one_or_none()
        if existing:
            return existing

    name = (
        _claim(claims, "org_name", "org_slug")
        or _claim(claims, "email", "name")
        or "Workspace"
    )
    org = Org(id=uuid.uuid4(), name=name, clerk_org_id=clerk_org_id)
    db.add(org)
    await db.flush()
    return org


async def get_request_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RequestContext:
    """FastAPI dependency: authenticate and resolve tenancy."""
    token = extract_bearer(request)
    claims = get_verifier().verify(token)
    return await resolve_context(db, claims)
