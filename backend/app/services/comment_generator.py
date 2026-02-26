import logging

import httpx

from app.config import LLM_TIMEOUT, settings

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_AVOID_PHRASES = [
    "thanks for sharing",
    "great insights",
    "this is very insightful",
    "couldn't agree more",
    "spot on",
    "well said",
    "great post",
    "love this",
    "so true",
    "this resonates",
    "beautifully written",
    "nailed it",
    "this is gold",
    "food for thought",
    "game changer",
    "totally agree",
    "this is a must-read",
    "absolutely brilliant",
    "—",
    "–",
]

PLATFORM_TONE = {
    "linkedin": (
        "You are a social media engagement assistant. Your job is to write short, authentic comments on LinkedIn posts "
        "that sound like they come from a real professional.\n\n"
        "PLATFORM STYLE: Professional but conversational. No hashtags. Emojis sparingly (max 1-2). "
        "Use industry vocabulary when relevant. Sound like a colleague, not a marketer."
    ),
    "instagram": (
        "You are a social media engagement assistant. Your job is to write short, authentic comments on Instagram posts "
        "that sound like they come from a real person.\n\n"
        "PLATFORM STYLE: Casual, warm, and brief. 1-2 short sentences max. Use 1-2 emojis naturally. "
        "Can use slang and informal language. Sound like a friend commenting, not a brand. "
        "Never use hashtags in comments."
    ),
    "facebook": (
        "You are a social media engagement assistant. Your job is to write short, authentic comments on Facebook posts "
        "that sound like they come from a real person.\n\n"
        "PLATFORM STYLE: Friendly and conversational. 1-3 sentences. Can be slightly longer than Instagram. "
        "Use emojis occasionally (0-2). Sound like a friendly acquaintance. "
        "Can ask follow-up questions or share a brief related thought."
    ),
}

GENERATION_SYSTEM_PROMPT = """{platform_intro}

RULES:
1. Write 1-3 short comment variants (1-3 sentences each, prefer one-liners)
2. Sound conversational and human, like texting a colleague, not writing an essay
3. Reference specific details from the post content
4. Add value: share a quick opinion, ask a question, or relate it to experience
5. NEVER use these AI-tell phrases: {avoid_phrases}
6. NEVER be generic. Every comment must reference something specific from the post.
7. Match the user's tone and style profile below.
8. NEVER use em dashes or en dashes. Use commas, periods, or semicolons instead.

{custom_rules}

USER PROFILE:
{user_profile}

{tone_instructions}

Respond with a JSON object: {{"comments": ["comment1", "comment2", "comment3"]}}
"""

REVIEW_SYSTEM_PROMPT = """You are a brand safety reviewer for social media comments. Check the proposed comment against these rules:

1. Does it sound natural and human (not AI-generated)?
2. Does it avoid ALL of these banned phrases: {avoid_phrases}
3. Does it avoid sensitive topics (politics, religion)?
4. Is it professional and brand-safe?
5. Is it under 3 sentences?

If the comment passes ALL checks, respond: {{"passed": true, "notes": null}}
If it fails any check, respond: {{"passed": false, "notes": "explanation", "rewrite": "improved version"}}
"""


async def _call_openrouter(model: str, messages: list[dict]) -> dict:
    """Make a call to OpenRouter API."""
    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        response = await client.post(
            OPENROUTER_URL,
            json={
                "model": model,
                "messages": messages,
                "temperature": 0.8,
                "max_tokens": 500,
            },
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://b2bpulse.app",
                "X-Title": "B2B Pulse",
            },
        )
        response.raise_for_status()
        return response.json()


