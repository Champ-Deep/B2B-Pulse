"""Tests for LinkedIn URL utility functions."""

from app.services.url_utils import (
    extract_linkedin_post_id,
    get_linkedin_profile_type,
    is_linkedin_url,
    normalize_linkedin_url,
)


def test_is_linkedin_url():
    assert is_linkedin_url("https://www.linkedin.com/in/johndoe") is True
    assert is_linkedin_url("https://linkedin.com/company/acme") is True
    assert is_linkedin_url("https://www.instagram.com/user") is False
    assert is_linkedin_url("https://example.com") is False


def test_normalize_linkedin_url():
    assert (
        normalize_linkedin_url("https://www.linkedin.com/in/johndoe?trk=something")
        == "https://www.linkedin.com/in/johndoe"
    )
    assert (
        normalize_linkedin_url("https://linkedin.com/company/acme/")
        == "https://www.linkedin.com/company/acme"
    )


def test_extract_post_id_activity_urn():
    url = "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678"
    assert extract_linkedin_post_id(url) == "urn:li:activity:7123456789012345678"


def test_extract_post_id_posts_pattern():
    url = "https://www.linkedin.com/posts/johndoe_awesome-title-7123456789-abcd"
    result = extract_linkedin_post_id(url)
    assert result == "posts/johndoe_awesome-title-7123456789-abcd"


def test_extract_post_id_feed_update():
    url = "https://www.linkedin.com/feed/update/urn:li:share:123456"
    result = extract_linkedin_post_id(url)
    assert result is not None


def test_extract_post_id_not_a_post():
    # Profile URL (not a post) — should return None
    url = "https://www.linkedin.com/in/johndoe"
    assert extract_linkedin_post_id(url) is None


def test_get_profile_type_personal():
    assert get_linkedin_profile_type("https://www.linkedin.com/in/johndoe") == "personal"


def test_get_profile_type_company():
    assert get_linkedin_profile_type("https://www.linkedin.com/company/acme") == "company"


def test_get_profile_type_unknown():
    assert get_linkedin_profile_type("https://www.linkedin.com/feed/update/123") == "unknown"
