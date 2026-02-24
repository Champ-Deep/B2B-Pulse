"""Tests for Instagram URL utility functions."""

from app.services.url_utils import (
    extract_instagram_post_id,
    get_instagram_profile_username,
    is_instagram_url,
    normalize_instagram_url,
)


class TestIsInstagramUrl:
    def test_standard_url(self):
        assert is_instagram_url("https://www.instagram.com/p/ABC123/")

    def test_without_www(self):
        assert is_instagram_url("https://instagram.com/p/ABC123/")

    def test_short_url(self):
        assert is_instagram_url("https://instagr.am/p/ABC123/")

    def test_not_instagram(self):
        assert not is_instagram_url("https://www.linkedin.com/in/user")

    def test_not_instagram_facebook(self):
        assert not is_instagram_url("https://www.facebook.com/page")


class TestNormalizeInstagramUrl:
    def test_standard_post(self):
        result = normalize_instagram_url("https://www.instagram.com/p/ABC123/?utm_source=ig")
        assert result == "https://www.instagram.com/p/ABC123"

    def test_profile_url(self):
        result = normalize_instagram_url("https://instagram.com/username/")
        assert result == "https://www.instagram.com/username"

    def test_reel_url(self):
        result = normalize_instagram_url("https://www.instagram.com/reel/DEF456/")
        assert result == "https://www.instagram.com/reel/DEF456"


class TestExtractInstagramPostId:
    def test_post_url(self):
        assert extract_instagram_post_id("https://www.instagram.com/p/ABC123/") == "ABC123"

    def test_reel_url(self):
        assert extract_instagram_post_id("https://www.instagram.com/reel/DEF456/") == "DEF456"

    def test_tv_url(self):
        assert extract_instagram_post_id("https://www.instagram.com/tv/GHI789/") == "GHI789"

    def test_post_with_query(self):
        assert extract_instagram_post_id("https://www.instagram.com/p/ABC123/?utm=test") == "ABC123"

    def test_alphanumeric_shortcode(self):
        assert extract_instagram_post_id("https://www.instagram.com/p/Cx_dE-fG1h2/") == "Cx_dE-fG1h2"

    def test_profile_url_returns_none(self):
        assert extract_instagram_post_id("https://www.instagram.com/username/") is None

    def test_empty_url(self):
        assert extract_instagram_post_id("https://www.instagram.com/") is None


class TestGetInstagramProfileUsername:
    def test_profile_url(self):
        assert get_instagram_profile_username("https://www.instagram.com/username/") == "username"

    def test_profile_without_trailing_slash(self):
        assert get_instagram_profile_username("https://www.instagram.com/username") == "username"

    def test_post_url_returns_none(self):
        assert get_instagram_profile_username("https://www.instagram.com/p/ABC123/") is None

    def test_reel_url_returns_none(self):
        assert get_instagram_profile_username("https://www.instagram.com/reel/ABC123/") is None

    def test_explore_returns_none(self):
        assert get_instagram_profile_username("https://www.instagram.com/explore/tags/test/") is None
