"""
Persona resolution: merging an org's direction with a person's own voice.

The whole value of the persona layer is in :func:`resolve`. Everything else is
CRUD.

Merge rules, and why each is what it is
---------------------------------------
- **Voice and bio: the account wins outright.** A person's voice is the one
  thing that must not be homogenised by a central setting — that is what makes
  five accounts sound like five people.
- **Expertise and content pillars: the account replaces if it sets them.** An
  empty list means "use the org's"; a non-empty one means "mine, not theirs".
  Merging these would give everyone the union of everybody's topics, which is
  how an account ends up commenting credibly on nothing.
- **Guardrails: cumulative, never overridable.** A person may add prohibitions;
  they may not remove one the org set. A guardrail that an individual could
  switch off is not a guardrail.
- **Autonomy: the *stricter* of the two.** If the org says comments need
  approval, one account cannot decide otherwise. Same principle as the caps
  merge — combining two controls must never loosen either.

An account with no persona of its own resolves to the org persona unchanged,
which is the sane default for someone who has just been added.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.personas.models import Persona


@dataclass
class ResolvedPersona:
    """The effective persona for one account, after inheritance."""

    name: str = ""
    bio: str = ""
    voice: dict = field(default_factory=dict)
    expertise: list = field(default_factory=list)
    content_pillars: list = field(default_factory=list)
    icp: dict = field(default_factory=dict)
    guardrails: dict = field(default_factory=dict)
    autonomy: dict = field(default_factory=dict)

    # Which fields came from the account rather than the org, so the UI can
    # show what has been personalised.
    overridden: list = field(default_factory=list)
    org_persona_id: str | None = None
    account_persona_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "bio": self.bio,
            "voice": self.voice,
            "expertise": self.expertise,
            "content_pillars": self.content_pillars,
            "icp": self.icp,
            "guardrails": self.guardrails,
            "autonomy": self.autonomy,
            "overridden": self.overridden,
            "org_persona_id": self.org_persona_id,
            "account_persona_id": self.account_persona_id,
        }

    def requires_approval(self, action: str) -> bool:
        """
        Does this action need a human before it goes out?

        Defaults to True for anything unspecified. An action nobody thought
        about should not ship unattended.
        """
        setting = (self.autonomy or {}).get(action)
        if setting is None:
            return True
        return not bool(setting)


async def org_persona(db: AsyncSession, org_id: uuid.UUID) -> Persona | None:
    """The org-level persona everyone inherits from."""
    return (
        await db.execute(
            select(Persona).where(
                Persona.org_id == org_id,
                Persona.account_id.is_(None),
                Persona.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()


async def account_persona(db: AsyncSession, account_id: uuid.UUID) -> Persona | None:
    """An account's own persona, if it has one."""
    return (
        await db.execute(
            select(Persona).where(
                Persona.account_id == account_id, Persona.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()


async def resolve(
    db: AsyncSession, org_id: uuid.UUID, account_id: uuid.UUID | None = None
) -> ResolvedPersona:
    """
    The effective persona for an account: the org's direction, with this
    person's own voice layered over it.
    """
    parent = await org_persona(db, org_id)
    child = await account_persona(db, account_id) if account_id else None

    resolved = ResolvedPersona(
        org_persona_id=str(parent.id) if parent else None,
        account_persona_id=str(child.id) if child else None,
    )

    if parent is None and child is None:
        return resolved

    base = parent or child
    resolved.name = base.name
    resolved.bio = base.bio or ""
    resolved.voice = dict(base.voice or {})
    resolved.expertise = list(base.expertise or [])
    resolved.content_pillars = list(base.content_pillars or [])
    resolved.icp = dict(base.icp or {})
    resolved.guardrails = dict(base.guardrails or {})
    resolved.autonomy = dict(base.autonomy or {})

    if child is None or child is base:
        return resolved

    # --- Voice: the person wins outright ---
    if child.name:
        resolved.name = child.name
        resolved.overridden.append("name")
    if child.bio:
        resolved.bio = child.bio
        resolved.overridden.append("bio")
    if child.voice:
        resolved.voice = {**resolved.voice, **child.voice}
        resolved.overridden.append("voice")

    # --- Substance: replace if set, inherit if not ---
    if child.expertise:
        resolved.expertise = list(child.expertise)
        resolved.overridden.append("expertise")
    if child.content_pillars:
        resolved.content_pillars = list(child.content_pillars)
        resolved.overridden.append("content_pillars")
    if child.icp:
        resolved.icp = {**resolved.icp, **child.icp}
        resolved.overridden.append("icp")

    # --- Guardrails: cumulative, never removable ---
    resolved.guardrails = _merge_guardrails(resolved.guardrails, child.guardrails or {})

    # --- Autonomy: the stricter of the two ---
    resolved.autonomy = _strictest_autonomy(resolved.autonomy, child.autonomy or {})

    return resolved


def _merge_guardrails(parent: dict, child: dict) -> dict:
    """
    Union the two, so a person can add prohibitions but never remove one.

    A guardrail an individual can switch off is not a guardrail.
    """
    merged = dict(parent)
    for key, value in child.items():
        existing = merged.get(key)
        if isinstance(existing, list) and isinstance(value, list):
            merged[key] = sorted(set(existing) | set(value))
        elif isinstance(existing, bool) or isinstance(value, bool):
            # True is the restrictive value for a guardrail flag.
            merged[key] = bool(existing) or bool(value)
        else:
            merged[key] = value if existing is None else existing
    return merged


def _strictest_autonomy(parent: dict, child: dict) -> dict:
    """
    Take the least autonomous setting for each action.

    If the org says comments need approval, one account cannot decide
    otherwise — the same principle as merging two sets of caps.
    """
    merged = dict(parent)
    for action, allowed in child.items():
        if action in merged:
            merged[action] = bool(merged[action]) and bool(allowed)
        else:
            merged[action] = bool(allowed)
    return merged


# ----------------------------------------------------------------------
# Prompt construction
# ----------------------------------------------------------------------


def to_prompt(persona: ResolvedPersona) -> str:
    """
    Render a persona as instructions for the copy generator.

    This is what stops five colleagues producing five near-identical comments:
    each account's generation is seeded with a genuinely different voice,
    different expertise and different angle, rather than one prompt with a name
    swapped in.
    """
    if not persona.name and not persona.bio:
        return ""

    parts = []
    if persona.name:
        parts.append(f"You are writing as {persona.name}.")
    if persona.bio:
        parts.append(persona.bio)

    voice = persona.voice or {}
    if voice.get("tone"):
        parts.append(f"Tone: {voice['tone']}.")
    if voice.get("style"):
        parts.append(f"Style: {voice['style']}.")
    if voice.get("never_say"):
        never = ", ".join(f'"{p}"' for p in voice["never_say"])
        parts.append(f"Never use these phrases: {never}.")

    if persona.expertise:
        parts.append(
            "You are credible on: "
            + ", ".join(persona.expertise)
            + ". Do not opine confidently outside that."
        )
    if persona.content_pillars:
        parts.append("Your recurring themes: " + ", ".join(persona.content_pillars) + ".")

    guardrails = persona.guardrails or {}
    if guardrails.get("avoid_topics"):
        parts.append("Never discuss: " + ", ".join(guardrails["avoid_topics"]) + ".")
    if guardrails.get("avoid_competitors"):
        parts.append(
            "Never mention these companies: "
            + ", ".join(guardrails["avoid_competitors"])
            + "."
        )
    if guardrails.get("no_opinions_on"):
        parts.append(
            "Stay neutral on: " + ", ".join(guardrails["no_opinions_on"]) + "."
        )

    return "\n".join(parts)
