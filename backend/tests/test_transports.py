"""
Transport layer: Voyager primary, browser fallback.

The merge's central mechanical claim is that these two paths compose — mobile
first because it is far less detectable, the existing Playwright automation
underneath so a drifted Voyager endpoint costs throughput rather than the
action. These tests hold that claim up.
"""

import pytest

from app.services.account_service import cookies_to_auth_blob
from app.transports.base import (
    ACTION_METHODS,
    TransportChallenge,
    TransportResult,
    TransportUnavailable,
)
from app.transports.fingerprints import generate_fingerprint
from app.transports.playwright import PlaywrightTransport
from app.transports.router import CompositeTransport


class FakeTransport:
    """A transport that succeeds, fails, or refuses, on command."""

    def __init__(self, name, *, raises=None, success=True):
        self.name = name
        self.raises = raises
        self.success = success
        self.calls = []

    def __getattr__(self, action):
        async def _call(*args, **kwargs):
            self.calls.append((action, args))
            if self.raises:
                raise self.raises
            return TransportResult(success=self.success, action=action, via=self.name)

        return _call


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


async def test_the_mobile_path_is_used_when_it_works():
    mobile = FakeTransport("mobile")
    browser = FakeTransport("playwright")
    composite = CompositeTransport(primary=mobile, fallback=browser)

    result = await composite.like(object(), "urn:li:activity:123")

    assert result.success
    assert result.via == "mobile"
    assert not browser.calls, "fell back when the primary succeeded"


async def test_it_falls_back_to_the_browser_when_voyager_cannot_help():
    """The whole point of the merge: a drifted endpoint costs speed, not the action."""
    mobile = FakeTransport("mobile", raises=TransportUnavailable("endpoint shape changed"))
    browser = FakeTransport("playwright")
    composite = CompositeTransport(primary=mobile, fallback=browser)

    result = await composite.comment(object(), "urn:li:activity:123", "nice work")

    assert result.success
    assert result.via == "playwright"
    assert result.detail["fell_back_from"] == "mobile"
    assert "endpoint shape changed" in result.detail["fallback_reason"]


async def test_a_challenge_also_falls_back():
    mobile = FakeTransport("mobile", raises=TransportChallenge("checkpoint"))
    browser = FakeTransport("playwright")
    composite = CompositeTransport(primary=mobile, fallback=browser)

    result = await composite.like(object(), "urn:li:activity:1")
    assert result.via == "playwright"


async def test_failure_on_both_paths_reports_cleanly():
    """No silent no-ops: the caller learns nothing happened."""
    mobile = FakeTransport("mobile", raises=TransportUnavailable("no"))
    browser = FakeTransport("playwright", raises=TransportUnavailable("also no"))
    composite = CompositeTransport(primary=mobile, fallback=browser)

    result = await composite.connect(object(), "urn:li:fs_profile:abc", "hello")
    assert not result.success
    assert result.error


async def test_a_genuine_rejection_is_not_retried_on_the_browser():
    """A transport that ran and said 'no' is answered; don't try twice."""
    mobile = FakeTransport("mobile", success=False)
    browser = FakeTransport("playwright")
    composite = CompositeTransport(primary=mobile, fallback=browser)

    result = await composite.like(object(), "urn:li:activity:1")
    assert not result.success
    assert not browser.calls


async def test_the_composite_covers_the_whole_action_surface():
    """Guards against a transport gaining an action the router can't route."""
    for action in ACTION_METHODS:
        assert hasattr(CompositeTransport, action), action


# ---------------------------------------------------------------------------
# The Playwright adapter
# ---------------------------------------------------------------------------


class FakeActions:
    """Stands in for app.automation.linkedin_actions."""

    def __init__(self):
        self.calls = []

    async def like_post(self, user_id, post_url):
        self.calls.append(("like_post", user_id, post_url))
        return True

    async def comment_on_post(self, user_id, post_url, text):
        self.calls.append(("comment_on_post", user_id, post_url, text))
        return True

    async def check_session_valid(self, user_id):
        self.calls.append(("check_session_valid", user_id))
        return True

    async def scrape_profile_posts(self, profile_url, cookies=None):
        self.calls.append(("scrape_profile_posts", profile_url))
        return [{"urn": "urn:li:activity:9", "text": "hi"}]


