"""
Clerk authentication and tenancy provisioning.

These replace the old signup/login/refresh tests, which covered endpoints that
no longer exist — Clerk issues and refreshes tokens on the client, so the
backend only verifies. What matters now is that verification is real, that
tenancy is provisioned correctly on first sight, and that one org can never
end up inside another.
"""

import time
import uuid

import jwt as pyjwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.org import Org
from app.models.user import User, UserRole


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


async def test_me_requires_a_token(client: AsyncClient):
    response = await client.get("/api/auth/me")
    assert response.status_code == 401


async def test_malformed_authorization_header_is_rejected(client: AsyncClient):
    for header in ("", "Bearer", "Basic abc", "abc"):
        response = await client.get("/api/auth/me", headers={"Authorization": header})
        assert response.status_code == 401, header


async def test_a_token_we_did_not_sign_is_rejected(client: AsyncClient):
    """The signature check has to be real, not decorative."""
    forged = pyjwt.encode(
        {"sub": "user_attacker", "exp": int(time.time()) + 3600},
        "not-the-right-key",
        algorithm="HS256",
        headers={"kid": "test-key"},
    )
    response = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {forged}"}
    )
    assert response.status_code == 401


async def test_an_expired_token_is_rejected(client: AsyncClient, clerk_token):
    expired = clerk_token(exp=int(time.time()) - 60)
    response = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


async def test_a_token_with_no_subject_is_rejected(client: AsyncClient, clerk_keypair):
    private_key, _ = clerk_keypair
    anonymous = pyjwt.encode(
        {"email": "nobody@example.com", "exp": int(time.time()) + 3600},
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )
    response = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {anonymous}"}
    )
    assert response.status_code == 401


async def test_an_unknown_signing_key_is_rejected(client: AsyncClient, clerk_keypair):
    private_key, _ = clerk_keypair
    wrong_kid = pyjwt.encode(
        {"sub": "user_x", "exp": int(time.time()) + 3600},
        private_key,
        algorithm="RS256",
        headers={"kid": "some-other-key"},
    )
    response = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {wrong_kid}"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


async def test_first_request_provisions_a_user_and_workspace(
    client: AsyncClient, clerk_token, db
):
    """A brand-new Clerk user gets a local User and a personal workspace."""
    token = clerk_token(email="dana@example.com", name="Dana Whitfield")
    response = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["email"] == "dana@example.com"
    assert body["full_name"] == "Dana Whitfield"
    assert body["org_id"]

    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(body["id"])))
    ).scalar_one()
    # Someone in their own workspace owns it.
    assert user.role == UserRole.ADMIN
    assert user.clerk_user_id


async def test_provisioning_is_idempotent(client: AsyncClient, clerk_token, db):
    """Repeated calls must not create a second user or a second workspace."""
    headers = {"Authorization": f"Bearer {clerk_token(email='repeat@example.com')}"}

    first = await client.get("/api/auth/me", headers=headers)
    second = await client.get("/api/auth/me", headers=headers)

    assert first.json()["id"] == second.json()["id"]
    assert first.json()["org_id"] == second.json()["org_id"]

    users = (await db.execute(select(User))).scalars().all()
    assert len(users) == 1


async def test_a_clerk_org_maps_to_one_shared_workspace(
    client: AsyncClient, clerk_token, db
):
    """Two users in the same Clerk org land in the same local org."""
    claims = {"org_id": "org_acme", "org_name": "Acme"}

    first = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {clerk_token(**claims)}"}
    )
    second = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {clerk_token(**claims)}"}
    )

    assert first.json()["org_id"] == second.json()["org_id"]
    assert first.json()["id"] != second.json()["id"]

    orgs = (
        await db.execute(select(Org).where(Org.clerk_org_id == "org_acme"))
    ).scalars().all()
    assert len(orgs) == 1
    assert orgs[0].name == "Acme"


async def test_separate_clerk_orgs_get_separate_workspaces(
    client: AsyncClient, clerk_token
):
    """The isolation property the whole multi-tenant model rests on."""
    one = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_one')}"},
    )
    two = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_two')}"},
    )
    assert one.json()["org_id"] != two.json()["org_id"]


