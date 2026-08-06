"""
Client-fingerprint coherence.

The bug these tests exist to prevent shipped once already. The generator sampled
the device pool, the OS-version pool and the TLS pool independently, so it could
produce a *native LinkedIn iOS app* user-agent claiming iOS 16.6, arriving over
a *desktop macOS Safari 17* TLS handshake, carrying *web* session cookies. Every
one of those three pairs is impossible in a real client, and any one of them is
a single-rule detection. Impersonation that is internally inconsistent is worse
than no impersonation, because it is a signal that only automation produces.

So: coherence is asserted for every profile in the catalogue, on every axis,
rather than spot-checked on one sample.
"""

import pytest

from app.transports import fingerprints as fp_module
from app.transports.fingerprints import (
    PROFILES,
    available_profiles,
    coherence_errors,
    generate_fingerprint,
    is_stale,
    li_track,
)


def _curl_targets():
    """The impersonation targets the installed curl_cffi actually supports."""
    import typing

    from curl_cffi.requests.impersonate import BrowserTypeLiteral

    return set(typing.get_args(BrowserTypeLiteral))


# ---------------------------------------------------------------------------
# The invariant, over the whole catalogue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("profile_key", available_profiles())
def test_every_profile_describes_one_real_client(profile_key):
    fingerprint = generate_fingerprint("account-abc", profile_key=profile_key)
    assert coherence_errors(fingerprint) == []


@pytest.mark.parametrize("profile_key", available_profiles())
def test_every_tls_target_exists_in_the_installed_curl_cffi(profile_key):
    """
    A target curl_cffi doesn't know raises at session construction — which would
    surface as a transport outage rather than as the config error it is.
    """
    fingerprint = generate_fingerprint("account-abc", profile_key=profile_key)
    assert fingerprint["tls_impersonate"] in _curl_targets()


def test_no_profile_claims_the_native_linkedin_app():
    """
    The transport authenticates with li_at/JSESSIONID web cookies against
    www.linkedin.com and sends a browser referer. A native-app user-agent
    contradicts all of that, and curl_cffi cannot reproduce OkHttp's or
    NSURLSession's TLS anyway.
    """
    for profile in PROFILES:
        assert "com.linkedin" not in profile.user_agent
        assert not profile.user_agent.startswith("LinkedIn/")


def test_ios_profiles_use_ios_tls_and_a_matching_ios_version():
    """The specific defect: Safari 17 TLS on a device claiming iOS 16.6."""
    ios = [p for p in PROFILES if p.os_name == "ios"]
    assert ios, "the catalogue should still cover mobile Safari"

    for profile in ios:
        assert profile.impersonate.endswith("_ios"), profile.key
        assert profile.browser_version.split(".")[0] == profile.os_version.split(".")[0]


def test_android_profiles_use_android_tls():
    """Desktop Chrome's handshake under an Android user-agent was the other half."""
    android = [p for p in PROFILES if p.os_name == "android"]
    assert android

    for profile in android:
        assert profile.impersonate.endswith("_android"), profile.key


def test_desktop_profiles_never_use_a_mobile_tls_target():
    for profile in PROFILES:
        if profile.is_mobile:
            continue
        assert not profile.impersonate.endswith(("_ios", "_android")), profile.key


def test_client_hints_follow_the_browser_family():
    """Safari sends no UA client hints; inventing them is itself a tell."""
    for profile in PROFILES:
        if profile.browser == "chrome":
            assert profile.client_hints["sec-ch-ua"]
            assert profile.client_hints["sec-ch-ua-mobile"] == (
                "?1" if profile.is_mobile else "?0"
            )
        else:
            assert profile.client_hints == {}


# ---------------------------------------------------------------------------
# The detector itself has to work
# ---------------------------------------------------------------------------


def test_the_coherence_check_catches_the_original_defect():
    """
    A regression guard on the guard: if ``coherence_errors`` stopped detecting
    anything, every test above would pass vacuously.
    """
    broken = generate_fingerprint("account-abc", profile_key="safari18_0-ios")
    broken["user_agent"] = "LinkedIn/9.28.0 (iOS 16.6; iPhone14,3) com.linkedin.LinkedIn"

    problems = coherence_errors(broken)
    assert any("native LinkedIn app" in p for p in problems)


