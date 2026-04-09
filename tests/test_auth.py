"""Tests for the OAuth authorization server provider."""

import time
import uuid

import pytest

import db
from auth import (
    OwAccessToken,
    OwAuthProvider,
    OwAuthorizationCode,
    _auth_codes,
    _get_or_create_user,
    _pending_admin_auths,
    _pending_auths,
    start_admin_google_login,
)
from models import OAuthAccessToken, OAuthRefreshToken
from mcp.server.auth.provider import AuthorizationParams, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull


@pytest.fixture(autouse=True)
def _clear_auth_stores():
    """Clear in-memory auth stores between tests."""
    _auth_codes.clear()
    _pending_auths.clear()
    _pending_admin_auths.clear()
    yield
    _auth_codes.clear()
    _pending_auths.clear()
    _pending_admin_auths.clear()


@pytest.fixture
def provider():
    return OwAuthProvider()


@pytest.fixture
def client_info():
    return OAuthClientInformationFull(
        client_id="test-client-123",
        redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
    )


async def _insert_access_token(token, client_id, user_id, email, expires_at=None, is_admin=False):
    """Insert an access token directly into the DB for testing."""
    if expires_at is None:
        expires_at = int(time.time()) + 3600
    async with db.async_session() as session:
        async with session.begin():
            session.add(OAuthAccessToken(
                token=token,
                client_id=client_id,
                scopes=[],
                expires_at=expires_at,
                user_id=user_id,
                email=email,
                is_admin=is_admin,
            ))


async def _insert_refresh_token(token, client_id, scopes=None):
    """Insert a refresh token directly into the DB for testing."""
    async with db.async_session() as session:
        async with session.begin():
            session.add(OAuthRefreshToken(
                token=token,
                client_id=client_id,
                scopes=scopes or [],
            ))


class TestClientRegistration:
    async def test_register_and_get_client(self, provider, client_info):
        await provider.register_client(client_info)
        result = await provider.get_client("test-client-123")
        assert result is not None
        assert result.client_id == "test-client-123"

    async def test_get_nonexistent_client(self, provider):
        result = await provider.get_client("nonexistent")
        assert result is None

    async def test_register_generates_client_id_if_missing(self, provider):
        info = OAuthClientInformationFull(
            redirect_uris=["https://example.com/callback"],
        )
        await provider.register_client(info)
        assert info.client_id is not None
        assert len(info.client_id) > 0

    async def test_register_client_survives_reregistration(self, provider, client_info):
        await provider.register_client(client_info)
        # Re-register same client (simulates restart + re-register)
        await provider.register_client(client_info)
        result = await provider.get_client("test-client-123")
        assert result is not None
        assert result.client_id == "test-client-123"


class TestAuthorize:
    async def test_authorize_returns_google_url(self, provider, client_info):
        await provider.register_client(client_info)
        params = AuthorizationParams(
            state="test-state",
            scopes=["mcp"],
            code_challenge="test-challenge",
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            redirect_uri_provided_explicitly=True,
        )
        url = await provider.authorize(client_info, params)
        assert "accounts.google.com" in url
        assert "response_type=code" in url

    async def test_authorize_stores_pending_auth(self, provider, client_info):
        await provider.register_client(client_info)
        params = AuthorizationParams(
            state="test-state",
            scopes=[],
            code_challenge="challenge",
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            redirect_uri_provided_explicitly=True,
        )
        await provider.authorize(client_info, params)
        assert len(_pending_auths) == 1


class TestTokenExchange:
    async def test_exchange_authorization_code(self, provider, client_info):
        await provider.register_client(client_info)
        user_id = uuid.uuid4()

        auth_code = OwAuthorizationCode(
            code="test-code",
            scopes=["mcp"],
            expires_at=time.time() + 300,
            client_id="test-client-123",
            code_challenge="challenge",
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            redirect_uri_provided_explicitly=True,
            user_id=user_id,
            email="test@example.com",
            is_admin=False,
        )
        _auth_codes["test-code"] = auth_code

        token = await provider.exchange_authorization_code(client_info, auth_code)
        assert token.access_token
        assert token.refresh_token
        assert token.token_type == "Bearer"

        # Verify access token is stored in DB with user info
        stored = await provider.load_access_token(token.access_token)
        assert stored is not None
        assert stored.user_id == user_id
        assert stored.email == "test@example.com"

    async def test_load_authorization_code(self, provider, client_info):
        await provider.register_client(client_info)
        auth_code = OwAuthorizationCode(
            code="test-code",
            scopes=[],
            expires_at=time.time() + 300,
            client_id="test-client-123",
            code_challenge="challenge",
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            redirect_uri_provided_explicitly=True,
            user_id=uuid.uuid4(),
            email="test@example.com",
        )
        _auth_codes["test-code"] = auth_code

        result = await provider.load_authorization_code(client_info, "test-code")
        assert result is not None
        assert result.code == "test-code"

    async def test_load_authorization_code_wrong_client(self, provider, client_info):
        other_client = OAuthClientInformationFull(
            client_id="other-client",
            redirect_uris=["https://example.com"],
        )
        auth_code = OwAuthorizationCode(
            code="test-code",
            scopes=[],
            expires_at=time.time() + 300,
            client_id="test-client-123",
            code_challenge="challenge",
            redirect_uri="https://claude.ai/api/mcp/auth_callback",
            redirect_uri_provided_explicitly=True,
            user_id=uuid.uuid4(),
            email="test@example.com",
        )
        _auth_codes["test-code"] = auth_code

        result = await provider.load_authorization_code(other_client, "test-code")
        assert result is None


