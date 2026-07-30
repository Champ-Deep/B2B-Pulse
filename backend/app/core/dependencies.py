"""
Request dependencies.

Authentication moved from a locally-issued JWT (LinkedIn OAuth login) to Clerk.
``get_current_user`` keeps its exact signature and return type through that
change, so every existing route continues to work untouched — only the way the
``User`` is arrived at differs. Routes that need the org explicitly can depend
on ``get_request_context`` or ``get_current_org_id`` instead.
"""

import uuid

from fastapi import Depends, HTTPException, status

from app.core.clerk import RequestContext, get_request_context
from app.models.user import User, UserRole

__all__ = [
    "get_current_user",
    "get_current_org_id",
    "get_request_context",
    "require_role",
    "require_platform_admin",
]


async def get_current_user(
    ctx: RequestContext = Depends(get_request_context),
) -> User:
    """The authenticated user, provisioned from Clerk claims on first sight."""
    return ctx.user


async def get_current_org_id(
    ctx: RequestContext = Depends(get_request_context),
) -> uuid.UUID:
    """The org every query in this request should be scoped to."""
    return ctx.org_id


def require_role(*roles: UserRole):
    """FastAPI dependency that enforces role-based access."""

    async def _check(current_user: User = Depends(get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return _check


async def require_platform_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """FastAPI dependency that requires platform-level admin access."""
    if not current_user.is_platform_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin access required",
        )
    return current_user
