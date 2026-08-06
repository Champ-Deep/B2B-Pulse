"""Test fixtures for B2B Pulse backend tests."""

import json
import os
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, Text, event
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app

# Use SQLite for tests by default (no external DB needed).
# Override with TEST_DATABASE_URL env var for PostgreSQL integration tests.
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "sqlite+aiosqlite:///./test.db",
)

_connect_args = {}
_is_sqlite = "sqlite" in TEST_DATABASE_URL
if _is_sqlite:
    _connect_args["check_same_thread"] = False

# Map PostgreSQL-specific types to SQLite equivalents. Only needed on the
# SQLite path -- against Postgres the real types are used, which is the point of
# being able to run there.
if _is_sqlite:
    @event.listens_for(Base.metadata, "column_reflect")
    def _column_reflect(inspector, table, column_info):
        if isinstance(column_info["type"], JSONB):
            column_info["type"] = JSON()
        elif isinstance(column_info["type"], UUID):
            column_info["type"] = Text()

    # Also patch the types at the model level for table creation
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
    SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON  # type: ignore[attr-defined]
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
async def setup_db():
    """
    A fresh schema per test, on an engine built inside this test's event loop.

    The engine is created here rather than at import time, with ``NullPool``, so
    no connection outlives the loop that opened it. pytest-asyncio 1.x ignores
    an ``event_loop`` fixture override and gives every test its own loop, so a
    pooled connection from the first test ends up bound to a loop that has since
    closed.

    SQLite tolerated that; Postgres does not. Which meant the suite could only
    ever run against SQLite — a database this app does not use, and one that
    models neither JSONB nor native enums. Both appear in the migrations, so
    "green on SQLite" was never evidence the schema worked.

    Point ``TEST_DATABASE_URL`` at a Postgres instance to run against the real
    thing:

        TEST_DATABASE_URL=postgresql+asyncpg://user@localhost/b2bpulse_test pytest
    """
    from sqlalchemy.pool import NullPool

    test_engine = create_async_engine(
        TEST_DATABASE_URL, echo=False, connect_args=_connect_args, poolclass=NullPool
    )
    session_factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield session_factory
    finally:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await test_engine.dispose()


@pytest.fixture
async def db(setup_db) -> AsyncSession:
    """Get a test database session."""
    async with setup_db() as session:
        yield session


@pytest.fixture
async def client(db: AsyncSession) -> AsyncClient:
    """Get an HTTP client with test DB injected."""

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Clerk auth
#
# Tests mint real RS256 tokens and verify them against an injected JWKS, so the
# full signature-verification path runs -- no network, no Clerk account, and no
# "skip verification" shortcut that would let a broken verifier pass tests.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def clerk_keypair():
    """An RSA keypair plus the JWKS that describes its public half."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = "test-key"
    return private_key, {"keys": [jwk]}


@pytest.fixture(autouse=True)
def clerk_verifier(clerk_keypair):
    """Point the app's verifier at the test JWKS for the duration of a test."""
    from app.core.clerk import ClerkConfig, ClerkVerifier, set_verifier

    _, jwks = clerk_keypair
    set_verifier(ClerkVerifier(ClerkConfig(), jwks=jwks))
    yield
    set_verifier(None)


@pytest.fixture
def clerk_token(clerk_keypair):
    """Mint a Clerk-shaped session token."""
    import jwt as pyjwt

    private_key, _ = clerk_keypair

    def _mint(**claims) -> str:
        payload = {
            "sub": f"user_{uuid.uuid4().hex[:12]}",
            "email": f"test-{uuid.uuid4().hex[:8]}@example.com",
            "name": "Test User",
            "exp": int(time.time()) + 3600,
        }
        payload.update(claims)
        return pyjwt.encode(
            payload, private_key, algorithm="RS256", headers={"kid": "test-key"}
        )

    return _mint


@pytest.fixture
async def auth_headers(client: AsyncClient, clerk_token) -> dict:
    """
    Headers for an authenticated user.

    Calling /api/auth/me is what provisions the local User and Org, so this
    also exercises the just-in-time provisioning path every real client hits.
    """
    headers = {"Authorization": f"Bearer {clerk_token()}"}
    response = await client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    return headers


@pytest.fixture
async def admin_headers(client: AsyncClient, clerk_token, db) -> dict:
    """An authenticated user promoted to platform admin."""
    from sqlalchemy import select

    from app.models.user import User

    headers = {"Authorization": f"Bearer {clerk_token()}"}
    response = await client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200, response.text

    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(response.json()["id"])))
    ).scalar_one()
    user.is_platform_admin = True
    await db.commit()
    return headers
