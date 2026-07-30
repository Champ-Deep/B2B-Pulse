"""
Preflight: prove a real account works using only read-only calls.

When you tie in a live LinkedIn account for the first time, the riskiest thing
you can do is find out whether the integration works by *sending something*. A
malformed invitation is not recoverable — it went to a real person under the
user's name, and LinkedIn counted it.

So preflight exercises the whole stack — session construction, TLS fingerprint,
device headers, the CSRF token, endpoint shapes, response parsing — using calls
that only ever read:

    whoami          → is the session valid, and who is it?
    fetch_profile   → can we resolve a person and parse the response?
    fetch_inbox     → can we read conversations (needed for reply detection)?
    fetch_activity  → can we see posts (needed for engagement)?

If whoami succeeds, the hard part is proven: headers, cookies and CSRF are all
correct, because Voyager rejects the request outright otherwise. The remaining
checks tell you which *features* will work, and a failure in one of them is a
known gap rather than a surprise later.

Nothing here writes, likes, connects, messages or posts. It is safe to run
against a production account at any time.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional

from app.transports.base import (
    TransportChallenge,
    TransportError,
    TransportUnavailable,
)

logger = logging.getLogger(__name__)


@dataclass
class Check:
    """One preflight probe."""

    name: str
    label: str
    ok: bool
    # Whether a failure here blocks using the account at all.
    critical: bool = False
    detail: Optional[dict] = None
    error: Optional[str] = None
    impact: str = ""


@dataclass
class PreflightReport:
    ok: bool = False
    identity: dict = field(default_factory=dict)
    checks: List[Check] = field(default_factory=list)
    summary: str = ""
    next_steps: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "identity": self.identity,
            "summary": self.summary,
            "next_steps": self.next_steps,
            "checks": [
                {
                    "name": c.name,
                    "label": c.label,
                    "ok": c.ok,
                    "critical": c.critical,
                    "detail": c.detail,
                    "error": c.error,
                    "impact": c.impact,
                }
                for c in self.checks
            ],
        }


async def run_preflight(
    account,
    *,
    transport: Any = None,
    sample_public_id: Optional[str] = None,
) -> PreflightReport:
    """
    Run every read-only probe against a live account.

    ``sample_public_id`` is any public profile handle used to test profile
    resolution; it defaults to the account's own profile, which is always safe
    to fetch.
    """
    from app.services.account_service import (
        LiveAccount,
        cookies_to_auth_blob,
        decrypt_cookies,
    )

    live = (
        account
        if isinstance(account, LiveAccount)
        else LiveAccount(account, cookies_to_auth_blob(decrypt_cookies(account)))
    )
    client = transport or _default_transport(live)

    report = PreflightReport()

    # --- 1. Session validity (the critical one) ------------------------
    identity_check = await _probe(
        client,
        "whoami",
        "Session is valid",
        critical=True,
        impact="Nothing works until the session is accepted by LinkedIn.",
        call=lambda: client.whoami(live),
    )
    report.checks.append(identity_check)

    if not identity_check.ok:
        report.ok = False
        report.summary = "LinkedIn rejected this session — the cookies are wrong or expired."
        report.next_steps = [
            "Sign in to LinkedIn in a browser, then copy a fresh li_at cookie.",
            "Copy JSESSIONID too, without the surrounding quotes — write actions "
            "need it for the CSRF token.",
            "Make sure both cookies come from the same browser session.",
        ]
        return report

    report.identity = identity_check.detail or {}
    own_handle = sample_public_id or report.identity.get("public_id")

    # --- 2. Feature probes (non-critical) ------------------------------
    if own_handle:
        report.checks.append(
            await _probe(
                client,
                "fetch_profile",
                "Can read profiles",
                impact="Without this, prospects can't be enriched, so copy will be "
                "less specific.",
                call=lambda: client.fetch_profile(live, own_handle),
            )
        )

    report.checks.append(
        await _probe(
            client,
            "fetch_inbox",
            "Can read conversations",
            impact="Without this, replies can't be detected — sequences would keep "
            "following up after someone answers.",
            call=lambda: client.fetch_inbox(live),
        )
    )

    if report.identity.get("member_urn"):
        report.checks.append(
            await _probe(
                client,
                "fetch_activity",
                "Can read posts",
                impact="Without this, the account can't find posts to engage with "
                "during warm-up.",
                call=lambda: client.fetch_activity(live, report.identity["member_urn"]),
            )
        )

    failed = [c for c in report.checks if not c.ok]
    report.ok = True  # session works; that's the bar for "usable"

    if not failed:
        name = report.identity.get("display_name") or "this account"
        report.summary = f"Everything checks out — connected as {name}."
        report.next_steps = [
            "Start warm-up. The account will like and follow for the first few "
            "days before anything else is unlocked.",
        ]
    else:
        report.summary = (
            f"Connected as {report.identity.get('display_name') or 'this account'}, "
            f"but {len(failed)} feature "
            f"{'probe' if len(failed) == 1 else 'probes'} failed."
        )
        report.next_steps = [
            f"{c.label} failed — {c.impact}" for c in failed
        ] + [
            "The session itself is fine, so warm-up can still start. These are "
            "endpoint shapes to fix, not account problems.",
        ]

    return report


async def _probe(client, name: str, label: str, *, call, critical: bool = False, impact: str = "") -> Check:
    """Run one probe, converting every failure mode into a readable Check."""
    try:
        result = await call()
    except TransportChallenge as exc:
        return Check(
            name=name, label=label, ok=False, critical=critical,
            error=f"LinkedIn challenged the request: {exc}",
            impact=impact or "The account may need to clear a checkpoint in a browser.",
        )
    except TransportUnavailable as exc:
        return Check(
            name=name, label=label, ok=False, critical=critical,
            error=str(exc), impact=impact,
        )
    except TransportError as exc:
        return Check(
            name=name, label=label, ok=False, critical=critical,
            error=str(exc), impact=impact,
        )
    except Exception as exc:  # unexpected shapes shouldn't crash preflight
        logger.exception("Preflight probe %s raised", name)
        return Check(
            name=name, label=label, ok=False, critical=critical,
            error=f"unexpected error: {exc}", impact=impact,
        )

    if not result.success:
        return Check(
            name=name, label=label, ok=False, critical=critical,
            error=result.error or "call did not succeed", impact=impact,
        )

    return Check(
        name=name, label=label, ok=True, critical=critical,
        detail=_summarize(name, result.detail or {}),
    )


def _summarize(name: str, detail: dict) -> dict:
    """Keep preflight output small and free of anything sensitive."""
    if name == "whoami":
        return {
            k: detail.get(k)
            for k in ("member_urn", "public_id", "display_name", "headline", "profile_url")
        }
    if name == "fetch_profile":
        return {k: detail.get(k) for k in ("member_urn", "display_name", "headline", "location")}
    if name == "fetch_inbox":
        return {"conversations": len(detail.get("conversations") or [])}
    if name == "fetch_activity":
        return {"posts": len(detail.get("posts") or [])}
    return {}


def _default_transport(live):
    from app.services.account_service import get_transport

    return get_transport(live)
