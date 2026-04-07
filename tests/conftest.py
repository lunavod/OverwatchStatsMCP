"""Test configuration: spins up a PostgreSQL container and wires it into db.async_session.

Requires Docker to be running. Tests NEVER connect to any external / production database.
"""

import os

os.environ["IS_TESTING"] = "1"

import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db
import main
from db import Base
import models  # noqa: F401 — ensure all models are registered with Base.metadata
from models import User


# ---------------------------------------------------------------------------
# Auth context helpers for tests
# ---------------------------------------------------------------------------

from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken


class _FakeAccessToken(AccessToken):
    """Extended token carrying user identity for tests."""
    user_id: uuid.UUID
    is_admin: bool = False


@contextmanager
def set_current_user(user: User):
    """Context manager that sets the MCP auth context to the given user."""
    token = _FakeAccessToken(
        token="test-token",
        client_id="test",
        scopes=[],
        user_id=user.id,
        is_admin=user.is_admin,
    )
    auth_user = AuthenticatedUser(token)
    t = auth_context_var.set(auth_user)
    try:
        yield
    finally:
        auth_context_var.reset(t)


# ---------------------------------------------------------------------------
# Session-scoped: container + engine + table creation (once per test run)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _test_uploads_dir():
    """Redirect UPLOADS_DIR to a temp directory for the entire test session."""
    tmp = Path(tempfile.mkdtemp(prefix="ow_test_uploads_"))
    original = main.UPLOADS_DIR
    main.UPLOADS_DIR = tmp
    yield tmp
    main.UPLOADS_DIR = original
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def postgres_url():
    """Start a disposable PostgreSQL container and yield an asyncpg URL."""
    with PostgresContainer("postgres:16-alpine") as pg:
        host = pg.get_container_host_ip()
        port = pg.get_exposed_port(5432)
        yield f"postgresql+asyncpg://test:test@{host}:{port}/test"


@pytest.fixture(scope="session")
async def engine(postgres_url):
    """Create the async engine and all tables once per session."""
    eng = create_async_engine(postgres_url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session", autouse=True)
async def _override_db_session(engine):
    """Replace db.async_session with the test-container-backed factory.

    This runs once at session start, BEFORE any test, guaranteeing that no
    test code can ever reach a production database — even if the test suite
    is accidentally executed on a production host.
    """
    # Dispose the default (production) engine so it can never be used
    await db.engine.dispose()

    test_factory = async_sessionmaker(engine, expire_on_commit=False)
    db.async_session = test_factory
    yield


# ---------------------------------------------------------------------------
# Session-scoped: default test user (created once, used by all tests)
# ---------------------------------------------------------------------------

_default_user: User | None = None


@pytest.fixture(scope="session")
async def default_user(_override_db_session):
    """Create a default test user once per session."""
    global _default_user
    async with db.async_session() as session:
        async with session.begin():
            user = User(
                google_sub="test-google-sub-default",
                email="testuser@test.com",
                display_name="Test User",
                is_admin=False,
            )
            session.add(user)
        await session.refresh(user)
    _default_user = user
    return user


@pytest.fixture(autouse=True)
async def _set_default_user(default_user):
    """Automatically set the auth context to the default test user for every test."""
    with set_current_user(default_user):
        yield


# ---------------------------------------------------------------------------
# Function-scoped: per-test cleanup
# ---------------------------------------------------------------------------

from testcontainers.postgres import PostgresContainer  # noqa: E402


@pytest.fixture(autouse=True)
async def _cleanup(engine, default_user):
    """Truncate every table after each test for full isolation (except users)."""
    yield
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name == "users":
                continue
            await conn.execute(table.delete())
