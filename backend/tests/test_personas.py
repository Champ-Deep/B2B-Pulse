"""
Persona resolution.

The whole value of the layer is in ``resolve``: an org's shared direction with
one person's own voice layered over it. These tests are about the merge rules,
because each one exists for a specific reason and getting any of them backwards
either homogenises the accounts or lets an individual switch off a control the
org set.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.integration import IntegrationAccount, Platform
from app.models.user import User
from app.personas import service as personas
from app.personas.models import Persona


@pytest.fixture
async def org_and_account(db, client, auth_headers):
    me = (await client.get("/api/auth/me", headers=auth_headers)).json()
    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(me["id"])))
    ).scalar_one()

    account = IntegrationAccount(
        id=uuid.uuid4(),
        user_id=user.id,
        platform=Platform.LINKEDIN,
        is_active=True,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return user.org_id, account


@pytest.fixture
async def org_persona(db, org_and_account):
    org_id, _ = org_and_account
    row = Persona(
        id=uuid.uuid4(),
        org_id=org_id,
        account_id=None,
        name="LakeB2B",
        bio="We help B2B teams reach the right buyers.",
        voice={"tone": "measured", "never_say": ["synergy"]},
        expertise=["b2b data", "demand generation"],
        content_pillars=["data quality", "outbound"],
        guardrails={"avoid_topics": ["politics"], "avoid_competitors": ["Acme Data"]},
        autonomy={"like": True, "comment": False},
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


async def test_an_account_with_no_persona_inherits_the_org(db, org_and_account, org_persona):
    """The sane default for someone just added to the team."""
    org_id, account = org_and_account
    resolved = await personas.resolve(db, org_id, account.id)

    assert resolved.name == "LakeB2B"
    assert resolved.expertise == ["b2b data", "demand generation"]
    assert resolved.overridden == []


async def test_no_personas_at_all_resolves_empty_rather_than_failing(db, org_and_account):
    org_id, account = org_and_account
    resolved = await personas.resolve(db, org_id, account.id)
    assert resolved.name == ""
    assert personas.to_prompt(resolved) == ""


# ---------------------------------------------------------------------------
# Voice: the person wins
# ---------------------------------------------------------------------------


async def test_a_persons_voice_overrides_the_org(db, org_and_account, org_persona):
    """
    The one thing that must never be homogenised by a central setting — it is
    what makes five accounts sound like five people.
    """
    org_id, account = org_and_account
    db.add(
        Persona(
            id=uuid.uuid4(),
            org_id=org_id,
            account_id=account.id,
            parent_id=org_persona.id,
            name="Dana Whitfield",
            bio="I run growth and I am blunt about what works.",
            voice={"tone": "direct"},
        )
    )
    await db.commit()

    resolved = await personas.resolve(db, org_id, account.id)

    assert resolved.name == "Dana Whitfield"
    assert "blunt" in resolved.bio
    assert resolved.voice["tone"] == "direct"
    # Voice keys the person didn't set still come from the org.
    assert resolved.voice["never_say"] == ["synergy"]
    assert "voice" in resolved.overridden


async def test_expertise_is_replaced_not_merged(db, org_and_account, org_persona):
    """
    Merging would give everyone the union of everybody's topics, which is how
    an account ends up commenting credibly on nothing.
    """
    org_id, account = org_and_account
    db.add(
        Persona(
            id=uuid.uuid4(), org_id=org_id, account_id=account.id,
            name="Dana", expertise=["healthcare data"],
        )
    )
    await db.commit()

    resolved = await personas.resolve(db, org_id, account.id)
    assert resolved.expertise == ["healthcare data"]
    assert "b2b data" not in resolved.expertise


async def test_an_unset_field_still_inherits(db, org_and_account, org_persona):
    """An empty list means 'use the org's', not 'I have none'."""
    org_id, account = org_and_account
    db.add(
        Persona(
            id=uuid.uuid4(), org_id=org_id, account_id=account.id,
            name="Dana", expertise=[],
        )
    )
    await db.commit()

    resolved = await personas.resolve(db, org_id, account.id)
    assert resolved.expertise == ["b2b data", "demand generation"]


# ---------------------------------------------------------------------------
# Guardrails: cumulative, never removable
# ---------------------------------------------------------------------------


