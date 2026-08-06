"""
The admin console.

The requirement: four or five real people's accounts, one operator, one view.
These tests are mostly about the two things that only exist at the org level —
behaviour correlation and cluster headroom — because those are what make this
an admin console rather than five dashboards in a trenchcoat.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.integration import IntegrationAccount, Platform
from app.models.user import User
from app.personas.models import Persona
from app.warmup import planner, program


@pytest.fixture
async def team(db, client, clerk_token):
    """An org with three connected accounts, as LakeB2B would have."""
    claims = {"org_id": "org_lakeb2b", "org_name": "LakeB2B"}
    headers, accounts = [], []

    for i in range(3):
        header = {"Authorization": f"Bearer {clerk_token(**claims)}"}
        me = (await client.get("/api/auth/me", headers=header)).json()
        headers.append(header)

        account = IntegrationAccount(
            id=uuid.uuid4(),
            user_id=uuid.UUID(me["id"]),
            platform=Platform.LINKEDIN,
            is_active=True,
            warmup_state={},
            daily_caps={},
            linkedin_user_name=f"Person {i}",
        )
        planner.set_stage(account, program.FIRST_STAGE)
        db.add(account)
        accounts.append(account)

    await db.commit()
    org_id = (
        await db.execute(select(User.org_id).where(User.id == accounts[0].user_id))
    ).scalar_one()
    return headers, accounts, org_id


async def test_overview_shows_every_account(client, team):
    headers, accounts, _ = team

    response = await client.get("/api/console/overview", headers=headers[0])
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["totals"]["accounts"] == 3
    assert len(body["accounts"]) == 3
    assert {a["name"] for a in body["accounts"]} == {"Person 0", "Person 1", "Person 2"}


async def test_overview_reports_who_is_still_warming_up(client, team):
    headers, _, _ = team
    body = (await client.get("/api/console/overview", headers=headers[0])).json()

    assert body["totals"]["warming_up"] == 3
    for account in body["accounts"]:
        assert account["stage"] == program.FIRST_STAGE
        # The point of the stage column: it says what is *not* yet possible.
        assert account["allowed_actions"] == ["like"]


async def test_overview_carries_the_org_level_questions(client, team):
    """Correlation and cluster policy — what no single account can answer."""
    headers, _, _ = team
    body = (await client.get("/api/console/overview", headers=headers[0])).json()

    assert "correlation" in body
    assert "verdict" in body["correlation"]
    assert body["cluster_policy"]["max_accounts_per_post"] >= 1
    assert 0 < body["cluster_policy"]["participation_rate"] <= 1


async def test_accounts_get_independent_schedule_windows(client, team):
    """All five working 9-to-5 together is its own signature."""
    headers, _, _ = team
    body = (await client.get("/api/console/overview", headers=headers[0])).json()

    windows = {tuple(a["schedule_window"]) for a in body["accounts"]}
    assert len(windows) > 1


async def test_the_review_split_is_visible(client, team):
    """The UI has to know which queue is bulk-approvable and which isn't."""
    headers, _, _ = team
    body = (await client.get("/api/console/overview", headers=headers[0])).json()

    assert body["review"]["engagement_is_bulk"] is True
    assert "messaging_by_account" in body["review"]


async def test_another_org_sees_none_of_it(client, team, clerk_token):
    """The isolation property, at the endpoint that aggregates everything."""
    outsider = {"Authorization": f"Bearer {clerk_token(org_id='org_other')}"}
    await client.get("/api/auth/me", headers=outsider)

    body = (await client.get("/api/console/overview", headers=outsider)).json()
    assert body["totals"]["accounts"] == 0
    assert body["accounts"] == []


# ---------------------------------------------------------------------------
# Steering
# ---------------------------------------------------------------------------


async def test_pausing_from_the_console_reaches_every_account(db, client, team):
    """The stop button. It would be useless if it respected overrides."""
    headers, accounts, _ = team

    response = await client.post(
        "/api/console/steer",
        headers=headers[0],
        json={"pause": True, "reason": "investigating a warning"},
    )
    assert response.status_code == 200
    assert len(response.json()["accounts_changed"]) == 3

    for account in accounts:
        await db.refresh(account)
        assert planner.paused(account)


async def test_resuming_works_the_same_way(db, client, team):
    headers, accounts, _ = team

    await client.post("/api/console/steer", headers=headers[0], json={"pause": True})
    await client.post("/api/console/steer", headers=headers[0], json={"pause": False})

    for account in accounts:
        await db.refresh(account)
        assert not planner.paused(account)


async def test_a_paused_account_shows_as_paused_in_the_overview(client, team):
    headers, _, _ = team
    await client.post(
        "/api/console/steer", headers=headers[0], json={"pause": True, "reason": "hold"}
    )

    body = (await client.get("/api/console/overview", headers=headers[0])).json()
    assert body["totals"]["paused"] == 3


# ---------------------------------------------------------------------------
# Personas in the console
# ---------------------------------------------------------------------------


async def test_the_overview_shows_which_accounts_have_their_own_persona(
    db, client, team
):
    """So an admin can see who has diverged from the org line."""
    headers, accounts, org_id = team

    db.add(
        Persona(
            id=uuid.uuid4(), org_id=org_id, account_id=None,
            name="LakeB2B", expertise=["b2b data"],
        )
    )
    db.add(
        Persona(
            id=uuid.uuid4(), org_id=org_id, account_id=accounts[0].id,
            name="Dana Whitfield", voice={"tone": "direct"},
        )
    )
    await db.commit()

    body = (await client.get("/api/console/overview", headers=headers[0])).json()
    by_id = {a["account_id"]: a for a in body["accounts"]}

    personalised = by_id[str(accounts[0].id)]
    inherited = by_id[str(accounts[1].id)]

    assert personalised["persona"] == "Dana Whitfield"
    assert "voice" in personalised["persona_overridden"]

    assert inherited["persona"] == "LakeB2B"
    assert inherited["persona_overridden"] == []


async def test_account_detail_returns_the_plan_and_the_persona(client, team):
    headers, accounts, _ = team

    response = await client.get(
        f"/api/console/accounts/{accounts[0].id}", headers=headers[0]
    )
    assert response.status_code == 200

    body = response.json()
    assert body["stage"] == program.FIRST_STAGE
    assert "plan" in body
    assert "persona" in body
    assert "health" in body


async def test_account_detail_refuses_another_orgs_account(client, team, clerk_token):
    _, accounts, _ = team
    outsider = {"Authorization": f"Bearer {clerk_token(org_id='org_other')}"}
    await client.get("/api/auth/me", headers=outsider)

    response = await client.get(
        f"/api/console/accounts/{accounts[0].id}", headers=outsider
    )
    assert response.status_code == 404
