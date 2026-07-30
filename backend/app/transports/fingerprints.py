"""
Per-account device fingerprint generation.

The mobile transport persists a stable, realistic device identity per connected
account (user-agent, device model, OS/app versions, device id, TLS impersonation
profile). Stability matters: LinkedIn ties trust to a consistent device, so the
same account must present the same fingerprint every time. We derive it
deterministically from the account id so it survives restarts even before it is
persisted to ``ConnectedAccount.device_fingerprint``.
"""

from __future__ import annotations

import hashlib
import random
from typing import Optional

# Small, realistic pools. Expanded/rotated carefully in the real implementation.
_ANDROID_DEVICES = [
    ("Pixel 7", "13", "arm64-v8a"),
    ("Pixel 8", "14", "arm64-v8a"),
    ("SM-G991B", "13", "arm64-v8a"),   # Galaxy S21
    ("SM-S911B", "14", "arm64-v8a"),   # Galaxy S23
    ("OnePlus 11", "13", "arm64-v8a"),
]
_IOS_DEVICES = [
    ("iPhone14,3", "16.6"),   # iPhone 13 Pro Max
    ("iPhone15,2", "17.4"),   # iPhone 14 Pro
    ("iPhone16,1", "17.5"),   # iPhone 15 Pro
]
# curl_cffi impersonation targets (TLS/JA3 fingerprints).
_TLS_ANDROID = ["chrome120", "chrome124"]
_TLS_IOS = ["safari17_0", "safari17_2_ios"]

_LINKEDIN_ANDROID_APP = "4.1.900"
_LINKEDIN_IOS_APP = "9.28.0"
_LOCALES = ["en_US", "en_GB", "en_CA", "en_AU"]


def _seeded_rng(seed: str) -> random.Random:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def generate_fingerprint(account_id: str, platform: Optional[str] = None) -> dict:
    """
    Deterministically generate a device fingerprint for an account.

    Same ``account_id`` -> same fingerprint. Pass ``platform`` ("android"/"ios")
    to force one, otherwise it is chosen deterministically.
    """
    rng = _seeded_rng(account_id)
    plat = platform or rng.choice(["android", "ios"])

    if plat == "android":
        model, os_version, abi = rng.choice(_ANDROID_DEVICES)
        app_version = _LINKEDIN_ANDROID_APP
        tls = rng.choice(_TLS_ANDROID)
        user_agent = (
            f"LinkedIn/{app_version} (Android {os_version}; {model}; {abi}) "
            f"com.linkedin.android"
        )
    else:
        model, os_version = rng.choice(_IOS_DEVICES)
        abi = "arm64"
        app_version = _LINKEDIN_IOS_APP
        tls = rng.choice(_TLS_IOS)
        user_agent = (
            f"LinkedIn/{app_version} (iOS {os_version}; {model}) com.linkedin.LinkedIn"
        )

    # Stable synthetic device id (UUID-shaped) derived from the account.
    raw = hashlib.sha256(f"{account_id}:device".encode()).hexdigest()
    device_id = "-".join([raw[0:8], raw[8:12], raw[12:16], raw[16:20], raw[20:32]])

    return {
        "platform": plat,
        "device_model": model,
        "os_version": os_version,
        "abi": abi,
        "app_version": app_version,
        "user_agent": user_agent,
        "device_id": device_id,
        "tls_impersonate": tls,
        "locale": rng.choice(_LOCALES),
    }
