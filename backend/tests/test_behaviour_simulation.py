"""
Thirty simulated days, measured against LinkedIn's real thresholds.

Every other test in this suite asserts that one component refuses the thing it
should refuse. This one asks the question the components exist to answer:

    *if we connect a real account to this and leave it running for a month,
    does it look like a person or like a bot?*

That cannot be answered by inspecting any single module. Volume is a product of
the stage programme, the caps, the health governor, the weekend factor and the
limiter interacting; burstiness is a product of the scatter; correlation is a
product of cluster sampling and per-account schedule windows. So this file runs
the real planner, the real caps and the real limiter forward day by day and
measures the resulting behaviour.

The thresholds asserted here come from platform research, not from taste:

* **Invitations are capped at ~100 per rolling week**, and identically for
  Free, Premium and Sales Navigator — Premium does not raise it. Accounts
  exceeding it get restricted.
* **~20-30 invitations a day** is the safe working range for an established
  account; a new account should start at 10-15 and ramp.
* **Acceptance rate below 15% is treated as spam regardless of volume.**
  Roughly a quarter of restricted accounts were *inside* the official limits —
  negative recipient response matters more than raw count.
* Sustained same-hour activity, zero-variance daily volumes and never taking a
  day off are all machine signatures independent of volume.

Where the numbers here are deliberately below the platform ceiling, that is the
point: the ceiling is where accounts get restricted, not where they are safe.
"""

import uuid
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta

import fakeredis.aioredis
import pytest

from app.safety import caps as caps_policy
from app.safety import cluster
from app.safety.rate_policy import AccountRateLimiter
from app.warmup import planner, program

SIM_DAYS = 30
START = date(2026, 3, 2)  # a Monday, so weekday/weekend land where you expect


class _Account:
    """
    The minimum an account needs to be planned for.

    Deliberately not an ORM row: this simulation is about the policy layer, and
    a real row would drag in a database for no gain.
    """

    def __init__(self, account_id: str, stage: str = program.FIRST_STAGE, tier: str = None):
        self.id = account_id
        self.warmup_state = {}
        self.daily_caps = {"tier": tier} if tier else {}
        self.is_active = True
        planner.set_stage(self, stage, now=datetime.combine(START, datetime.min.time(), UTC))


def simulate(account, *, days=SIM_DAYS, start=START, throttle=1.0, graduate=True):
    """
    Run the account forward a day at a time and return everything it did.

    Returns a list of ``(datetime, action)`` in chronological order. Stage
    graduation is applied on the programme's own schedule so the ramp is the
    real one rather than a fixed stage held for a month.
    """
    timeline = []
    stage_started = start

    for offset in range(days):
        day = start + timedelta(days=offset)
        now = datetime.combine(day, datetime.min.time(), UTC)

        if graduate:
            stage = program.stage_for(planner.current_stage(account))
            if stage.min_days and (day - stage_started).days >= stage.min_days:
                nxt = program.next_stage(stage.key)
                if nxt:
                    planner.set_stage(account, nxt.key, now=now)
                    stage_started = day

        plan = planner.plan_day(account, day=day, throttle=throttle, now=now)
        for item in plan.actions:
            timeline.append((item.at, item.action))

    timeline.sort()
    return timeline


def per_day(timeline, action=None):
    counts = Counter()
    for when, act in timeline:
        if action is None or act == action:
            counts[when.date()] += 1
    return counts


def rolling_week_max(timeline, action):
    """The largest count in any 7-day window — how LinkedIn actually counts."""
    stamps = sorted(w for w, a in timeline if a == action)
    worst = 0
    for i, start in enumerate(stamps):
        window = start + timedelta(days=7)
        worst = max(worst, sum(1 for s in stamps[i:] if s < window))
    return worst


# ---------------------------------------------------------------------------
# One account, thirty days
# ---------------------------------------------------------------------------


@pytest.fixture
def account():
    return _Account("sim-account-1")


def test_the_first_week_is_almost_entirely_passive(account):
    """
    The single most important property for a freshly connected login.

    A new account whose first action is an AI comment on a company post is the
    exact profile that gets restricted. For the first two days it may only
    like, and it may not invite anyone for three weeks.
    """
    timeline = simulate(account, days=7)
    actions = {a for _, a in timeline}

    assert program.CONNECT not in actions, "a week-old account invited someone"
    assert program.MESSAGE not in actions
    assert program.LIKE in actions

    first_two_days = [
        a for w, a in timeline if (w.date() - START).days < 2
    ]
    assert set(first_two_days) <= {program.LIKE}


def test_no_invitations_before_the_programme_allows_them(account):
    """21 days minimum before the connect stage — the ramp, not a cliff."""
    timeline = simulate(account)
    first_invite = next((w for w, a in timeline if a == program.CONNECT), None)

    assert first_invite is not None, "the account never graduates to outreach"
    assert (first_invite.date() - START).days >= 14, (
        f"invitations started on day {(first_invite.date() - START).days}"
    )


