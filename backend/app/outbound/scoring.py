"""
Relevance scoring: is this the right person to talk to?

Deliberately deterministic — no model call. Three reasons:

1. **Explainability.** The user reviewing a suggestion sees exactly why someone
   was surfaced ("title matches 'Head of Growth'; industry matches 'SaaS'").
   A similarity score from an embedding explains nothing.
2. **Auditability.** The same target always scores the same, so a suppression
   or a floor change has predictable consequences.
3. **Cost.** Scoring runs over every candidate; inference runs only over the
   handful that survive.

An LLM still writes the copy (``outreach/copy.py``) and can be layered on top
as a re-ranker later, but it never decides *whether* someone is in scope.

Scoring is out of 100 across the dimensions the ICP actually defines. An ICP
that specifies only titles is scored purely on titles rather than being
penalized for the fields it left blank — otherwise every ICP would need every
field filled in to clear the floor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional

# Relative weight of each dimension when the ICP defines it.
_WEIGHTS = {
    "title": 35,
    "seniority": 15,
    "industry": 20,
    "keyword": 20,
    "location": 10,
}

# Seniority vocabulary, ordered loosely from most to least senior. Matching is
# on the words that actually appear in LinkedIn headlines.
_SENIORITY_TERMS = {
    "founder": ["founder", "co-founder", "cofounder", "owner"],
    "c_level": ["ceo", "cto", "coo", "cfo", "cmo", "cpo", "cro", "chief"],
    "vp": ["vp", "vice president", "svp", "evp"],
    "director": ["director", "head of"],
    "manager": ["manager", "lead", "principal"],
    "senior": ["senior", "sr.", "staff"],
    "junior": ["junior", "jr.", "associate", "intern", "entry"],
}


@dataclass
class RelevanceResult:
    """Outcome of scoring one target against one ICP."""

    score: int
    reasons: List[str] = field(default_factory=list)
    excluded: bool = False
    exclusion_reason: Optional[str] = None

    def __bool__(self) -> bool:
        return not self.excluded and self.score > 0


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _terms(values: Optional[Iterable]) -> List[str]:
    """Normalize an ICP criteria list, dropping blanks."""
    if not values:
        return []
    return [t for t in (_norm(v) for v in values) if t]


def _contains(haystack: str, needle: str) -> bool:
    """
    Word-boundary-aware substring match.

    Plain ``in`` would make "ai" match "chair" and "cto" match "director".
    Multi-word needles ("head of growth") are matched as a phrase.
    """
    if not needle or not haystack:
        return False
    pattern = r"(?<!\w)" + re.escape(needle) + r"(?!\w)"
    return re.search(pattern, haystack) is not None


def _match_any(haystack: str, needles: List[str]) -> Optional[str]:
    for needle in needles:
        if _contains(haystack, needle):
            return needle
    return None


def _seniority_of(text: str) -> Optional[str]:
    for level, terms in _SENIORITY_TERMS.items():
        if _match_any(text, terms):
            return level
    return None


def score_target(target: Any, icp: Any) -> RelevanceResult:
    """
    Score ``target`` against ``icp``.

    Both are duck-typed: any object (ORM row, dataclass, dict-like) exposing the
    documented attributes works, which keeps this callable from the API, the
    agents and the tests without adapters.

    Exclusions are absolute: a single excluded keyword or title zeroes the
    score regardless of how well everything else matches. "Nearly right" is the
    most expensive kind of wrong in outreach — it is the message that makes
    someone report you.
    """
    title = _norm(_attr(target, "title"))
    headline = _norm(_attr(target, "headline"))
    company = _norm(_attr(target, "company"))
    industry = _norm(_attr(target, "industry"))
    location = _norm(_attr(target, "location"))
    # The blob we keyword-match against: everything we know about the person.
    blob = " ".join(filter(None, [title, headline, company, industry, location]))

    # --- Hard exclusions first ---
    excluded_keywords = _terms(_attr(icp, "excluded_keywords"))
    hit = _match_any(blob, excluded_keywords)
    if hit:
        return RelevanceResult(
            score=0,
            excluded=True,
            exclusion_reason=f"excluded keyword '{hit}'",
            reasons=[f"Excluded: profile mentions '{hit}'"],
        )

    excluded_titles = _terms(_attr(icp, "excluded_titles"))
    hit = _match_any(title or headline, excluded_titles)
    if hit:
        return RelevanceResult(
            score=0,
            excluded=True,
            exclusion_reason=f"excluded title '{hit}'",
            reasons=[f"Excluded: title matches '{hit}'"],
        )

    reasons: List[str] = []
    earned = 0
    available = 0

    # --- Title ---
    titles = _terms(_attr(icp, "titles"))
    if titles:
        available += _WEIGHTS["title"]
        hit = _match_any(title, titles) or _match_any(headline, titles)
        if hit:
            earned += _WEIGHTS["title"]
            reasons.append(f"Title matches '{hit}'")

    # --- Seniority ---
    seniorities = _terms(_attr(icp, "seniorities"))
    if seniorities:
        available += _WEIGHTS["seniority"]
        level = _seniority_of(title) or _seniority_of(headline)
        wanted = {s.replace("-", "_").replace(" ", "_") for s in seniorities}
        if level and (level in wanted or _match_any(title or headline, seniorities)):
            earned += _WEIGHTS["seniority"]
            reasons.append(f"Seniority matches '{level.replace('_', ' ')}'")

    # --- Industry ---
    industries = _terms(_attr(icp, "industries"))
    if industries:
        available += _WEIGHTS["industry"]
        hit = _match_any(industry, industries) or _match_any(blob, industries)
        if hit:
            earned += _WEIGHTS["industry"]
            reasons.append(f"Industry matches '{hit}'")

    # --- Keywords (partial credit: more hits, more score) ---
    keywords = _terms(_attr(icp, "keywords"))
    if keywords:
        available += _WEIGHTS["keyword"]
        hits = [k for k in keywords if _contains(blob, k)]
        if hits:
            ratio = min(1.0, len(hits) / min(len(keywords), 3))
            earned += int(_WEIGHTS["keyword"] * ratio)
            shown = "', '".join(hits[:3])
            reasons.append(f"Mentions '{shown}'")

    # --- Location ---
    locations = _terms(_attr(icp, "locations"))
    if locations:
        available += _WEIGHTS["location"]
        hit = _match_any(location, locations)
        if hit:
            earned += _WEIGHTS["location"]
            reasons.append(f"Located in '{hit}'")

    if available == 0:
        # An ICP with no criteria matches nobody. Failing closed here is what
        # stops an empty ICP from turning into "message the entire database".
        return RelevanceResult(
            score=0,
            reasons=["ICP defines no matching criteria"],
            excluded=True,
            exclusion_reason="empty ICP",
        )

    score = round(100 * earned / available)

    if not reasons:
        reasons.append("No ICP criteria matched this profile")

    return RelevanceResult(score=score, reasons=reasons)


def _attr(obj: Any, name: str) -> Any:
    """Read an attribute from an object or a key from a mapping."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def apply_score(target: Any, icp: Any) -> RelevanceResult:
    """Score a target and write the result back onto it."""
    from app.outbound.models import TargetStatus

    result = score_target(target, icp)
    target.relevance_score = result.score
    target.relevance_reasons = result.reasons
    target.status = TargetStatus.SKIPPED if result.excluded else TargetStatus.SCORED
    return result