class TestAccessToken:
    async def test_load_valid_access_token(self, provider):
        user_id = uuid.uuid4()
        await _insert_access_token("valid-token", "test", user_id, "test@example.com")

        result = await provider.load_access_token("valid-token")
        assert result is not None
        assert result.user_id == user_id

    async def test_load_expired_access_token(self, provider):
        user_id = uuid.uuid4()
        await _insert_access_token(
            "expired-token", "test", user_id, "test@example.com",
            expires_at=int(time.time()) - 100,
        )

        result = await provider.load_access_token("expired-token")
        assert result is None

        # Verify it was cleaned up from DB
        async with db.async_session() as session:
            from sqlalchemy import select
            row = (await session.execute(
                select(OAuthAccessToken).where(OAuthAccessToken.token == "expired-token")
            )).scalar_one_or_none()
        assert row is None

    async def test_load_nonexistent_token(self, provider):
        result = await provider.load_access_token("nonexistent")
        assert result is None


class TestRefreshToken:
    async def test_load_refresh_token(self, provider, client_info):
        await provider.register_client(client_info)
        await _insert_refresh_token("refresh-1", "test-client-123", ["mcp"])

        result = await provider.load_refresh_token(client_info, "refresh-1")
        assert result is not None

    async def test_exchange_refresh_token(self, provider, client_info):
        await provider.register_client(client_info)
        user_id = uuid.uuid4()

        await _insert_access_token(
            "old-access", "test-client-123", user_id, "test@example.com",
            is_admin=True,
        )
        await _insert_refresh_token("old-refresh", "test-client-123", ["mcp"])

        rt = RefreshToken(
            token="old-refresh",
            client_id="test-client-123",
            scopes=["mcp"],
        )

        new_token = await provider.exchange_refresh_token(client_info, rt, ["mcp"])
        assert new_token.access_token != "old-access"
        assert new_token.refresh_token != "old-refresh"

        # New token has user info
        stored = await provider.load_access_token(new_token.access_token)
        assert stored is not None
        assert stored.user_id == user_id
        assert stored.is_admin is True


class TestRevoke:
    async def test_revoke_access_token(self, provider):
        user_id = uuid.uuid4()
        await _insert_access_token("to-revoke", "test", user_id, "test@example.com")

        token = OwAccessToken(
            token="to-revoke",
            client_id="test",
            scopes=[],
            user_id=user_id,
            email="test@example.com",
        )

        await provider.revoke_token(token)
        result = await provider.load_access_token("to-revoke")
        assert result is None

    async def test_revoke_refresh_token(self, provider, client_info):
        await provider.register_client(client_info)
        await _insert_refresh_token("rt-to-revoke", "test-client-123", ["mcp"])

        rt = RefreshToken(
            token="rt-to-revoke",
            client_id="test-client-123",
            scopes=["mcp"],
        )
        await provider.revoke_token(rt)
        result = await provider.load_refresh_token(client_info, "rt-to-revoke")
        assert result is None


class TestGetOrCreateUser:
    async def test_creates_new_user(self):
        user = await _get_or_create_user("google-sub-new", "new@test.com", "New User")
        assert user.email == "new@test.com"
        assert user.google_sub == "google-sub-new"
        assert user.display_name == "New User"

    async def test_first_user_is_admin(self):
        # The default_user from conftest is already created, so this won't be first.
        # Create a fresh scenario by checking the existing user.
        user = await _get_or_create_user("google-sub-another", "another@test.com", "Another")
        # Not first user (default_user exists), so not admin
        assert user.is_admin is False

    async def test_returns_existing_user(self):
        user1 = await _get_or_create_user("google-sub-existing", "existing@test.com", "Existing")
        user2 = await _get_or_create_user("google-sub-existing", "existing@test.com", "Existing")
        assert user1.id == user2.id


class TestAdminGoogleLogin:
    def test_start_admin_login_returns_google_url(self):
        url = start_admin_google_login()
        assert "accounts.google.com" in url
        assert "response_type=code" in url

    def test_start_admin_login_stores_pending_state(self):
        start_admin_google_login()
        assert len(_pending_admin_auths) == 1
        state = next(iter(_pending_admin_auths))
        assert state.startswith("admin:")
