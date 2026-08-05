"""
Warm-up programme tests.

The warm-up is what stands between "a fresh login" and "a restricted account",
so the tests here are mostly about what an account is *not* allowed to do, and
about the conditions under which it gets pulled backwards.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.safety import health as health_module
from app.safety.health import CAUTION, DANGER, HEALTHY, UNKNOWN, Funnel, assess
from app.warmup import planner, program
from app.warmup import service as warmup_service



# ----------------------------------------------------------------------
# Capability gating (synchronous — no DB needed)
# ----------------------------------------------------------------------


def test_a_fresh_account_may_only_observe():
    """Day one: no invitations, no messages, no comments, no posts."""
    assert program.is_allowed("observe", program.LIKE)
    for action in (program.COMMENT, program.POST, program.CONNECT, program.MESSAGE):
        assert not program.is_allowed("observe", action), action


def test_capability_unlocks_in_order():
    """Each stage adds capability and never removes it."""
    seen = set()
    for stage in program.STAGES:
        assert seen <= stage.allowed, f"{stage.key} removed a capability"
        seen = set(stage.allowed)
    assert program.STAGES[-1].allowed >= {
        program.CONNECT, program.MESSAGE, program.POST, program.COMMENT
    }


def test_messaging_is_the_last_thing_unlocked():
    """Follow-up messaging only exists once invitations are established."""
    for stage in program.STAGES[:-1]:
        assert program.MESSAGE not in stage.allowed, stage.key


def test_program_takes_a_meaningful_amount_of_time():
    """A ramp that finishes in two days isn't a ramp."""
    assert program.estimated_days_to_outreach() >= 14


# ----------------------------------------------------------------------
# Graduation
# ----------------------------------------------------------------------


def test_time_alone_does_not_graduate_an_account():
    """The account must have actually done the work, not just waited."""
    result = program.evaluate_graduation(
        "react", days_in_stage=30, totals={"like": 2, "follow": 0}
    )
    assert not result.ready
    assert any("like" in b for b in result.blockers)


def test_work_alone_does_not_graduate_an_account():
    """Cramming a stage's activity into one day doesn't skip the clock."""
    result = program.evaluate_graduation(
        "react", days_in_stage=0, totals={"like": 500, "follow": 50}
    )
    assert not result.ready
    assert any("days" in b for b in result.blockers)


def test_account_graduates_when_time_and_work_are_both_done():
    result = program.evaluate_graduation(
        "react", days_in_stage=4, totals={"like": 40, "follow": 8}
    )
    assert result.ready
    assert result.target == "converse"


def test_a_challenge_walks_the_account_backwards():
    """A checkpoint means LinkedIn already decided something is wrong."""
    result = program.evaluate_graduation(
        "connect",
        days_in_stage=99,
        totals={"connect": 500},
        acceptance_rate=0.9,
        invites_sent=500,
        had_challenge=True,
    )
    assert not result.ready
    assert result.demote_to == "publish"
    assert "challenged" in result.blockers[0].lower()


def test_collapsed_acceptance_demotes_rather_than_holds():
    """Continuing to invite at 8% acceptance is how accounts get banned."""
    result = program.evaluate_graduation(
        "connect",
        days_in_stage=30,
        totals={"connect": 100},
        acceptance_rate=0.08,
        invites_sent=100,
    )
    assert not result.ready
    assert result.demote_to == "publish"


def test_mediocre_acceptance_blocks_scaling_up_but_does_not_demote():
    result = program.evaluate_graduation(
        "connect",
        days_in_stage=30,
        totals={"connect": 100},
        acceptance_rate=0.20,
        invites_sent=100,
    )
    assert not result.ready
    assert result.demote_to is None
    assert any("acceptance" in b for b in result.blockers)


def test_small_samples_do_not_trigger_demotion():
    """Two rejections out of three is not a 33% acceptance rate."""
    result = program.evaluate_graduation(
        "connect", days_in_stage=10, totals={"connect": 3},
        acceptance_rate=0.0, invites_sent=3,
    )
    assert result.demote_to is None


# ----------------------------------------------------------------------
# Acceptance-rate governor
# ----------------------------------------------------------------------


def test_healthy_acceptance_runs_at_full_volume():
    report = assess(Funnel(invites_sent=60, invites_accepted=25))
    assert report.verdict == HEALTHY
    assert report.throttle == 1.0
    assert not report.suspended_actions


