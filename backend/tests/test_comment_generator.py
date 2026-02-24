"""Tests for the comment generator, including platform-specific tone."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.comment_generator import (
    PLATFORM_TONE,
    generate_and_review_comment,
    generate_comments,
    review_comment,
)


def _mock_openrouter_response(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "model": "test-model",
    }


class TestPlatformTone:
    def test_linkedin_tone_exists(self):
        assert "linkedin" in PLATFORM_TONE
        assert "professional" in PLATFORM_TONE["linkedin"].lower()

    def test_instagram_tone_exists(self):
        assert "instagram" in PLATFORM_TONE
        assert "casual" in PLATFORM_TONE["instagram"].lower()

    def test_facebook_tone_exists(self):
        assert "facebook" in PLATFORM_TONE
        assert "friendly" in PLATFORM_TONE["facebook"].lower()


class TestGenerateComments:
    @pytest.mark.asyncio
    @patch("app.services.comment_generator._call_openrouter")
    async def test_generates_with_linkedin_platform(self, mock_call):
        mock_call.return_value = _mock_openrouter_response(
            json.dumps({"comments": ["Great point about AI trends"]})
        )
        result = await generate_comments("Post about AI", platform="linkedin")
        assert len(result["comments"]) == 1

        # Check that the system prompt included LinkedIn tone
        call_args = mock_call.call_args
        system_msg = call_args[0][1][0]["content"]
        assert "professional" in system_msg.lower()

    @pytest.mark.asyncio
    @patch("app.services.comment_generator._call_openrouter")
    async def test_generates_with_instagram_platform(self, mock_call):
        mock_call.return_value = _mock_openrouter_response(
            json.dumps({"comments": ["Love the vibe!"]})
        )
        result = await generate_comments("Photo of sunset", platform="instagram")
        assert len(result["comments"]) == 1

        call_args = mock_call.call_args
        system_msg = call_args[0][1][0]["content"]
        assert "casual" in system_msg.lower()

    @pytest.mark.asyncio
    @patch("app.services.comment_generator._call_openrouter")
    async def test_generates_with_facebook_platform(self, mock_call):
        mock_call.return_value = _mock_openrouter_response(
            json.dumps({"comments": ["This is really interesting!"]})
        )
        result = await generate_comments("Article about cooking", platform="facebook")
        assert len(result["comments"]) == 1

        call_args = mock_call.call_args
        system_msg = call_args[0][1][0]["content"]
        assert "friendly" in system_msg.lower()

    @pytest.mark.asyncio
    @patch("app.services.comment_generator._call_openrouter")
    async def test_meta_maps_to_facebook(self, mock_call):
        mock_call.return_value = _mock_openrouter_response(
            json.dumps({"comments": ["Nice!"]})
        )
        result = await generate_comments("Post content", platform="meta")
        call_args = mock_call.call_args
        system_msg = call_args[0][1][0]["content"]
        assert "friendly" in system_msg.lower()

    @pytest.mark.asyncio
    @patch("app.services.comment_generator._call_openrouter")
    async def test_user_message_includes_platform_label(self, mock_call):
        mock_call.return_value = _mock_openrouter_response(
            json.dumps({"comments": ["Nice!"]})
        )
        await generate_comments("Post content", platform="instagram")
        call_args = mock_call.call_args
        user_msg = call_args[0][1][1]["content"]
        assert "Instagram" in user_msg

    @pytest.mark.asyncio
    @patch("app.services.comment_generator._call_openrouter")
    async def test_defaults_to_linkedin(self, mock_call):
        mock_call.return_value = _mock_openrouter_response(
            json.dumps({"comments": ["Insightful take"]})
        )
        result = await generate_comments("Post content")
        call_args = mock_call.call_args
        system_msg = call_args[0][1][0]["content"]
        assert "professional" in system_msg.lower()


class TestReviewComment:
    @pytest.mark.asyncio
    @patch("app.services.comment_generator._call_openrouter")
    async def test_passes_review(self, mock_call):
        mock_call.return_value = _mock_openrouter_response(
            json.dumps({"passed": True, "notes": None})
        )
        result = await review_comment("Great analysis of the market trends")
        assert result["passed"] is True

    @pytest.mark.asyncio
    @patch("app.services.comment_generator._call_openrouter")
    async def test_fails_review_with_rewrite(self, mock_call):
        mock_call.return_value = _mock_openrouter_response(
            json.dumps({"passed": False, "notes": "Uses banned phrase", "rewrite": "Better version"})
        )
        result = await review_comment("Thanks for sharing this great insight")
        assert result["passed"] is False
        assert result["rewrite"] == "Better version"


class TestGenerateAndReviewComment:
    @pytest.mark.asyncio
    @patch("app.services.comment_generator._call_openrouter")
    async def test_end_to_end_with_platform(self, mock_call):
        # First call: generation, second call: review
        mock_call.side_effect = [
            _mock_openrouter_response(json.dumps({"comments": ["This is fire!"]})),
            _mock_openrouter_response(json.dumps({"passed": True, "notes": None})),
        ]
        result = await generate_and_review_comment(
            post_content="New reel about travel",
            platform="instagram",
        )
        assert result["comment"] == "This is fire!"
        assert result["review_passed"] is True
