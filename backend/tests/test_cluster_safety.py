"""
Cluster safety.

Every other control protects an account from itself; this protects the accounts
from each other. Five colleagues engaging with the same post, from the same
model, in the same hours, is a coordinated-inauthenticity pattern — and
detection catches the cluster, not the account, so all five go at once.
"""

import uuid

from app.safety import cluster


ACCOUNTS = [f"account-{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def test_not_every_account_engages_with_a_post():
    """The core property: the group must not move as one."""
    decision = cluster.sample_participants("post-1", ACCOUNTS)

    assert 0 < len(decision.selected) < len(ACCOUNTS)
    assert decision.skipped
    assert len(decision.selected) <= cluster.MAX_ACCOUNTS_PER_POST


def test_sampling_is_deterministic():
    """
    A retry must not re-roll.

    A Celery task that retries would otherwise pick a different set, and an
    admin reviewing a queue would see something other than what runs.
    """
    first = cluster.sample_participants("post-1", ACCOUNTS)
    second = cluster.sample_participants("post-1", ACCOUNTS)
    assert first.selected == second.selected


def test_different_posts_select_different_accounts():
    """Participation rotates without anyone tracking whose turn it is."""
    selections = {
        tuple(cluster.sample_participants(f"post-{i}", ACCOUNTS).selected)
        for i in range(25)
    }
    assert len(selections) > 1, "the same accounts were chosen for every post"


def test_participation_is_spread_roughly_evenly():
    """No account should be doing all the work, or none of it."""
    counts = dict.fromkeys(ACCOUNTS, 0)
    for i in range(300):
        for account in cluster.sample_participants(f"post-{i}", ACCOUNTS).selected:
            counts[account] += 1

    assert all(counts.values()), f"an account never participated: {counts}"
    # No account should carry more than double another's share.
    assert max(counts.values()) < 2 * min(counts.values()), counts


def test_a_single_account_still_participates():
    """One-person orgs must not be sampled out of existence."""
    decision = cluster.sample_participants("post-1", ["only-account"])
    assert decision.selected == ["only-account"]


def test_no_accounts_is_handled():
    decision = cluster.sample_participants("post-1", [])
    assert decision.selected == []
    assert "no eligible" in decision.reason


def test_the_hard_ceiling_beats_the_rate():
    """A large org must not put twenty accounts on one post."""
    many = [f"account-{i}" for i in range(40)]
    decision = cluster.sample_participants("post-1", many)
    assert len(decision.selected) <= cluster.MAX_ACCOUNTS_PER_POST


def test_the_decision_explains_itself():
    decision = cluster.sample_participants("post-1", ACCOUNTS)
    assert "sampled" in decision.reason
    assert all(reason for reason in decision.skipped.values())


# ---------------------------------------------------------------------------
# Independent schedules
# ---------------------------------------------------------------------------


def test_accounts_get_different_activity_windows():
    """All five working exactly 9-to-5 together is its own signature."""
    windows = {cluster.schedule_window(a) for a in ACCOUNTS}
    assert len(windows) > 1


def test_a_window_is_stable_for_an_account():
    assert cluster.schedule_window("account-1") == cluster.schedule_window("account-1")


def test_windows_stay_within_plausible_working_hours():
    for account in ACCOUNTS:
        start, end = cluster.schedule_window(account)
        assert 6 <= start <= 11, account
        assert end <= 22, account
        assert end - start >= 8, account


# ---------------------------------------------------------------------------
# Cluster caps and correlation, against the database
# ---------------------------------------------------------------------------


async def _org_with_engagement(db, client, clerk_token, pairs):
    """Build an org whose users have engaged with the given (user_idx, post) pairs."""
    from app.models.engagement import ActionStatus, ActionType, EngagementAction
    from app.models.post import Post
    from app.models.tracked_page import PageType, TrackedPage
    from app.models.integration import Platform
    from sqlalchemy import select
    from app.models.user import User

    claims = {"org_id": "org_cluster", "org_name": "Cluster Co"}
    users = []
    for _ in range(3):
        headers = {"Authorization": f"Bearer {clerk_token(**claims)}"}
        me = (await client.get("/api/auth/me", headers=headers)).json()
        users.append(uuid.UUID(me["id"]))

    org_id = (
        await db.execute(select(User.org_id).where(User.id == users[0]))
    ).scalar_one()

    page = TrackedPage(
        id=uuid.uuid4(),
        org_id=org_id,
        platform=Platform.LINKEDIN,
        url="https://www.linkedin.com/company/acme",
        name="Acme",
        page_type=PageType.COMPANY,
    )
    db.add(page)
    await db.flush()

    posts = {}
    for _, post_name in pairs:
        if post_name not in posts:
            post = Post(
                id=uuid.uuid4(),
                tracked_page_id=page.id,
                platform=Platform.LINKEDIN,
                external_post_id=post_name,
                url=f"https://linkedin.com/{post_name}",
            )
            db.add(post)
            await db.flush()
            posts[post_name] = post

    for user_idx, post_name in pairs:
        db.add(
            EngagementAction(
                id=uuid.uuid4(),
                post_id=posts[post_name].id,
                user_id=users[user_idx],
                action_type=ActionType.LIKE,
                status=ActionStatus.COMPLETED,
            )
        )

    await db.commit()
    return org_id, page, posts, users


async def test_post_headroom_shrinks_as_accounts_engage(db, client, clerk_token):
    _, _, posts, _ = await _org_with_engagement(
        db, client, clerk_token, [(0, "p1"), (1, "p1")]
    )
    headroom = await cluster.post_headroom(db, posts["p1"].id)
    assert headroom == cluster.MAX_ACCOUNTS_PER_POST - 2


async def test_a_saturated_post_admits_nobody_else(db, client, clerk_token):
    pairs = [(i, "p1") for i in range(cluster.MAX_ACCOUNTS_PER_POST)]
    _, page, posts, users = await _org_with_engagement(db, client, clerk_token, pairs[:3])

    decision = await cluster.eligible_participants(
        db, posts["p1"].id, page.id, [str(u) for u in users]
    )
    assert decision.selected == []
    assert "cluster limit" in decision.reason


async def test_company_headroom_counts_across_posts(db, client, clerk_token):
    """
    Per-post limits alone don't stop five accounts working through six posts
    from the same company in an afternoon.
    """
    _, page, _, _ = await _org_with_engagement(
        db, client, clerk_token,
        [(0, "p1"), (1, "p2"), (2, "p3")],
    )
    headroom = await cluster.company_headroom(db, page.id)
    assert headroom == cluster.MAX_ACCOUNTS_PER_COMPANY_PER_DAY - 3


async def test_correlation_is_low_when_accounts_engage_with_different_posts(
    db, client, clerk_token
):
    org_id, _, _, _ = await _org_with_engagement(
        db, client, clerk_token,
        [(0, "a1"), (0, "a2"), (1, "b1"), (1, "b2"), (2, "c1")],
    )
    report = await cluster.measure_correlation(db, org_id)
    assert report.verdict == "healthy"
    assert report.max_overlap == 0.0


async def test_correlation_flags_accounts_moving_together(db, client, clerk_token):
    """Two accounts engaging with everything the same are effectively one actor."""
    org_id, _, _, _ = await _org_with_engagement(
        db, client, clerk_token,
        [(0, "p1"), (0, "p2"), (0, "p3"), (1, "p1"), (1, "p2"), (1, "p3")],
    )
    report = await cluster.measure_correlation(db, org_id)

    assert report.verdict == "danger"
    assert report.max_overlap == 1.0
    assert report.advice, "a danger verdict must say what to do about it"


async def test_correlation_needs_something_to_compare(db, client, clerk_token):
    org_id, _, _, _ = await _org_with_engagement(db, client, clerk_token, [(0, "p1")])
    report = await cluster.measure_correlation(db, org_id)
    assert report.verdict == "unknown"
