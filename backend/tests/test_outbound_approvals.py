"""
The approval queue, and the bulk-versus-per-account split.

The split is by *action type*, not by account: engagement is bulk-approved
org-wide, messaging is approved per account. These tests exist because getting
that backwards in either direction is bad — bulk-approving messages would let
one click send as five colleagues, and per-item approval of engagement produces
five queues nobody reads.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.integration import IntegrationAccount, Platform
from app.models.user import User
from app.outbound import approvals
from app.outbound.models import (
    OutreachSuggestion,
    OutreachTarget,
    SuggestionAction,
    SuggestionStatus,
    TargetStatus,
)
from app.outbound.scoring import score_target


@pytest.fixture
async def account(db, client, auth_headers):
    me = (await client.get("/api/auth/me", headers=auth_headers)).json()
    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(me["id"])))
    ).scalar_one()

    record = IntegrationAccount(
        id=uuid.uuid4(), user_id=user.id, platform=Platform.LINKEDIN,
        is_active=True, warmup_state={}, daily_caps={},
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    record._org_id = user.org_id
    return record


async def _suggestion(db, account, action, *, text=None, status=SuggestionStatus.PENDING):
    target = OutreachTarget(
        id=uuid.uuid4(),
        org_id=account._org_id,
        account_id=account.id,
        member_urn=f"urn:{uuid.uuid4().hex[:8]}",
        full_name="Dana Whitfield",
        first_name="Dana",
        title="Head of Growth",
        company="Northwind",
        headline="Head of Growth at Northwind",
        status=TargetStatus.SCORED,
    )
    db.add(target)
    await db.flush()

    default = (
        "Hi Dana — your work as Head of Growth at Northwind keeps coming up. "
        "Would be glad to connect."
    )
    row = OutreachSuggestion(
        id=uuid.uuid4(),
        org_id=account._org_id,
        account_id=account.id,
        target_id=target.id,
        action=action,
        status=status,
        draft_text=text if text is not None else default,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, target


# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------


def test_engagement_is_bulk_approvable_and_messaging_is_not():
    assert not approvals.requires_individual_approval(SuggestionAction.LIKE)
    assert not approvals.requires_individual_approval(SuggestionAction.COMMENT)

    assert approvals.requires_individual_approval(SuggestionAction.MESSAGE)
    assert approvals.requires_individual_approval(SuggestionAction.CONNECT)


async def test_bulk_approval_accepts_engagement(db, account):
    like, _ = await _suggestion(db, account, SuggestionAction.LIKE, text="")
    comment, _ = await _suggestion(
        db, account, SuggestionAction.COMMENT,
        text="The activation point is the part most teams skip, Dana. What changed your mind?",
    )

    result = await approvals.approve_bulk(
        db, [str(like.id), str(comment.id)], org_id=account._org_id
    )
    assert set(result.approved) == {str(like.id), str(comment.id)}
    assert not result.refused


async def test_bulk_approval_refuses_messages_explicitly(db, account):
    """
    A DM goes out under one person's name to someone who will reply to them.

    Refused per-item rather than silently skipped, so a client can't discover
    the rule by noticing some approvals didn't take.
    """
    message, _ = await _suggestion(db, account, SuggestionAction.MESSAGE)

    result = await approvals.approve_bulk(db, [str(message.id)], org_id=account._org_id)

    assert not result.approved
    assert str(message.id) in result.refused
    assert "one person's name" in result.refused[str(message.id)]

    await db.refresh(message)
    assert message.status == SuggestionStatus.PENDING


async def test_bulk_approval_refuses_invitations_too(db, account):
    invite, _ = await _suggestion(db, account, SuggestionAction.CONNECT)
    result = await approvals.approve_bulk(db, [str(invite.id)], org_id=account._org_id)
    assert str(invite.id) in result.refused


async def test_a_mixed_batch_approves_only_what_it_may(db, account):
    like, _ = await _suggestion(db, account, SuggestionAction.LIKE, text="")
    message, _ = await _suggestion(db, account, SuggestionAction.MESSAGE)

    result = await approvals.approve_bulk(
        db, [str(like.id), str(message.id)], org_id=account._org_id
    )
    assert result.approved == [str(like.id)]
    assert str(message.id) in result.refused


async def test_bulk_approval_cannot_reach_another_org(db, account, client, clerk_token):
    """The isolation property, at the one endpoint that takes a list of ids."""
    like, _ = await _suggestion(db, account, SuggestionAction.LIKE, text="")

    other_org = uuid.uuid4()
    result = await approvals.approve_bulk(db, [str(like.id)], org_id=other_org)

    assert not result.approved
    assert "not found" in result.refused[str(like.id)]


async def test_messages_can_still_be_approved_one_at_a_time(db, account):
    message, _ = await _suggestion(
        db, account, SuggestionAction.MESSAGE,
        text="Thanks for connecting, Dana. What's proving hardest about activation at Northwind?",
    )
    approved = await approvals.approve(db, message, account=account)
    assert approved.status == SuggestionStatus.SCHEDULED
    assert approved.scheduled_for is not None


# ---------------------------------------------------------------------------
# The quality gate still applies to humans
# ---------------------------------------------------------------------------


async def test_a_human_edit_is_re_checked(db, account):
    """Approving supplies intent, not an exemption from the safety rules."""
    invite, _ = await _suggestion(db, account, SuggestionAction.CONNECT)

    with pytest.raises(approvals.ApprovalError) as exc:
        await approvals.approve(
            db, invite, account=account,
            edited_text="Hi Dana, book a call here: calendly.com/me/30min",
        )
    assert "booking link" in str(exc.value).lower()

    await db.refresh(invite)
    assert invite.status == SuggestionStatus.BLOCKED


async def test_a_good_edit_is_accepted(db, account):
    invite, _ = await _suggestion(db, account, SuggestionAction.CONNECT)
    approved = await approvals.approve(
        db, invite, account=account,
        edited_text=(
            "Hi Dana — the activation work at Northwind is exactly the problem "
            "I spend my time on. Would be glad to connect."
        ),
    )
    assert approved.status == SuggestionStatus.SCHEDULED
    assert "activation work" in approved.final_text


async def test_a_suggestion_toward_someone_who_replied_is_refused(db, account):
    """The reply rule holds at the approval boundary too."""
    message, target = await _suggestion(db, account, SuggestionAction.MESSAGE)
    target.status = TargetStatus.REPLIED
    await db.commit()

    with pytest.raises(approvals.ApprovalError) as exc:
        await approvals.approve(db, message, account=account)
    assert "replied" in str(exc.value)


async def test_rejecting_with_suppression_is_permanent(db, account):
    invite, target = await _suggestion(db, account, SuggestionAction.CONNECT)
    await approvals.reject(db, invite, suppress_target=True)

    assert invite.status == SuggestionStatus.REJECTED
    assert target.status == TargetStatus.SUPPRESSED


async def test_bulk_rejection_is_allowed_for_every_action(db, account):
    """Saying no to a hundred things at once cannot hurt anybody."""
    message, _ = await _suggestion(db, account, SuggestionAction.MESSAGE)
    like, _ = await _suggestion(db, account, SuggestionAction.LIKE, text="")

    result = await approvals.reject_bulk(
        db, [str(message.id), str(like.id)], org_id=account._org_id
    )
    assert len(result.approved) == 2
    await db.refresh(message)
    assert message.status == SuggestionStatus.REJECTED


# ---------------------------------------------------------------------------
# The two queues
# ---------------------------------------------------------------------------


async def test_the_queues_are_separate(db, account):
    await _suggestion(db, account, SuggestionAction.LIKE, text="")
    await _suggestion(db, account, SuggestionAction.MESSAGE)

    engagement = await approvals.queue(db, account._org_id, kind="engagement")
    messaging = await approvals.queue(db, account._org_id, kind="messaging")

    assert [s.action for s in engagement] == [SuggestionAction.LIKE]
    assert [s.action for s in messaging] == [SuggestionAction.MESSAGE]


async def test_the_queue_is_ordered_by_fit(db, account):
    """The reviewer's attention is the scarce resource."""
    low, _ = await _suggestion(db, account, SuggestionAction.LIKE, text="")
    high, _ = await _suggestion(db, account, SuggestionAction.LIKE, text="")
    low.relevance_score = 40
    high.relevance_score = 95
    await db.commit()

    rows = await approvals.queue(db, account._org_id, kind="engagement")
    assert rows[0].id == high.id


# ---------------------------------------------------------------------------
# Scoring still behaves after the port
# ---------------------------------------------------------------------------


def test_scoring_survived_the_port():
    from types import SimpleNamespace

    icp = SimpleNamespace(
        titles=["head of growth"], industries=["saas"], keywords=[],
        excluded_keywords=["recruiter"], excluded_titles=[], locations=[],
        seniorities=[], relevance_floor=60,
    )
    good = score_target(
        SimpleNamespace(
            title="Head of Growth", company="Northwind", industry="SaaS",
            headline="Head of Growth at Northwind", location=None,
        ),
        icp,
    )
    excluded = score_target(
        SimpleNamespace(
            title="Head of Growth", company="TalentCo", industry="SaaS",
            headline="Head of Growth | technical recruiter", location=None,
        ),
        icp,
    )

    assert good.score == 100
    assert excluded.excluded