def test_the_coherence_check_catches_a_swapped_tls_target():
    fingerprint = generate_fingerprint("account-abc", profile_key="safari18_0-ios")
    fingerprint["tls_impersonate"] = "chrome120"
    assert coherence_errors(fingerprint)


def test_the_coherence_check_catches_a_desktop_viewport_on_a_phone():
    fingerprint = generate_fingerprint("account-abc", profile_key="safari18_0-ios")
    fingerprint["display_width"] = 1920
    assert any("desktop viewport" in p for p in coherence_errors(fingerprint))


# ---------------------------------------------------------------------------
# Stability and spread
# ---------------------------------------------------------------------------


def test_a_fingerprint_is_stable_for_an_account():
    assert generate_fingerprint("account-abc") == generate_fingerprint("account-abc")


def test_generated_fingerprints_are_coherent_without_pinning_a_profile():
    for i in range(200):
        assert coherence_errors(generate_fingerprint(f"account-{i}")) == []


def test_colleagues_do_not_all_land_on_one_profile():
    """
    Five accounts in one org sharing a single client identity is a correlation
    signal in its own right — the cluster problem, at the transport layer.
    """
    profiles = {generate_fingerprint(f"account-{i}")["profile"] for i in range(20)}
    assert len(profiles) > 1


def test_an_unknown_profile_key_is_refused_loudly():
    with pytest.raises(ValueError):
        generate_fingerprint("account-abc", profile_key="nokia3310")


# ---------------------------------------------------------------------------
# x-li-track
# ---------------------------------------------------------------------------


def test_li_track_identifies_the_web_client():
    """
    ``osName`` names the client, not the operating system: every linkedin.com
    page load sends "web". The previous version sent "ios"/"android", which
    contradicted the browser user-agent next to it in the same request.
    """
    payload = li_track(generate_fingerprint("account-abc"))
    assert payload["osName"] == "web"
    assert payload["mpName"] == "voyager-web"


def test_li_track_reports_a_timezone_the_locale_could_be_in():
    """
    Everyone reporting UTC both contradicts an en_AU locale and is a cluster
    tell in its own right: five colleagues in one org with one timezone.
    """
    payload = li_track(generate_fingerprint("account-abc"))
    assert isinstance(payload["timezoneOffset"], int)

    offsets = {
        li_track(generate_fingerprint(f"account-{i}"))["timezoneOffset"]
        for i in range(20)
    }
    assert len(offsets) > 1


def test_a_timezone_outside_the_locale_is_incoherent():
    fingerprint = generate_fingerprint("account-abc")
    fingerprint["locale"] = "en_AU"
    fingerprint["timezone_offset"] = -480  # Australia, on Pacific time
    assert any("timezone offset" in p for p in coherence_errors(fingerprint))


def test_li_track_form_factor_matches_the_device():
    phone = li_track(generate_fingerprint("a", profile_key="safari18_0-ios"))
    desk = li_track(generate_fingerprint("a", profile_key="chrome131-win"))
    assert phone["deviceFormFactor"] == "PHONE"
    assert desk["deviceFormFactor"] == "DESKTOP"


# ---------------------------------------------------------------------------
# Migration off the incoherent fingerprints
# ---------------------------------------------------------------------------


def test_a_v1_fingerprint_is_treated_as_stale():
    """
    Accounts connected before the fix carry the incoherent blob. Device
    consistency is normally sacrosanct, but a consistent *and detectable*
    identity is the worse of the two options.
    """
    v1 = {
        "platform": "ios",
        "device_model": "iPhone14,3",
        "os_version": "16.6",
        "app_version": "9.28.0",
        "user_agent": "LinkedIn/9.28.0 (iOS 16.6; iPhone14,3) com.linkedin.LinkedIn",
        "device_id": "abc",
        "tls_impersonate": "safari17_0",
        "locale": "en_US",
    }
    assert is_stale(v1)