def test_low_acceptance_stops_invitations_entirely():
    report = assess(Funnel(invites_sent=100, invites_accepted=9))
    assert report.verdict == DANGER
    assert report.blocks("connect")
    # Messaging existing connections is still fine -- only new invitations stop.
    assert not report.blocks("message")
    assert any("targeting problem" in a for a in report.advice)


def test_middling_acceptance_reduces_volume_smoothly():
    low = assess(Funnel(invites_sent=100, invites_accepted=17))
    high = assess(Funnel(invites_sent=100, invites_accepted=29))
    assert low.verdict == high.verdict == CAUTION
    assert 0 < low.throttle < high.throttle < 1.0


def test_small_sample_gives_no_verdict_and_no_throttle():
    report = assess(Funnel(invites_sent=4, invites_accepted=0))
    assert report.verdict == UNKNOWN
    assert report.throttle == 1.0
    assert "more for a reading" in report.headline


def test_a_challenge_overrides_a_good_acceptance_rate():
    report = assess(Funnel(invites_sent=100, invites_accepted=80), had_challenge=True)
    assert report.verdict == DANGER
    assert report.blocks("connect")


def test_funnel_rates_are_computed_from_real_counts():
    funnel = Funnel(
        invites_sent=100, invites_accepted=40, messages_sent=40, replies=12, booked=4
    )
    assert funnel.acceptance_rate == 0.4
    assert funnel.reply_rate == 0.3
    assert funnel.as_dict()["acceptance_rate"] == 40.0


# ----------------------------------------------------------------------
# The daily plan
# ----------------------------------------------------------------------


class FakeAccount:
    def __init__(self, stage="react", created_days_ago=1, caps=None):
        self.id = "11111111-1111-1111-1111-111111111111"
        self.created_at = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
        self.daily_caps = caps or {"tier": "standard", "active_hours": [8, 19]}
        self.warmup_state = {}
        planner.set_stage(self, stage)


def test_plan_only_contains_actions_the_stage_allows():
    plan = planner.plan_day(FakeAccount(stage="react"), day=date(2026, 6, 3))
    assert plan.actions
    for item in plan.actions:
        assert item.action in program.stage_for("react").allowed


def test_plan_is_stable_for_a_day_and_changes_the_next():
    account = FakeAccount(stage="converse")
    monday = planner.plan_day(account, day=date(2026, 6, 1))
    monday_again = planner.plan_day(account, day=date(2026, 6, 1))
    tuesday = planner.plan_day(account, day=date(2026, 6, 2))

    assert monday.counts == monday_again.counts
    assert [a.at for a in monday.actions] == [a.at for a in monday_again.actions]
    # Different day, different rhythm (overwhelmingly likely, and seeded so
    # this is deterministic rather than flaky).
    assert monday.counts != tuesday.counts or [a.at for a in monday.actions] != [
        a.at for a in tuesday.actions
    ]


def test_actions_land_inside_the_active_hours_window():
    account = FakeAccount(stage="converse", caps={"tier": "standard", "active_hours": [9, 17]})
    plan = planner.plan_day(account, day=date(2026, 6, 3))
    for item in plan.actions:
        assert 9 <= item.at.hour < 17, item.at


def test_actions_are_never_evenly_spaced():
    """Even spacing is the most obvious automation signature there is."""
    plan = planner.plan_day(FakeAccount(stage="publish"), day=date(2026, 6, 3))
    times = [a.at for a in plan.actions if a.action == "like"]
    if len(times) >= 4:
        gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
        assert len(set(gaps)) > 1


def test_weekends_are_reduced_not_empty_over_time():
    """A schedule that is exactly Mon-Fri is its own signature."""
    account = FakeAccount(stage="publish")
    saturday = planner.plan_day(account, day=date(2026, 6, 6))  # a Saturday
    assert any("Weekend" in n for n in saturday.notes)


def test_throttle_reduces_the_days_volume():
    account = FakeAccount(stage="full")
    full = planner.plan_day(account, day=date(2026, 6, 3), throttle=1.0)
    slow = planner.plan_day(account, day=date(2026, 6, 3), throttle=0.3)
    assert sum(slow.counts.values()) < sum(full.counts.values())
    assert any("Throttled" in n for n in slow.notes)
