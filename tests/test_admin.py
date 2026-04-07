"""Tests for the admin panel routes."""

import secrets

import pytest
from sqlalchemy import select
from starlette.requests import Request

import db
from admin import (
    ADMIN_SESSIONS,
    ADMIN_SESSION_COOKIE,
    _get_admin_user,
    admin_delete_user,
    admin_disable_user,
    admin_set_quota,
    admin_toggle_admin,
    admin_users,
)
from models import Match, User


@pytest.fixture
async def admin_user():
    """Create an admin user."""
    async with db.async_session() as session:
        existing = (
            await session.execute(
                select(User).where(User.google_sub == "admin-google-sub")
            )
        ).scalar_one_or_none()
        if existing:
            return existing

    async with db.async_session() as session:
        async with session.begin():
            user = User(
                google_sub="admin-google-sub",
                email="admin@test.com",
                display_name="Admin",
                is_admin=True,
            )
            session.add(user)
        await session.refresh(user)
    return user


@pytest.fixture
async def regular_user():
    """Create a regular (non-admin) user."""
    async with db.async_session() as session:
        existing = (
            await session.execute(
                select(User).where(User.google_sub == "regular-google-sub")
            )
        ).scalar_one_or_none()
        if existing:
            return existing

    async with db.async_session() as session:
        async with session.begin():
            user = User(
                google_sub="regular-google-sub",
                email="regular@test.com",
                display_name="Regular",
                is_admin=False,
            )
            session.add(user)
        await session.refresh(user)
    return user


@pytest.fixture
def admin_session_id(admin_user):
    """Create an admin session and return the session ID."""
    session_id = secrets.token_urlsafe(32)
    ADMIN_SESSIONS[session_id] = admin_user.id
    yield session_id
    ADMIN_SESSIONS.pop(session_id, None)


@pytest.fixture(autouse=True)
def _clear_admin_sessions():
    yield
    ADMIN_SESSIONS.clear()


def _make_request(path="/admin/", cookies=None, path_params=None, method="GET", form_data=None):
    """Create a minimal Starlette Request for testing handlers directly."""
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
        "path_params": path_params or {},
        "state": {},
    }
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        scope["headers"] = [(b"cookie", cookie_str.encode())]

    request = Request(scope)

    if form_data:
        request._form = form_data

    return request


class TestGetAdminUser:
    async def test_no_cookie_returns_none(self):
        req = _make_request()
        user = await _get_admin_user(req)
        assert user is None

    async def test_invalid_session_returns_none(self):
        req = _make_request(cookies={ADMIN_SESSION_COOKIE: "nonexistent"})
        user = await _get_admin_user(req)
        assert user is None

    async def test_valid_admin_session(self, admin_user, admin_session_id):
        req = _make_request(cookies={ADMIN_SESSION_COOKIE: admin_session_id})
        user = await _get_admin_user(req)
        assert user is not None
        assert user.id == admin_user.id

    async def test_non_admin_session_returns_none(self, regular_user):
        session_id = secrets.token_urlsafe(32)
        ADMIN_SESSIONS[session_id] = regular_user.id
        req = _make_request(cookies={ADMIN_SESSION_COOKIE: session_id})
        user = await _get_admin_user(req)
        assert user is None


class TestAdminUsersPage:
    async def test_unauthenticated_redirects_to_login(self):
        req = _make_request()
        resp = await admin_users(req)
        assert resp.status_code == 303
        assert "/admin/login" in resp.headers["location"]

    async def test_admin_can_list_users(self, admin_user, admin_session_id):
        req = _make_request(cookies={ADMIN_SESSION_COOKIE: admin_session_id})
        resp = await admin_users(req)
        assert resp.status_code == 200
        assert "admin@test.com" in resp.body.decode()


