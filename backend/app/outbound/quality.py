"""
Copy quality gate: the deterministic "is this spam?" check.

Every drafted message passes through here before a human ever sees it, and
again before it is sent. It is rules-based on purpose — a model asked "is this
spammy?" will happily approve its own output, and the failure modes we care
about (a leaked ``{{first_name}}``, a booking link in a connection note, a
message that could have been sent to literally anyone) are precisely the ones
plain string checks catch reliably.

Two severities:

- **Blockers** fail the copy outright. It cannot be shown for approval or sent
  until it is rewritten. These are things that are always wrong.
- **Warnings** deduct from a 0-100 quality score and are surfaced to the
  reviewer. These are things that are usually wrong.

The bar is intentionally high. Copy that scores badly here is copy that would
have made the recipient think "this is a bot", which is the one outcome that
costs more than sending nothing at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

# LinkedIn's hard limit on an invitation note.
CONNECT_NOTE_MAX = 300
# Our own limit on a first direct message. Longer than this doesn't get read.
MESSAGE_MAX = 900
MESSAGE_IDEAL_MAX = 600
COMMENT_MAX = 400

# Phrases that mark copy as template-generated outreach. Recipients have seen
# each of these a thousand times.
_TIRED_PHRASES = [
    "i hope this message finds you well",
    "i hope this email finds you well",
    "hope you're doing well",
    "hope you are doing well",
    "quick question",
    "just following up",
    "just checking in",
    "circle back",
    "touch base",
    "reach out to see if",
    "as per my last",
    "i wanted to reach out",
    "i came across your profile",
    "i stumbled upon your profile",
    "let me know if you'd be open",
    "does that sound like something",
    "pick your brain",
    "synergy",
    "synergies",
    "game-changer",
    "game changer",
    "revolutionary",
    "cutting-edge solution",
    "world-class",
    "best-in-class",
    "10x your",
    "guaranteed results",
    "limited time",
    "act now",
    "dear sir or madam",
    "to whom it may concern",
]

# Asks that don't belong in a first touch, especially not a connection note.
_HARD_CTA = [
    "book a call",
    "book a time",
    "book a demo",
    "schedule a call",
    "schedule a demo",
    "hop on a call",
    "jump on a call",
    "15 minutes of your time",
    "15 mins of your time",
    "30 minutes of your time",
    "grab 15",
    "free trial",
    "sign up today",
    "buy now",
]

_LINK_PATTERN = re.compile(r"(https?://|www\.|\b[\w.-]+\.(?:com|io|co|ai|net|org)/)", re.I)
_CALENDAR_PATTERN = re.compile(r"(calendly|savvycal|hubspot\.com/meetings|cal\.com|zcal)", re.I)
# Unsubstituted template variables in every syntax we might plausibly emit.
_PLACEHOLDER_PATTERN = re.compile(
    r"(\{\{.*?\}\}|\{[a-z_]+\}|\[(?:first[_ ]?name|name|company|title|role)\]"
    r"|<[a-z_]+>|\bFIRST[_ ]NAME\b|\bXYZ\b|\bACME\b|\bLorem ipsum\b)",
    re.I,
)
_EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF]"
)
# Acronyms that are legitimately capitalized in professional copy.
_ALLOWED_CAPS = {
    "AI", "ML", "API", "SaaS", "B2B", "B2C", "CEO", "CTO", "COO", "CFO", "CMO",
    "VP", "HR", "IT", "SEO", "CRM", "ERP", "ROI", "KPI", "OKR", "SDR", "AE",
    "UK", "US", "USA", "EU", "NHS", "GTM", "PLG", "LLM", "SQL", "AWS", "GCP",
    "IPO", "PE", "VC", "NPS", "ARR", "MRR", "QA", "UX", "UI", "PM",
}

# Generic openers that prove nothing about the recipient was read.
_GENERIC_OPENERS = ["hi there", "hey there", "hello there", "dear friend", "hi friend"]

# Words common enough in professional headlines that echoing one back proves
# nothing. Without this, "growth" appearing in both a headline and a generic
# message would be scored as evidence the profile was read.
_GENERIC_HEADLINE_WORDS = {
    "growth", "manager", "director", "founder", "leader", "leading", "company",
    "business", "product", "customer", "customers", "marketing", "digital",
    "strategy", "strategic", "solutions", "services", "software", "technology",
    "platform", "consultant", "consulting", "specialist", "professional",
    "experience", "helping", "building", "passionate", "driving", "focused",
    "operations", "engineering", "development", "management", "partner",
}


@dataclass
class QualityReport:
    """Verdict on one piece of drafted copy."""

    score: int
    passed: bool
    blockers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.passed

    @property
    def all_issues(self) -> List[str]:
        return self.blockers + self.warnings


def _limit_for(action: str) -> int:
    if action == "connect":
        return CONNECT_NOTE_MAX
    if action == "comment":
        return COMMENT_MAX
    return MESSAGE_MAX


def check_copy(
    text: Optional[str],
    action: str,
    target: Any = None,
    *,
    min_score: int = 70,
    allow_scheduler_link: bool = False,
) -> QualityReport:
    """
    Evaluate drafted copy for ``action`` aimed at ``target``.

    ``target`` is duck-typed (``first_name``, ``full_name``, ``company``,
    ``title``, ``headline``) and used to verify the copy is actually about this
    person rather than about nobody in particular.

    ``allow_scheduler_link`` is set only by the booking step of a sequence,
    after the prospect has replied and been qualified. A calendar link in a
    first touch is spam; the same link once someone has asked to talk is what
    they wanted. The rule is about *when*, not about the link.
    """
    blockers: List[str] = []
    warnings: List[str] = []
    score = 100

    body = (text or "").strip()
    if not body:
        return QualityReport(
            score=0, passed=False, blockers=["Message is empty"], stats={"length": 0}
        )

    lowered = body.lower()
    limit = _limit_for(action)
    words = body.split()

    # --- Length ---
    if len(body) > limit:
        blockers.append(
            f"Too long for a {action}: {len(body)} characters (limit {limit})"
        )
    elif action == "message" and len(body) > MESSAGE_IDEAL_MAX:
        warnings.append(f"Long for a first message ({len(body)} chars); shorter reads better")
        score -= 10

    if len(words) < 8:
        warnings.append("Very short — likely too thin to be worth sending")
        score -= 15

    # --- Unsubstituted placeholders: always a blocker ---
    placeholder = _PLACEHOLDER_PATTERN.search(body)
    if placeholder:
        blockers.append(f"Contains an unfilled placeholder: '{placeholder.group(0)}'")

    # --- Links ---
    has_calendar = bool(_CALENDAR_PATTERN.search(body))
    if has_calendar and not allow_scheduler_link:
        blockers.append("Contains a booking link — never in a first touch")
    elif _LINK_PATTERN.search(body) and not (has_calendar and allow_scheduler_link):
        if action == "connect":
            blockers.append("Contains a link — connection notes with links get reported")
        elif not allow_scheduler_link:
            warnings.append("Contains a link in a first message")
            score -= 20

    # --- Hard CTAs ---
    # Asking for time is the entire point of the booking step, so the CTA rules
    # relax exactly where a scheduler link is permitted.
    if not allow_scheduler_link:
        for phrase in _HARD_CTA:
            if phrase in lowered:
                if action == "connect":
                    blockers.append(f"Pitches in a connection request: '{phrase}'")
                else:
                    warnings.append(f"Asks for time up front: '{phrase}'")
                    score -= 15
                break

    # --- Tired template phrases ---
    tired = [p for p in _TIRED_PHRASES if p in lowered]
    if tired:
        warnings.append(f"Uses worn-out outreach phrasing: '{tired[0]}'")
        score -= 20 * min(len(tired), 3)

    # --- Generic opener ---
    for opener in _GENERIC_OPENERS:
        if lowered.startswith(opener):
            warnings.append(f"Generic opener '{opener}' — no sign the profile was read")
            score -= 30
            break

    # --- Personalization: is this message about *this* person? ---
    if target is not None:
        signals = _personalization_signals(lowered, target)
        if not signals:
            warnings.append(
                "No personal detail — this could have been sent to anyone"
            )
            score -= 25
    else:
        signals = []

    # --- Shouting and punctuation ---
    shouty = [
        w for w in words
        if len(w) > 3 and w.isupper() and w.strip(".,!?") not in _ALLOWED_CAPS
    ]
    if shouty:
        warnings.append(f"Shouting in caps: '{shouty[0]}'")
        score -= 15

    exclamations = body.count("!")
    if exclamations > 2:
        warnings.append(f"{exclamations} exclamation marks reads as hype")
        score -= 15
    if "!!" in body:
        warnings.append("Repeated exclamation marks")
        score -= 10

    emoji_count = len(_EMOJI_PATTERN.findall(body))
    if emoji_count > 2:
        warnings.append(f"{emoji_count} emoji is a lot for professional outreach")
        score -= 10

    # --- "I/we" heavy copy: talking about yourself, not them ---
    self_refs = len(re.findall(r"\b(i|we|our|my|us)\b", lowered))
    you_refs = len(re.findall(r"\b(you|your|you're)\b", lowered))
    if self_refs > 0 and you_refs == 0:
        warnings.append("Entirely about the sender — never mentions the recipient")
        score -= 30
    elif self_refs >= 3 * max(you_refs, 1):
        warnings.append("Heavily sender-focused ('I/we' far outweighs 'you')")
        score -= 15

    score = max(0, min(100, score))
    passed = not blockers and score >= min_score

    return QualityReport(
        score=score,
        passed=passed,
        blockers=blockers,
        warnings=warnings,
        stats={
            "length": len(body),
            "words": len(words),
            "personalization_signals": signals,
            "emoji": emoji_count,
        },
    )


def _personalization_signals(lowered: str, target: Any) -> List[str]:
    """Which concrete facts about the target actually appear in the copy."""
    signals = []

    def value(name: str):
        if isinstance(target, dict):
            return target.get(name)
        return getattr(target, name, None)

    first_name = value("first_name")
    if not first_name and value("full_name"):
        first_name = str(value("full_name")).split()[0]
    if first_name and str(first_name).lower() in lowered:
        signals.append("first_name")

    for field_name in ("company", "title", "industry"):
        raw = value(field_name)
        if raw and str(raw).lower() in lowered:
            signals.append(field_name)

    # A *distinctive* word from the headline counts as evidence the profile was
    # read. Generic business vocabulary does not — echoing "growth" back at a
    # growth lead says nothing.
    headline = str(value("headline") or "")
    for word in re.findall(r"\b[a-z]{6,}\b", headline.lower()):
        if word in lowered and word not in _GENERIC_HEADLINE_WORDS:
            signals.append("headline")
            break

    # A reference to something they actually posted is the strongest signal.
    context = value("context") or {}
    if isinstance(context, dict):
        snippet = str(context.get("post_text") or "")
        for word in re.findall(r"\b[a-z]{6,}\b", snippet.lower())[:40]:
            if word in lowered:
                signals.append("their_post")
                break

    return sorted(set(signals))
