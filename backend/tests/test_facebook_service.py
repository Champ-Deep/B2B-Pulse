"""Tests for Facebook URL utility functions."""

from app.services.url_utils import (
    extract_facebook_post_id,
    get_facebook_page_username,
    is_facebook_url,
    normalize_facebook_url,
)


class TestIsFacebookUrl:
    def test_standard_url(self):
        assert is_facebook_url("https://www.facebook.com/page/posts/123")

    def test_without_www(self):
        assert is_facebook_url("https://facebook.com/page/posts/123")

    def test_fb_short(self):
        assert is_facebook_url("https://fb.com/page")

    def test_mobile(self):
        assert is_facebook_url("https://m.facebook.com/page/posts/123")

    def test_web_subdomain(self):
        assert is_facebook_url("https://web.facebook.com/page/posts/123")

    def test_not_facebook(self):
        assert not is_facebook_url("https://www.linkedin.com/in/user")

    def test_not_facebook_instagram(self):
        assert not is_facebook_url("https://www.instagram.com/user")


class TestNormalizeFacebookUrl:
    def test_standard_post(self):
        result = normalize_facebook_url("https://www.facebook.com/page/posts/123?ref=abc")
        assert result == "https://www.facebook.com/page/posts/123"

    def test_mobile_url(self):
        result = normalize_facebook_url("https://m.facebook.com/page/")
        assert result == "https://www.facebook.com/page"


class TestExtractFacebookPostId:
    def test_posts_pattern(self):
        result = extract_facebook_post_id("https://www.facebook.com/username/posts/1234567890")
        assert result == "post_1234567890"

    def test_permalink_pattern(self):
        result = extract_facebook_post_id("https://www.facebook.com/permalink.php?story_fbid=123&id=456")
        assert result == "456_123"

    def test_permalink_no_id(self):
        result = extract_facebook_post_id("https://www.facebook.com/permalink.php?story_fbid=123")
        assert result == "123"

    def test_photo_pattern(self):
        result = extract_facebook_post_id("https://www.facebook.com/photo/?fbid=123")
        assert result == "photo_123"

    def test_watch_pattern(self):
        result = extract_facebook_post_id("https://www.facebook.com/watch/?v=456")
        assert result == "video_456"

    def test_reel_pattern(self):
        result = extract_facebook_post_id("https://www.facebook.com/reel/789")
        assert result == "reel_789"

    def test_group_posts(self):
        result = extract_facebook_post_id("https://www.facebook.com/groups/123/posts/456")
        assert result == "post_456"

    def test_pfbid_pattern(self):
        result = extract_facebook_post_id("https://www.facebook.com/user/posts/pfbidAbcDef123")
        assert result == "post_pfbidAbcDef123"

    def test_profile_url_returns_none(self):
        result = extract_facebook_post_id("https://www.facebook.com/username")
        assert result is None

    def test_homepage_returns_none(self):
        result = extract_facebook_post_id("https://www.facebook.com/")
        assert result is None


class TestGetFacebookPageUsername:
    def test_page_url(self):
        assert get_facebook_page_username("https://www.facebook.com/pagename") == "pagename"

    def test_page_with_trailing_slash(self):
        assert get_facebook_page_username("https://www.facebook.com/pagename/") == "pagename"

    def test_permalink_returns_none(self):
        assert get_facebook_page_username("https://www.facebook.com/permalink.php?story_fbid=123") is None

    def test_watch_returns_none(self):
        assert get_facebook_page_username("https://www.facebook.com/watch/?v=123") is None

    def test_reel_returns_none(self):
        assert get_facebook_page_username("https://www.facebook.com/reel/123") is None
