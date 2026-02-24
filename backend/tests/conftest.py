"""Test fixtures for AutoEngage backend tests."""

import asyncio
import os
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

engine = create_async_engine(TEST_DATABASE_URL, echo=False, connect_args=_connect_args)
TestSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Map PostgreSQL-specific types to SQLite equivalents for testing
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


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
async def setup_db():
    """Create tables before each test, drop after."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def db() -> AsyncSession:
    """Get a test database session."""
    async with TestSessionLocal() as session:
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


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict:
    """Sign up a test user and return auth headers."""
    response = await client.post(
        "/api/auth/signup",
        json={
            "email": f"test-{uuid.uuid4().hex[:8]}@example.com",
            "password": "testpassword123",
            "full_name": "Test User",
            "org_name": "Test Org",
        },
    )
    assert response.status_code == 201
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
