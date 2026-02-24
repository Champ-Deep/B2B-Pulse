"""Shared URL detection, normalization, and extraction utilities for all platforms."""

import re
from urllib.parse import parse_qs, urlparse

from app.models.integration import Platform
from app.models.tracked_page import PageType

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def is_linkedin_url(url: str) -> bool:
    """Check if a URL is a LinkedIn URL."""
    parsed = urlparse(url)
    return "linkedin.com" in parsed.netloc.lower()


def is_instagram_url(url: str) -> bool:
    """Check if a URL is an Instagram URL."""
    parsed = urlparse(url)
    return any(
        d in parsed.netloc.lower() for d in ["instagram.com", "www.instagram.com", "instagr.am"]
    )


def is_facebook_url(url: str) -> bool:
    """Check if a URL is a Facebook URL."""
    parsed = urlparse(url)
    return any(
        d in parsed.netloc.lower()
        for d in [
            "facebook.com",
            "www.facebook.com",
            "fb.com",
            "m.facebook.com",
            "web.facebook.com",
        ]
    )


def detect_platform(url: str) -> Platform:
    """Detect the social platform from a URL.

    Raises ValueError for unsupported URLs.
    """
    if is_linkedin_url(url):
        return Platform.LINKEDIN
    if is_instagram_url(url) or is_facebook_url(url):
        return Platform.META
    raise ValueError(f"Unsupported platform for URL: {url}")


def detect_page_type(url: str, platform: Platform) -> PageType:
    """Determine the page type from a URL and platform."""
    if platform == Platform.LINKEDIN:
        if "/company/" in url:
            return PageType.COMPANY
        return PageType.PERSONAL
    if platform == Platform.META:
        if "instagram.com" in url:
            return PageType.IG_BUSINESS
        return PageType.FB_PAGE
    return PageType.PERSONAL


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """Ensure URL has a scheme."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    return url


def normalize_linkedin_url(url: str) -> str:
    """Normalize a LinkedIn URL to a canonical form."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    return f"https://www.linkedin.com/{path}"


def normalize_instagram_url(url: str) -> str:
    """Normalize an Instagram URL to a canonical form."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    return f"https://www.instagram.com/{path}"


def normalize_facebook_url(url: str) -> str:
    """Normalize a Facebook URL to a canonical form."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    return f"https://www.facebook.com/{path}"


# ---------------------------------------------------------------------------
# Post ID extraction
# ---------------------------------------------------------------------------


def extract_linkedin_post_id(url: str) -> str | None:
    """Extract the post/activity ID from a LinkedIn post URL.

    Patterns:
      - linkedin.com/feed/update/urn:li:activity:1234567890
      - linkedin.com/posts/username_title-1234567890-abcd
      - linkedin.com/pulse/title-name-1234567890
    """
    parsed = urlparse(url)
    path = parsed.path

    # Activity URN pattern
    urn_match = re.search(r"urn:li:activity:(\d+)", url)
    if urn_match:
        return f"urn:li:activity:{urn_match.group(1)}"

    # /posts/ pattern
    posts_match = re.search(r"/posts/([^/]+)", path)
    if posts_match:
        return f"posts/{posts_match.group(1)}"

    # /feed/update/ pattern
    update_match = re.search(r"/feed/update/([^/?]+)", path)
    if update_match:
        return update_match.group(1)

    return None


def extract_instagram_post_id(url: str) -> str | None:
    """Extract the media shortcode from an Instagram post URL.

    Handles patterns:
      - instagram.com/p/ABC123/
      - instagram.com/reel/ABC123/
      - instagram.com/tv/ABC123/
    """
    parsed = urlparse(url)
    path = parsed.path

    match = re.search(r"/(p|reel|tv)/([A-Za-z0-9_-]+)", path)
    if match:
        return match.group(2)
    return None


def extract_facebook_post_id(url: str) -> str | None:
    """Extract the post ID from a Facebook post URL.

    Handles patterns:
      - facebook.com/username/posts/1234567890
      - facebook.com/permalink.php?story_fbid=123&id=456
      - facebook.com/photo/?fbid=123
      - facebook.com/watch/?v=123
      - facebook.com/reel/123
      - facebook.com/groups/123/posts/456
    """
    parsed = urlparse(url)
    path = parsed.path
    query = parse_qs(parsed.query)

    # /permalink.php?story_fbid=...&id=...
    if "permalink.php" in path:
        story_fbid = query.get("story_fbid", [None])[0]
        post_id = query.get("id", [None])[0]
        if story_fbid:
            return f"{post_id}_{story_fbid}" if post_id else story_fbid

    # /photo/?fbid=...
    if "/photo" in path:
        fbid = query.get("fbid", [None])[0]
        if fbid:
            return f"photo_{fbid}"

    # /watch/?v=...
    if "/watch" in path:
        video_id = query.get("v", [None])[0]
        if video_id:
            return f"video_{video_id}"

    # /reel/123
    reel_match = re.search(r"/reel/(\d+)", path)
    if reel_match:
        return f"reel_{reel_match.group(1)}"

    # /username/posts/123 or /groups/gid/posts/123
    posts_match = re.search(r"/posts/(\d+)", path)
    if posts_match:
        return f"post_{posts_match.group(1)}"

    # /username/posts/pfbid... (new-style alphanumeric IDs)
    pfbid_match = re.search(r"/posts/(pfbid[A-Za-z0-9]+)", path)
    if pfbid_match:
        return f"post_{pfbid_match.group(1)}"

    return None


def extract_post_id(url: str, platform: Platform) -> str | None:
    """Extract a unique post identifier from a URL based on platform."""
    if platform == Platform.LINKEDIN:
        return extract_linkedin_post_id(url)
    elif platform == Platform.META:
        if is_instagram_url(url):
            post_id = extract_instagram_post_id(url)
            return f"ig_{post_id}" if post_id else None
        else:
            return extract_facebook_post_id(url)
    return None


# ---------------------------------------------------------------------------
# Profile / page identification
# ---------------------------------------------------------------------------


def get_linkedin_profile_type(url: str) -> str:
    """Determine if a LinkedIn URL is for a personal profile or company page."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    if "/company/" in path:
        return "company"
    if "/in/" in path:
        return "personal"
    return "unknown"


def get_instagram_profile_username(url: str) -> str | None:
    """Extract the username from an Instagram profile URL."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    # Skip post/reel/tv paths
    if re.match(r"^(p|reel|tv|stories|explore|accounts)/", path):
        return None
    # The first path segment is the username
    parts = path.split("/")
    if parts and parts[0]:
        return parts[0]
    return None


def get_facebook_page_username(url: str) -> str | None:
    """Extract the page username/ID from a Facebook page URL."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    # Skip non-page paths
    if re.match(r"^(permalink\.php|photo|watch|reel|stories|events|marketplace|groups)", path):
        return None
    parts = path.split("/")
    if parts and parts[0]:
        return parts[0]
    return None


def extract_external_id(url: str, platform: Platform) -> str | None:
    """Extract an external identifier for a tracked page from its URL."""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if platform == Platform.LINKEDIN:
        match = re.match(r"(in|company)/([^/]+)", path)
        if match:
            return f"{match.group(1)}/{match.group(2)}"
    if platform == Platform.META:
        parts = path.split("/")
        if parts:
            return parts[0]
    return None