def test_a_current_fingerprint_is_not_stale():
    assert not is_stale(generate_fingerprint("account-abc"))


def test_a_missing_fingerprint_is_stale():
    assert is_stale(None)
    assert is_stale({})


def test_a_fingerprint_naming_a_retired_profile_is_stale():
    fingerprint = generate_fingerprint("account-abc")
    fingerprint["profile"] = "chrome42-webos"
    assert is_stale(fingerprint)


def test_regeneration_is_deterministic():
    """Replacing a stale fingerprint must not introduce per-restart drift."""
    assert generate_fingerprint("account-abc") == generate_fingerprint("account-abc")
    assert not is_stale(generate_fingerprint("account-abc"))


# ---------------------------------------------------------------------------
# What the session actually puts on the wire
# ---------------------------------------------------------------------------


def _build_session(monkeypatch, profile_key):
    """Capture the kwargs the transport hands to curl_cffi, without a network."""
    from curl_cffi import requests as cffi_requests

    from app.transports.mobile import MobileAPITransport

    captured = {}
    monkeypatch.setattr(
        cffi_requests, "Session", lambda **kwargs: captured.update(kwargs)
    )

    class _Account:
        id = "account-abc"
        auth_blob = {"li_at": "x", "jsessionid": "ajax:1"}
        proxy = None
        device_fingerprint = generate_fingerprint("account-abc", profile_key=profile_key)

    MobileAPITransport().build_session(_Account())
    return captured


def test_the_session_headers_agree_with_the_tls_profile(monkeypatch):
    """
    The end-to-end property. libcurl-impersonate supplies a header set matching
    its TLS target, and our explicit headers override it — which is how the
    mismatch got onto the wire in the first place. So assert on the headers the
    transport actually builds, not just on the fingerprint dict.
    """
    captured = _build_session(monkeypatch, "safari18_0-ios")
    headers = captured["headers"]

    assert captured["impersonate"] == "safari18_0_ios"
    assert "iPhone OS 18_0" in headers["user-agent"]
    assert "Version/18.0" in headers["user-agent"]
    # Safari sends no UA client hints, so none may appear here.
    assert not any(h.startswith("sec-ch-ua") for h in headers)
    assert '"osName":"web"' in headers["x-li-track"]


def test_a_chrome_session_carries_matching_client_hints(monkeypatch):
    captured = _build_session(monkeypatch, "chrome131-win")
    headers = captured["headers"]

    assert captured["impersonate"] == "chrome131"
    assert "Chrome/131.0.0.0" in headers["user-agent"]
    assert headers["sec-ch-ua-mobile"] == "?0"
    assert headers["sec-ch-ua-platform"] == '"Windows"'
    assert '"131"' in headers["sec-ch-ua"]


def test_a_corrupt_fingerprint_is_replaced_rather_than_used(monkeypatch):
    """
    The transport must never fall back to "some browser" when the stored blob
    is missing a TLS target — that is how a mismatched handshake reaches the
    wire silently. Regenerating costs one deterministic replacement; guessing
    costs an unexplained restriction weeks later.
    """
    from curl_cffi import requests as cffi_requests

    from app.transports.mobile import MobileAPITransport

    captured = {}
    monkeypatch.setattr(
        cffi_requests, "Session", lambda **kwargs: captured.update(kwargs)
    )

    corrupt = generate_fingerprint("account-abc")
    corrupt.pop("tls_impersonate")
    assert is_stale(corrupt)

    class _Account:
        id = "account-abc"
        auth_blob = {"li_at": "x", "jsessionid": "ajax:1"}
        proxy = None
        device_fingerprint = corrupt

    MobileAPITransport().build_session(_Account())

    assert captured["impersonate"] in {p.impersonate for p in PROFILES}
    # And the replacement is the account's own stable identity, not a random one.
    assert captured["impersonate"] == generate_fingerprint("account-abc")["tls_impersonate"]
