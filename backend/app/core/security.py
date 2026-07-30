"""
Cryptographic helpers.

Session tokens are no longer issued here — Clerk owns identity, and this
service only verifies the tokens it is handed (see ``app/core/clerk.py``). What
remains is credential encryption and the short-lived signed values used for
OAuth state during integration connect.
"""

from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from jose import JWTError, jwt

from app.config import settings

fernet = Fernet(settings.fernet_key.encode() if settings.fernet_key else Fernet.generate_key())

# OAuth state is a round-trip through the browser and back within a minute or
# two; anything longer is a replay.
OAUTH_STATE_TTL_MINUTES = 10


def sign_state(data: dict, ttl_minutes: int = OAUTH_STATE_TTL_MINUTES) -> str:
    """Sign a short-lived payload for an OAuth round trip."""
    to_encode = data.copy()
    to_encode.update(
        {"exp": datetime.now(UTC) + timedelta(minutes=ttl_minutes), "type": "state"}
    )
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    """Decode a value this service signed. Returns None if invalid or expired."""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def encrypt_value(value: str) -> str:
    return fernet.encrypt(value.encode()).decode()


def decrypt_value(encrypted_value: str) -> str:
    return fernet.decrypt(encrypted_value.encode()).decode()