async def test_a_person_can_add_guardrails(db, org_and_account, org_persona):
    org_id, account = org_and_account
    db.add(
        Persona(
            id=uuid.uuid4(), org_id=org_id, account_id=account.id,
            name="Dana", guardrails={"avoid_topics": ["salary"]},
        )
    )
    await db.commit()

    resolved = await personas.resolve(db, org_id, account.id)
    assert set(resolved.guardrails["avoid_topics"]) == {"politics", "salary"}


async def test_a_person_cannot_remove_an_org_guardrail(db, org_and_account, org_persona):
    """A guardrail an individual can switch off is not a guardrail."""
    org_id, account = org_and_account
    db.add(
        Persona(
            id=uuid.uuid4(), org_id=org_id, account_id=account.id,
            name="Dana", guardrails={"avoid_topics": []},
        )
    )
    await db.commit()

    resolved = await personas.resolve(db, org_id, account.id)
    assert "politics" in resolved.guardrails["avoid_topics"]
    assert resolved.guardrails["avoid_competitors"] == ["Acme Data"]


# ---------------------------------------------------------------------------
# Autonomy: the stricter wins
# ---------------------------------------------------------------------------


async def test_a_person_cannot_grant_themselves_more_autonomy(
    db, org_and_account, org_persona
):
    """
    If the org says comments need approval, one account cannot decide
    otherwise — the same principle as merging two sets of caps.
    """
    org_id, account = org_and_account
    db.add(
        Persona(
            id=uuid.uuid4(), org_id=org_id, account_id=account.id,
            name="Dana", autonomy={"comment": True, "like": True},
        )
    )
    await db.commit()

    resolved = await personas.resolve(db, org_id, account.id)
    assert resolved.autonomy["comment"] is False
    assert resolved.requires_approval("comment")


async def test_a_person_can_choose_to_be_more_cautious(db, org_and_account, org_persona):
    org_id, account = org_and_account
    db.add(
        Persona(
            id=uuid.uuid4(), org_id=org_id, account_id=account.id,
            name="Dana", autonomy={"like": False},
        )
    )
    await db.commit()

    resolved = await personas.resolve(db, org_id, account.id)
    assert resolved.autonomy["like"] is False


async def test_an_unspecified_action_requires_approval(db, org_and_account, org_persona):
    """An action nobody thought about should not ship unattended."""
    org_id, account = org_and_account
    resolved = await personas.resolve(db, org_id, account.id)
    assert resolved.requires_approval("post")


# ---------------------------------------------------------------------------
# Central steering
# ---------------------------------------------------------------------------


async def test_editing_the_org_persona_moves_everyone_who_has_not_overridden(
    db, org_and_account, org_persona
):
    """The point of inheritance: one admin steers, without flattening voices."""
    org_id, account = org_and_account
    db.add(
        Persona(
            id=uuid.uuid4(), org_id=org_id, account_id=account.id,
            name="Dana", voice={"tone": "direct"},
        )
    )
    await db.commit()

    org_persona.content_pillars = ["healthcare outbound"]
    await db.commit()

    resolved = await personas.resolve(db, org_id, account.id)
    # The campaign moved...
    assert resolved.content_pillars == ["healthcare outbound"]
    # ...but Dana still sounds like Dana.
    assert resolved.name == "Dana"
    assert resolved.voice["tone"] == "direct"


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


async def test_the_prompt_carries_voice_expertise_and_guardrails(
    db, org_and_account, org_persona
):
    org_id, account = org_and_account
    prompt = personas.to_prompt(await personas.resolve(db, org_id, account.id))

    assert "LakeB2B" in prompt
    assert "measured" in prompt
    assert "synergy" in prompt
    assert "politics" in prompt
    assert "Acme Data" in prompt


async def test_two_personas_produce_genuinely_different_prompts(
    db, org_and_account, org_persona
):
    """
    The cluster-safety payoff: five accounts must not generate from one prompt.
    """
    org_id, account = org_and_account
    db.add(
        Persona(
            id=uuid.uuid4(), org_id=org_id, account_id=account.id,
            name="Dana Whitfield", bio="Blunt about what works.",
            voice={"tone": "direct"}, expertise=["healthcare data"],
        )
    )
    await db.commit()

    with_override = personas.to_prompt(await personas.resolve(db, org_id, account.id))
    org_only = personas.to_prompt(await personas.resolve(db, org_id, None))

    assert with_override != org_only
    assert "Dana Whitfield" in with_override
    assert "healthcare data" in with_override