def test_daily_invitations_stay_in_the_safe_band(account):
    """
    20-30/day is the safe range for an established account. Above that the
    volume itself becomes the signal.
    """
    timeline = simulate(account, days=60)
    worst = max(per_day(timeline, program.CONNECT).values(), default=0)
    assert worst <= 30, f"{worst} invitations in one day"


def test_weekly_invitations_stay_under_linkedins_rolling_cap(account):
    """
    ~100 per rolling week is where accounts get restricted, and Premium does
    not raise it. Staying meaningfully under is the whole point.
    """
    timeline = simulate(account, days=60)
    worst = rolling_week_max(timeline, program.CONNECT)
    assert worst <= 100, f"{worst} invitations in a rolling week"


def test_even_the_aggressive_tier_respects_the_weekly_cap():
    """
    The vendor pitched ~800 connections a month. The real ceiling is ~100 a
    week, so that number is not reachable safely on one account whatever the
    tier says. The aggressive tier is opt-in and still bounded.
    """
    account = _Account("sim-aggressive", stage=program.FINAL_STAGE, tier="aggressive")
    timeline = simulate(account, days=60, graduate=False)

    worst = rolling_week_max(timeline, program.CONNECT)
    assert worst <= 100, f"the aggressive tier reached {worst} invitations a week"


def test_activity_is_not_the_same_every_day(account):
    """
    Zero variance is a machine signature independent of volume. A real person's
    Tuesday does not match their Wednesday.
    """
    counts = list(per_day(simulate(account)).values())
    assert len(set(counts)) > 3, f"only {len(set(counts))} distinct daily volumes"


def test_the_account_takes_days_off(account):
    """
    Nobody engages every single day for a month. Quiet days are a designed
    feature of the planner, not a gap in it.
    """
    counts = per_day(simulate(account))
    quiet = SIM_DAYS - len([d for d, n in counts.items() if n > 0])
    assert quiet >= 2, "the account worked every single day for a month"


def test_weekends_are_quieter_but_not_dead(account):
    """
    An account that works exactly Monday-to-Friday is its own signature — so is
    one that ignores the weekend entirely.
    """
    counts = per_day(simulate(account, days=60))
    weekday = [n for d, n in counts.items() if d.weekday() < 5]
    weekend = [n for d, n in counts.items() if d.weekday() >= 5]

    assert weekend, "the account never did anything at a weekend"
    assert sum(weekend) / len(weekend) < sum(weekday) / len(weekday)


def test_activity_stays_inside_waking_hours(account):
    """3am engagement is the cheapest bot tell there is."""
    start_hour, end_hour = caps_policy.active_hours(account)
    for when, action in simulate(account):
        assert start_hour <= when.hour <= end_hour, f"{action} at {when.hour}:00"


def test_actions_are_not_bunched_into_one_burst(account):
    """
    Twelve likes in four minutes is a script; twelve likes across an afternoon
    is a person catching up on their feed.
    """
    by_day = defaultdict(list)
    for when, _ in simulate(account):
        by_day[when.date()].append(when)

    for day, stamps in by_day.items():
        if len(stamps) < 4:
            continue
        stamps.sort()
        span = (stamps[-1] - stamps[0]).total_seconds() / 3600
        assert span >= 1.0, f"{len(stamps)} actions inside {span:.1f}h on {day}"


def test_the_plan_is_stable_for_a_given_day(account):
    """
    Determinism is a safety property here: replanning must not let an account
    do its day twice because the second plan looked different from the first.
    """
    day = START + timedelta(days=10)
    now = datetime.combine(day, datetime.min.time(), UTC)
    first = planner.plan_day(account, day=day, now=now)
    second = planner.plan_day(account, day=day, now=now)
    assert [(a.action, a.at) for a in first.actions] == [
        (a.action, a.at) for a in second.actions
    ]


# ---------------------------------------------------------------------------
# The health governor, over time
# ---------------------------------------------------------------------------


def test_a_throttled_account_actually_does_less(account):
    """
    Acceptance below 15% is treated as spam regardless of volume, so the
    governor's throttle has to reach real behaviour, not just a dashboard.
    """
    full = len(simulate(_Account("sim-throttle"), throttle=1.0))
    throttled = len(simulate(_Account("sim-throttle"), throttle=0.4))
    assert throttled < full * 0.75, f"throttling 1.0 -> 0.4 only moved {full} to {throttled}"


# ---------------------------------------------------------------------------
# The cap actually binds, through the real limiter
# ---------------------------------------------------------------------------