class TestToggleAdmin:
    async def test_toggle_admin_on(self, admin_session_id, regular_user):
        req = _make_request(
            path=f"/admin/users/{regular_user.id}/toggle-admin",
            cookies={ADMIN_SESSION_COOKIE: admin_session_id},
            path_params={"user_id": str(regular_user.id)},
            method="POST",
        )
        resp = await admin_toggle_admin(req)
        assert resp.status_code == 303

        async with db.async_session() as session:
            u = (await session.execute(select(User).where(User.id == regular_user.id))).scalar_one()
            assert u.is_admin is True

    async def test_unauthenticated_redirects_to_login(self, regular_user):
        req = _make_request(
            path_params={"user_id": str(regular_user.id)},
            method="POST",
        )
        resp = await admin_toggle_admin(req)
        assert resp.status_code == 303
        assert "/admin/login" in resp.headers["location"]


class TestDisableUser:
    async def test_disable_user(self, admin_session_id, regular_user):
        req = _make_request(
            cookies={ADMIN_SESSION_COOKIE: admin_session_id},
            path_params={"user_id": str(regular_user.id)},
            method="POST",
        )
        resp = await admin_disable_user(req)
        assert resp.status_code == 303

        async with db.async_session() as session:
            u = (await session.execute(select(User).where(User.id == regular_user.id))).scalar_one()
            assert u.is_disabled is True

    async def test_enable_disabled_user(self, admin_session_id, regular_user):
        async with db.async_session() as session:
            async with session.begin():
                u = (await session.execute(select(User).where(User.id == regular_user.id))).scalar_one()
                u.is_disabled = True

        req = _make_request(
            cookies={ADMIN_SESSION_COOKIE: admin_session_id},
            path_params={"user_id": str(regular_user.id)},
            method="POST",
        )
        resp = await admin_disable_user(req)
        assert resp.status_code == 303

        async with db.async_session() as session:
            u = (await session.execute(select(User).where(User.id == regular_user.id))).scalar_one()
            assert u.is_disabled is False


class TestDeleteUser:
    async def test_delete_user_cascades(self, admin_user, admin_session_id, regular_user):
        # Create a match for the regular user
        async with db.async_session() as session:
            async with session.begin():
                session.add(Match(
                    user_id=regular_user.id,
                    map_name="Hanamura",
                    duration="10:00",
                    mode="ESCORT",
                    queue_type="COMPETITIVE",
                    result="VICTORY",
                ))

        req = _make_request(
            cookies={ADMIN_SESSION_COOKIE: admin_session_id},
            path_params={"user_id": str(regular_user.id)},
            method="POST",
        )
        resp = await admin_delete_user(req)
        assert resp.status_code == 303

        async with db.async_session() as session:
            u = (await session.execute(select(User).where(User.id == regular_user.id))).scalar_one_or_none()
            assert u is None
            matches = (await session.execute(select(Match).where(Match.user_id == regular_user.id))).scalars().all()
            assert len(matches) == 0

    async def test_cannot_delete_self(self, admin_user, admin_session_id):
        req = _make_request(
            cookies={ADMIN_SESSION_COOKIE: admin_session_id},
            path_params={"user_id": str(admin_user.id)},
            method="POST",
        )
        resp = await admin_delete_user(req)
        assert resp.status_code == 303
        assert "Cannot" in resp.headers["location"]

        async with db.async_session() as session:
            u = (await session.execute(select(User).where(User.id == admin_user.id))).scalar_one_or_none()
            assert u is not None


class TestSetQuota:
    async def test_set_quota(self, admin_session_id, regular_user):
        req = _make_request(
            cookies={ADMIN_SESSION_COOKIE: admin_session_id},
            path_params={"user_id": str(regular_user.id)},
            method="POST",
            form_data={"max_stored_matches": "50"},
        )
        resp = await admin_set_quota(req)
        assert resp.status_code == 303

        async with db.async_session() as session:
            u = (await session.execute(select(User).where(User.id == regular_user.id))).scalar_one()
            assert u.max_stored_matches == 50
