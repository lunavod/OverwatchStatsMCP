"""Test configuration: spins up a PostgreSQL container and wires it into db.async_session.

Requires Docker to be running. Tests NEVER connect to any external / production database.
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

import db
from db import Base
import models  # noqa: F401 — ensure all models are registered with Base.metadata


# ---------------------------------------------------------------------------
# Session-scoped: container + engine + table creation (once per test run)
# ---------------------------------------------------------------------------


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
# Function-scoped: per-test cleanup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _cleanup(engine):
    """Truncate every table after each test for full isolation."""
    yield
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