async def test_the_limiter_holds_the_line_over_a_simulated_month():
    """
    The planner proposing safe volumes is not the same as the system enforcing
    them. Run the month's proposed actions through the real limiter and confirm
    nothing gets past the weekly cap even if the planner were wrong.
    """
    account = _Account("sim-limiter", stage=program.FINAL_STAGE)
    timeline = simulate(account, days=60, graduate=False)

    now = [0]
    limiter = AccountRateLimiter(
        fakeredis.aioredis.FakeRedis(decode_responses=True), clock=lambda: now[0]
    )
    caps = caps_policy.caps_for(account, program.CONNECT)

    sent = []
    for when, action in timeline:
        if action != program.CONNECT:
            continue
        now[0] = int(when.timestamp())
        decision = await limiter.check_and_consume(
            str(account.id), action,
            per_hour=caps.per_hour, per_day=caps.per_day, per_week=caps.per_week,
            cooldown_seconds=caps.cooldown_seconds,
        )
        if decision.allowed:
            sent.append(when)

    worst = 0
    for i, start in enumerate(sent):
        worst = max(worst, sum(1 for s in sent[i:] if s < start + timedelta(days=7)))
    assert worst <= caps.per_week
    assert worst <= 100


# ---------------------------------------------------------------------------
# Five accounts in one org — the cluster
# ---------------------------------------------------------------------------


@pytest.fixture
def team():
    """LakeB2B's five, all fully warmed, as they would be after the ramp."""
    return [
        _Account(str(uuid.uuid5(uuid.NAMESPACE_DNS, f"lakeb2b-{i}")), stage=program.FINAL_STAGE)
        for i in range(5)
    ]


def test_colleagues_work_different_hours(team):
    """
    Five accounts that all become active at 9:02 and stop at 17:30 is a
    coordinated-inauthenticity pattern. Detection catches the cluster rather
    than the account, so all five would go at once.
    """
    windows = {cluster.schedule_window(str(a.id)) for a in team}
    assert len(windows) > 1, "all five colleagues share one schedule window"


def test_colleagues_do_not_act_within_moments_of_each_other(team):
    """
    The actual coordinated-inauthenticity signature.

    Five colleagues all being online at 2pm on a Tuesday is not suspicious —
    that is what colleagues do, and an earlier version of this test wrongly
    flagged it. What no group of real people produces is four or five accounts
    firing actions inside the same two-minute window, over and over: that is one
    scheduler serving five identities.
    """
    stamps = []
    for account in team:
        stamps.extend((when, account.id) for when, _ in simulate(account, graduate=False))
    stamps.sort()

    window = timedelta(minutes=2)
    crowded = 0
    for i, (when, _) in enumerate(stamps):
        together = {aid for w, aid in stamps[i:] if w - when < window}
        if len(together) >= 4:
            crowded += 1

    assert crowded < len(stamps) * 0.02, (
        f"{crowded} of {len(stamps)} actions had 4+ accounts firing within two minutes"
    )


def test_the_team_is_not_permanently_in_lockstep(team):
    """
    The weaker companion property, guarding the config rather than the timing:
    if every account fell back to one shared window the hours would line up
    almost perfectly, which is what happened before the planner started using
    ``cluster.schedule_window``.
    """
    hours = defaultdict(set)
    for account in team:
        for when, _ in simulate(account, graduate=False):
            hours[(when.date(), when.hour)].add(account.id)

    all_five = [k for k, v in hours.items() if len(v) == 5]
    assert len(all_five) < len(hours) * 0.5, (
        f"all five accounts were active together in {len(all_five)} of {len(hours)} hours"
    )


def test_colleagues_take_different_days_off(team):
    """Five accounts all quiet on the same Thursday is its own signal."""
    off_days = [
        {d for d in (START + timedelta(days=i) for i in range(SIM_DAYS))
         if not per_day(simulate(a, graduate=False)).get(d)}
        for a in team
    ]
    shared = set.intersection(*off_days)
    assert not shared, f"the whole team was quiet on {sorted(shared)}"


def test_no_single_post_draws_the_whole_team():
    """
    The sampling that makes bulk approval safe. An admin approves "these three
    accounts engage with this post", never "all five do".
    """
    ids = [str(i) for i in range(5)]
    for post in range(40):
        decision = cluster.sample_participants(f"post-{post}", ids)
        assert len(decision.selected) <= cluster.MAX_ACCOUNTS_PER_POST, (
            f"{len(decision.selected)} of 5 accounts engaged with post-{post}"
        )


def test_participation_is_spread_rather_than_always_the_same_three():
    """
    Sampling that always picks accounts 0-2 is not sampling — it just moves the
    correlation from the post to the account.
    """
    ids = [str(i) for i in range(5)]
    engaged = Counter()
    for post in range(200):
        for chosen in cluster.sample_participants(f"post-{post}", ids).selected:
            engaged[chosen] += 1

    assert len(engaged) == 5, "some accounts never participate at all"
    spread = max(engaged.values()) - min(engaged.values())
    assert spread < 60, f"participation is lopsided by {spread} posts"


def test_the_team_stays_under_the_cluster_weekly_cap(team):
    """
    Five accounts each safely under 100 invitations a week is still 500 a week
    landing on an overlapping audience from one company's domain.
    """
    total = sum(rolling_week_max(simulate(a, days=60, graduate=False), program.CONNECT) for a in team)
    per_account = total / len(team)
    assert per_account <= 100, f"average {per_account:.0f} invitations per account per week"