async def test_switching_clerk_org_moves_the_user(client: AsyncClient, clerk_token):
    """Switching organization in Clerk switches tenancy here."""
    subject = f"user_{uuid.uuid4().hex[:12]}"

    first = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {clerk_token(sub=subject, org_id='org_a')}"},
    )
    second = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {clerk_token(sub=subject, org_id='org_b')}"},
    )

    assert first.json()["id"] == second.json()["id"]
    assert first.json()["org_id"] != second.json()["org_id"]


async def test_org_members_are_not_automatically_admins(
    client: AsyncClient, clerk_token, db
):
    """Joining someone else's org shouldn't hand out admin."""
    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {clerk_token(org_id='org_acme')}"},
    )
    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(response.json()["id"])))
    ).scalar_one()
    assert user.role == UserRole.MEMBER


async def test_a_deactivated_user_is_refused(client: AsyncClient, clerk_token, db):
    headers = {"Authorization": f"Bearer {clerk_token()}"}

    response = await client.get("/api/auth/me", headers=headers)
    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(response.json()["id"])))
    ).scalar_one()
    user.is_active = False
    await db.commit()

    again = await client.get("/api/auth/me", headers=headers)
    assert again.status_code == 403


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


@pytest.fixture
async def invite(client: AsyncClient, auth_headers, db):
    """An invite issued by an existing org."""
    from datetime import UTC, datetime, timedelta

    from app.models.invite import OrgInvite

    me = (await client.get("/api/auth/me", headers=auth_headers)).json()
    inviter = (
        await db.execute(select(User).where(User.id == uuid.UUID(me["id"])))
    ).scalar_one()

    row = OrgInvite(
        org_id=inviter.org_id,
        invited_by=inviter.id,
        invite_code=f"code-{uuid.uuid4().hex[:12]}",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def test_invite_can_be_previewed_without_signing_in(client: AsyncClient, invite):
    """So the sign-in page can say what the user is being asked to join."""
    response = await client.get(f"/api/auth/invite/{invite.invite_code}")
    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["org_name"]


async def test_redeeming_an_invite_moves_the_user_into_that_org(
    client: AsyncClient, clerk_token, invite
):
    joiner = {"Authorization": f"Bearer {clerk_token()}"}
    before = (await client.get("/api/auth/me", headers=joiner)).json()

    response = await client.post(
        "/api/auth/redeem-invite",
        headers=joiner,
        json={"invite_code": invite.invite_code},
    )
    assert response.status_code == 200, response.text
    assert response.json()["org_id"] == str(invite.org_id)

    after = (await client.get("/api/auth/me", headers=joiner)).json()
    assert after["org_id"] != before["org_id"]
    assert after["org_id"] == str(invite.org_id)


async def test_an_invite_cannot_be_redeemed_twice(
    client: AsyncClient, clerk_token, invite
):
    first = {"Authorization": f"Bearer {clerk_token()}"}
    await client.get("/api/auth/me", headers=first)
    ok = await client.post(
        "/api/auth/redeem-invite",
        headers=first,
        json={"invite_code": invite.invite_code},
    )
    assert ok.status_code == 200

    second = {"Authorization": f"Bearer {clerk_token()}"}
    await client.get("/api/auth/me", headers=second)
    again = await client.post(
        "/api/auth/redeem-invite",
        headers=second,
        json={"invite_code": invite.invite_code},
    )
    assert again.status_code == 404


async def test_an_email_scoped_invite_only_works_for_that_address(
    client: AsyncClient, clerk_token, invite, db
):
    invite.email = "only-me@example.com"
    await db.commit()

    wrong = {"Authorization": f"Bearer {clerk_token(email='someone-else@example.com')}"}
    await client.get("/api/auth/me", headers=wrong)

    response = await client.post(
        "/api/auth/redeem-invite",
        headers=wrong,
        json={"invite_code": invite.invite_code},
    )
    assert response.status_code == 403
    assert "only-me@example.com" in response.json()["detail"]


async def test_an_expired_invite_is_refused(
    client: AsyncClient, clerk_token, invite, db
):
    from datetime import UTC, datetime, timedelta

    invite.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db.commit()

    headers = {"Authorization": f"Bearer {clerk_token()}"}
    await client.get("/api/auth/me", headers=headers)

    response = await client.post(
        "/api/auth/redeem-invite",
        headers=headers,
        json={"invite_code": invite.invite_code},
    )
    assert response.status_code == 410
