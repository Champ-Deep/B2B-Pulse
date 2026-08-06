"""
Per-account device fingerprint generation.

The mobile transport presents a stable, realistic client identity per connected
account. Stability matters: LinkedIn ties trust to a consistent device, so the
same account must present the same fingerprint every time. We derive it
deterministically from the account id so it survives restarts even before it is
persisted to ``IntegrationAccount.device_fingerprint``.

Why these are *browser* profiles and not native-app ones
-------------------------------------------------------
An earlier version of this module advertised the native LinkedIn mobile app
(``LinkedIn/9.28.0 (iOS 16.6; iPhone14,3) com.linkedin.LinkedIn``). That was
worse than no impersonation at all, because nothing else about the transport is
a native app:

* it authenticates with ``li_at`` + ``JSESSIONID`` **web cookies**, which the
  native app does not use — it carries an OAuth-style bearer token;
* it sends ``referer: https://www.linkedin.com/feed/``, which no native app
  sends;
* it talks to ``www.linkedin.com/voyager/api``, the web origin;
* and ``curl_cffi`` can only impersonate the TLS stacks of *browsers*. The
  native Android app uses OkHttp/Conscrypt and the iOS app uses NSURLSession —
  neither has a curl_cffi profile, so a native-app user-agent was necessarily
  arriving over Chrome's or Safari's TLS handshake.

A JA3/JA4 hash that says "desktop Chrome" underneath a user-agent that says
"iPhone LinkedIn app" is a one-line detection rule. So the honest identity —
and the coherent one — is the one the credentials actually describe: a person
signed into linkedin.com in a browser.

Coherence is structural, not incidental
---------------------------------------
The old bug was possible because the device pool, the OS-version pool and the
TLS pool were sampled *independently*: ``rng.choice(_IOS_DEVICES)`` could return
iOS 16.6 while ``rng.choice(_TLS_IOS)`` returned a Safari 17 handshake, which no
real device can present. Here a profile is one indivisible record — TLS target,
user-agent, client hints, platform and version are chosen together or not at
all, and :func:`coherence_errors` asserts the invariant in tests.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Optional

# Bump when the profile catalogue changes in a way that makes previously
# persisted fingerprints wrong. Fingerprints stamped with an older version are
# regenerated on load rather than trusted.
FINGERPRINT_VERSION = 2


@dataclass(frozen=True)
class BrowserProfile:
    """
    One coherent browser identity.

    Every field describes the *same* real client. ``impersonate`` must name a
    target that exists in the installed curl_cffi, and ``user_agent`` must be
    the string that browser genuinely sends — libcurl-impersonate would supply a
    matching one itself, but we set it explicitly so it is visible, testable and
    identical whether or not curl_cffi's default headers are in play.
    """

    key: str
    impersonate: str          # curl_cffi target
    browser: str              # "chrome" | "safari"
    browser_version: str
    os_name: str              # "windows" | "macos" | "ios" | "android"
    os_version: str
    device_model: str
    is_mobile: bool
    user_agent: str
    # Chromium-only. Safari sends no UA client hints at all, and inventing them
    # would itself be a tell.
    client_hints: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# The catalogue.
#
# Small and deliberately boring: every entry is a configuration a large number
# of real people are browsing LinkedIn with right now. Rare-but-real is still a
# fingerprint, and a rare one narrows the crowd we are hiding in.
# ---------------------------------------------------------------------------

_CHROMIUM_FETCH_PLATFORM = {
    "windows": '"Windows"',
    "macos": '"macOS"',
    "android": '"Android"',
}


def _chrome(key, impersonate, version, os_name, os_version, model, is_mobile, ua, brands):
    return BrowserProfile(
        key=key,
        impersonate=impersonate,
        browser="chrome",
        browser_version=version,
        os_name=os_name,
        os_version=os_version,
        device_model=model,
        is_mobile=is_mobile,
        user_agent=ua,
        client_hints={
            "sec-ch-ua": brands,
            "sec-ch-ua-mobile": "?1" if is_mobile else "?0",
            "sec-ch-ua-platform": _CHROMIUM_FETCH_PLATFORM[os_name],
        },
    )


def _safari(key, impersonate, version, os_name, os_version, model, is_mobile, ua):
    return BrowserProfile(
        key=key,
        impersonate=impersonate,
        browser="safari",
        browser_version=version,
        os_name=os_name,
        os_version=os_version,
        device_model=model,
        is_mobile=is_mobile,
        user_agent=ua,
    )


PROFILES: tuple[BrowserProfile, ...] = (
    # -- Desktop Chrome ----------------------------------------------------
    _chrome(
        "chrome131-win", "chrome131", "131", "windows", "10", "Windows PC", False,
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    ),
    _chrome(
        "chrome131-mac", "chrome131", "131", "macos", "10_15_7", "Mac", False,
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    ),
    _chrome(
        "chrome124-win", "chrome124", "124", "windows", "10", "Windows PC", False,
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    ),
    _chrome(
        "chrome124-mac", "chrome124", "124", "macos", "10_15_7", "Mac", False,
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    ),
    # -- Desktop Safari ----------------------------------------------------
    _safari(
        "safari17-mac", "safari17_0", "17.0", "macos", "10_15_7", "Mac", False,
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    ),
    _safari(
        "safari18-mac", "safari18_0", "18.0", "macos", "10_15_7", "Mac", False,
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    ),
    # -- Mobile Safari (iOS) ----------------------------------------------
    # Note the pairing: safari17_2_ios goes with iOS 17.2, never 16.6.
    _safari(
        "safari17_2-ios", "safari17_2_ios", "17.2", "ios", "17.2", "iPhone", True,
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 "
        "Safari/604.1",
    ),
    _safari(
        "safari18_0-ios", "safari18_0_ios", "18.0", "ios", "18.0", "iPhone", True,
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 "
        "Safari/604.1",
    ),
    _safari(
        "safari18_4-ios", "safari18_4_ios", "18.4", "ios", "18.4", "iPhone", True,
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Mobile/15E148 "
        "Safari/604.1",
    ),
    # -- Mobile Chrome (Android) ------------------------------------------
    _chrome(
        "chrome131-android", "chrome131_android", "131", "android", "15", "Pixel 8", True,
        "Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
        '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    ),
)

_BY_KEY = {p.key: p for p in PROFILES}

# LinkedIn's web client version, sent as ``clientVersion`` in ``x-li-track``.
# The web app, not the mobile app — see the module docstring.
LINKEDIN_WEB_CLIENT_VERSION = "1.13.35832"
LINKEDIN_WEB_MP_VERSION = "0.13.11072"

# Locale paired with the timezone offsets (in minutes, as browsers report them)
# that a person in that locale plausibly sits in. Reporting UTC for everybody
# both contradicts an ``en_AU`` locale and hands over a correlation signal:
# five colleagues in one org all claiming the same offset is a cluster tell.
_LOCALES = {
    "en_US": [-300, -360, -420, -480],   # Eastern .. Pacific
    "en_GB": [0, 60],                    # GMT / BST
    "en_CA": [-300, -420],               # Toronto / Vancouver
    "en_AU": [600, 660],                 # AEST / AEDT
}


def _seeded_rng(seed: str) -> random.Random:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def generate_fingerprint(account_id: str, profile_key: Optional[str] = None) -> dict:
    """
    Deterministically generate a client fingerprint for an account.

    Same ``account_id`` -> same fingerprint, so an account keeps one identity
    across restarts. Pass ``profile_key`` to pin a specific profile.
    """
    rng = _seeded_rng(account_id)

    if profile_key is not None:
        try:
            profile = _BY_KEY[profile_key]
        except KeyError:
            raise ValueError(
                f"unknown profile {profile_key!r}; "
                f"expected one of {sorted(_BY_KEY)}"
            ) from None
    else:
        profile = rng.choice(PROFILES)

    # Stable synthetic device id (UUID-shaped) derived from the account.
    raw = hashlib.sha256(f"{account_id}:device".encode()).hexdigest()
    device_id = "-".join([raw[0:8], raw[8:12], raw[12:16], raw[16:20], raw[20:32]])

    # Viewport, chosen from what this class of device actually reports. A
    # desktop that claims a phone's viewport is the same category of mistake the
    # module docstring is about.
    if profile.is_mobile:
        width, height, density = rng.choice(
            [(390, 844, 3.0), (393, 852, 3.0), (430, 932, 3.0), (412, 915, 2.625)]
        )
        form_factor = "PHONE"
    else:
        width, height, density = rng.choice(
            [(1512, 858, 2.0), (1920, 1080, 1.0), (1440, 900, 2.0), (1366, 768, 1.0)]
        )
        form_factor = "DESKTOP"

    locale = rng.choice(sorted(_LOCALES))
    timezone_offset = rng.choice(_LOCALES[locale])

    return {
        "version": FINGERPRINT_VERSION,
        "profile": profile.key,
        # Identity
        "browser": profile.browser,
        "browser_version": profile.browser_version,
        "os_name": profile.os_name,
        "os_version": profile.os_version,
        "device_model": profile.device_model,
        "is_mobile": profile.is_mobile,
        "form_factor": form_factor,
        # Wire-level
        "user_agent": profile.user_agent,
        "tls_impersonate": profile.impersonate,
        "client_hints": dict(profile.client_hints),
        "device_id": device_id,
        "locale": locale,
        "accept_language": locale.replace("_", "-"),
        "timezone_offset": timezone_offset,
        "display_density": density,
        "display_width": width,
        "display_height": height,
        # LinkedIn web client identity for x-li-track.
        "app_version": LINKEDIN_WEB_CLIENT_VERSION,
        "mp_version": LINKEDIN_WEB_MP_VERSION,
        # Retained for backwards compatibility with callers that read
        # ``platform``; it now names the OS family rather than "android"/"ios".
        "platform": profile.os_name,
    }


def li_track(fingerprint: dict) -> dict:
    """
    The ``x-li-track`` payload for this fingerprint.

    A browser session reports ``osName: "web"`` — the field names the *client*,
    not the operating system, and every linkedin.com page load sends "web"
    regardless of whether it is running on Windows or on an iPhone. Sending
    ``"ios"`` here, as the previous version did, contradicted every other header
    in the request.
    """
    return {
        "clientVersion": fingerprint.get("app_version", LINKEDIN_WEB_CLIENT_VERSION),
        "mpVersion": fingerprint.get("mp_version", LINKEDIN_WEB_MP_VERSION),
        "osName": "web",
        "timezoneOffset": fingerprint.get("timezone_offset", 0),
        "deviceFormFactor": fingerprint.get("form_factor", "DESKTOP"),
        "mpName": "voyager-web",
        "displayDensity": fingerprint.get("display_density", 1.0),
        "displayWidth": fingerprint.get("display_width", 1920),
        "displayHeight": fingerprint.get("display_height", 1080),
    }


def is_stale(fingerprint: Optional[dict]) -> bool:
    """
    Should this persisted fingerprint be regenerated?

    True for anything stamped with an older catalogue version, or naming a
    profile we no longer ship. Serving a stale fingerprint is not a cosmetic
    problem — the v1 blobs are precisely the incoherent ones.
    """
    if not fingerprint:
        return True
    if fingerprint.get("version") != FINGERPRINT_VERSION:
        return True
    if fingerprint.get("profile") not in _BY_KEY:
        return True
    # A blob missing any field the session builder needs is treated as stale
    # rather than allowed to raise mid-request: a corrupt fingerprint should
    # cost one regeneration, not a transport outage.
    return not all(
        fingerprint.get(key)
        for key in ("user_agent", "tls_impersonate", "device_id", "app_version")
    )


def coherence_errors(fingerprint: dict) -> list[str]:
    """
    Everything about this fingerprint that no real client could produce.

    Empty list means coherent. Used by the tests to hold the invariant that
    made the previous version dangerous — that the user-agent, the TLS
    handshake and the claimed OS all describe one machine.
    """
    problems: list[str] = []
    profile = _BY_KEY.get(fingerprint.get("profile", ""))
    if profile is None:
        return [f"unknown profile {fingerprint.get('profile')!r}"]

    ua = fingerprint["user_agent"]

    if fingerprint["tls_impersonate"] != profile.impersonate:
        problems.append("TLS target does not match the profile")

    # The native-app tell that started all this.
    if "com.linkedin" in ua or ua.startswith("LinkedIn/"):
        problems.append(
            "user-agent claims the native LinkedIn app, which does not "
            "authenticate with web cookies and has no curl_cffi TLS profile"
        )

    # OS family must agree between the user-agent and the TLS target.
    tls = profile.impersonate
    if profile.os_name == "ios":
        if not tls.endswith("_ios"):
            problems.append(f"{tls} is not an iOS TLS profile")
        # Safari 17.2 shipped with iOS 17.2; a device on an older iOS cannot
        # present it. Require the major versions to agree.
        if profile.browser_version.split(".")[0] != profile.os_version.split(".")[0]:
            problems.append(
                f"Safari {profile.browser_version} cannot run on iOS {profile.os_version}"
            )
        if "iPhone" not in ua or "Mobile/" not in ua:
            problems.append("iOS profile without a mobile Safari user-agent")
    elif profile.os_name == "android":
        if not tls.endswith("_android"):
            problems.append(f"{tls} is not an Android TLS profile")
        if "Android" not in ua or "Mobile Safari" not in ua:
            problems.append("Android profile without a mobile Chrome user-agent")
    else:
        if tls.endswith("_ios") or tls.endswith("_android"):
            problems.append(f"{tls} is a mobile TLS profile on a desktop profile")
        if "Mobile" in ua:
            problems.append("desktop profile with a mobile user-agent")

    # Browser family must agree between the user-agent and the TLS target.
    if profile.browser == "chrome":
        if not tls.startswith("chrome"):
            problems.append(f"{tls} is not a Chrome TLS profile")
        if f"Chrome/{profile.browser_version}." not in ua:
            problems.append("user-agent does not carry the profile's Chrome version")
        if not fingerprint.get("client_hints"):
            problems.append("Chromium client without UA client hints")
    elif profile.browser == "safari":
        if not tls.startswith("safari"):
            problems.append(f"{tls} is not a Safari TLS profile")
        if f"Version/{profile.browser_version} " not in ua:
            problems.append("user-agent does not carry the profile's Safari version")
        if fingerprint.get("client_hints"):
            problems.append("Safari does not send UA client hints")

    # Viewport must match the form factor.
    if profile.is_mobile and fingerprint["display_width"] > 600:
        problems.append("mobile profile reporting a desktop viewport")
    if not profile.is_mobile and fingerprint["display_width"] < 1000:
        problems.append("desktop profile reporting a mobile viewport")

    # Timezone must be somewhere the claimed locale is actually spoken.
    locale = fingerprint.get("locale")
    if locale not in _LOCALES:
        problems.append(f"unknown locale {locale!r}")
    elif fingerprint.get("timezone_offset") not in _LOCALES[locale]:
        problems.append(
            f"timezone offset {fingerprint.get('timezone_offset')} does not "
            f"correspond to a {locale} region"
        )

    return problems


def available_profiles() -> list[str]:
    """Profile keys, for operators and diagnostics."""
    return [p.key for p in PROFILES]