async def generate_comments(
    post_content: str,
    user_profile: str = "",
    tone_settings: dict | None = None,
    avoid_phrases: list[str] | None = None,
    page_tags: list[str] | None = None,
    platform: str = "linkedin",
) -> dict:
    """Generate comment variants using Claude Sonnet via OpenRouter."""
    phrases = avoid_phrases or DEFAULT_AVOID_PHRASES
    tone_instructions = ""
    if tone_settings:
        tone_instructions = f"TONE PREFERENCES: {tone_settings}"

    # Extract custom rules from tone_settings
    custom_rules_text = ""
    if tone_settings and tone_settings.get("custom_rules"):
        rules = tone_settings["custom_rules"]
        if isinstance(rules, list) and rules:
            numbered = [f"- {r}" for r in rules]
            custom_rules_text = "CUSTOM WRITING RULES:\n" + "\n".join(numbered)

    # Extract example comments for few-shot learning
    if tone_settings and tone_settings.get("example_comments"):
        examples = tone_settings["example_comments"]
        if examples.strip():
            custom_rules_text += f"\n\nEXAMPLE COMMENTS (match this style):\n{examples}"

    platform_key = platform.lower()
    # Map 'meta' to specific sub-platform or default to facebook
    if platform_key == "meta":
        platform_key = "facebook"
    platform_intro = PLATFORM_TONE.get(platform_key, PLATFORM_TONE["linkedin"])

    system_prompt = GENERATION_SYSTEM_PROMPT.format(
        platform_intro=platform_intro,
        avoid_phrases=", ".join(f'"{p}"' for p in phrases),
        user_profile=user_profile or "No profile provided. Use a friendly professional tone.",
        tone_instructions=tone_instructions,
        custom_rules=custom_rules_text,
    )

    platform_label = {"linkedin": "LinkedIn", "instagram": "Instagram", "facebook": "Facebook"}.get(
        platform_key, "social media"
    )
    user_message = f"Write comments for this {platform_label} post:\n\n{post_content}"
    if page_tags:
        user_message += f"\n\nRelationship context: This page is tagged as: {', '.join(page_tags)}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    result = await _call_openrouter(settings.openrouter_generation_model, messages)
    content = result["choices"][0]["message"]["content"]

    # Parse JSON response (strip markdown code fences if present)
    import json
    import re as _re

    cleaned = _re.sub(r"^```(?:json)?\s*", "", content.strip())
    cleaned = _re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        comments = parsed.get("comments", [cleaned])
    except json.JSONDecodeError:
        comments = [cleaned.strip()]

    return {
        "comments": comments,
        "model": settings.openrouter_generation_model,
        "raw_response": result,
    }


async def review_comment(
    comment: str,
    avoid_phrases: list[str] | None = None,
) -> dict:
    """Review a comment for brand safety using Claude Haiku via OpenRouter."""
    phrases = avoid_phrases or DEFAULT_AVOID_PHRASES

    system_prompt = REVIEW_SYSTEM_PROMPT.format(
        avoid_phrases=", ".join(f'"{p}"' for p in phrases),
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Review this comment:\n\n{comment}"},
    ]

    result = await _call_openrouter(settings.openrouter_review_model, messages)
    content = result["choices"][0]["message"]["content"]

    import json

    try:
        parsed = json.loads(content)
        return {
            "passed": parsed.get("passed", True),
            "notes": parsed.get("notes"),
            "rewrite": parsed.get("rewrite"),
            "model": settings.openrouter_review_model,
            "raw_response": result,
        }
    except json.JSONDecodeError:
        return {
            "passed": True,
            "notes": None,
            "rewrite": None,
            "model": settings.openrouter_review_model,
            "raw_response": result,
        }


async def generate_and_review_comment(
    post_content: str,
    user_profile: str = "",
    tone_settings: dict | None = None,
    avoid_phrases: list[str] | None = None,
    page_tags: list[str] | None = None,
    platform: str = "linkedin",
) -> dict:
    """Generate comments, review the best one, and return the final result."""
    # Step 1: Generate
    gen_result = await generate_comments(
        post_content=post_content,
        user_profile=user_profile,
        tone_settings=tone_settings,
        avoid_phrases=avoid_phrases,
        page_tags=page_tags,
        platform=platform,
    )

    if not gen_result["comments"]:
        raise ValueError("LLM returned no comments")

    best_comment = gen_result["comments"][0]

    # Step 2: Review
    review_result = await review_comment(best_comment, avoid_phrases)

    # If review failed and a rewrite was provided, use the rewrite
    final_comment = best_comment
    if not review_result["passed"] and review_result.get("rewrite"):
        final_comment = review_result["rewrite"]

    return {
        "comment": final_comment,
        "all_variants": gen_result["comments"],
        "review_passed": review_result["passed"],
        "review_notes": review_result.get("notes"),
        "llm_data": {
            "generation": {
                "model": gen_result["model"],
                "comments": gen_result["comments"],
            },
            "review": {
                "model": review_result["model"],
                "passed": review_result["passed"],
                "notes": review_result.get("notes"),
            },
        },
    }