class FakeAccount:
    id = "acc-1"
    user_id = "user-42"


async def test_urns_are_translated_into_urls_for_the_browser():
    """Voyager speaks URNs; Playwright needs somewhere to navigate."""
    actions = FakeActions()
    transport = PlaywrightTransport(actions=actions)

    await transport.like(FakeAccount(), "urn:li:activity:7777")

    _, user_id, url = actions.calls[0]
    assert user_id == "user-42"
    assert url.startswith("https://www.linkedin.com/feed/update/")
    assert "7777" in url


async def test_a_url_is_passed_through_untouched():
    actions = FakeActions()
    transport = PlaywrightTransport(actions=actions)

    await transport.like(FakeAccount(), "https://www.linkedin.com/posts/xyz")
    assert actions.calls[0][2] == "https://www.linkedin.com/posts/xyz"


async def test_the_browser_path_keys_off_the_owning_user():
    """Because that's what the existing cookie storage is keyed on."""
    actions = FakeActions()
    transport = PlaywrightTransport(actions=actions)

    await transport.comment(FakeAccount(), "urn:li:activity:1", "a thought")
    assert actions.calls[0][1] == "user-42"


async def test_a_browser_error_becomes_unavailable_not_a_crash():
    """So the router can report it rather than the request 500-ing."""

    class Broken:
        async def like_post(self, *args):
            raise RuntimeError("chromium died")

    transport = PlaywrightTransport(actions=Broken())
    with pytest.raises(TransportUnavailable):
        await transport.like(FakeAccount(), "urn:li:activity:1")


async def test_unsupported_browser_actions_raise_rather_than_pretend():
    """Silence would look like success to the caller."""
    transport = PlaywrightTransport(actions=FakeActions())
    for call in (
        transport.connect(FakeAccount(), "urn:li:fs_profile:x"),
        transport.send_message(FakeAccount(), "urn:li:fs_profile:x", "hi"),
        transport.fetch_inbox(FakeAccount()),
    ):
        with pytest.raises(TransportUnavailable):
            await call


# ---------------------------------------------------------------------------
# Device identity
# ---------------------------------------------------------------------------


async def test_a_fingerprint_is_stable_for_an_account():
    """LinkedIn ties trust to device consistency, so this must never drift."""
    first = generate_fingerprint("account-abc")
    second = generate_fingerprint("account-abc")
    assert first == second


async def test_different_accounts_get_different_devices():
    """Five colleagues sharing one device fingerprint is a correlation signal."""
    a = generate_fingerprint("account-a")
    b = generate_fingerprint("account-b")
    assert a["device_id"] != b["device_id"]


async def test_a_fingerprint_carries_what_a_session_needs():
    fingerprint = generate_fingerprint("account-abc")
    for key in ("user_agent", "device_id", "app_version", "platform", "tls_impersonate"):
        assert fingerprint.get(key), key


# ---------------------------------------------------------------------------
# Credential translation
# ---------------------------------------------------------------------------


async def test_playwright_cookie_jars_convert_to_voyager_credentials():
    """B2B Pulse stores a cookie list; Voyager wants two named values."""
    jar = [
        {"name": "li_at", "value": "AQEDAT-token"},
        {"name": "JSESSIONID", "value": '"ajax:1234567890"'},
        {"name": "lang", "value": "en"},
    ]
    blob = cookies_to_auth_blob(jar)

    assert blob["li_at"] == "AQEDAT-token"
    # Quotes stripped: Voyager compares the CSRF header to the raw value.
    assert blob["jsessionid"] == "ajax:1234567890"


async def test_a_bare_cookie_string_is_accepted():
    assert cookies_to_auth_blob("AQEDAT-token")["li_at"] == "AQEDAT-token"


async def test_a_dict_of_cookies_is_accepted():
    blob = cookies_to_auth_blob({"li_at": "abc", "JSESSIONID": '"ajax:9"'})
    assert blob == {"li_at": "abc", "jsessionid": "ajax:9"}


async def test_missing_cookies_produce_an_empty_blob_not_a_crash():
    assert cookies_to_auth_blob(None) == {}
    assert cookies_to_auth_blob([]) == {}
