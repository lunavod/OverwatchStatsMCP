"""OAuth 2.1 authorization server provider using Google as the identity provider.

Implements the OAuthAuthorizationServerProvider protocol from the MCP SDK,
acting as an OAuth proxy: our /authorize redirects to Google, and our callback
exchanges the Google auth code for a Google ID token, creates/finds the local
user, and issues our own access token.

Clients, access tokens, and refresh tokens are persisted in the database so
that sessions survive server restarts.  Authorization codes and pending auth
flows remain in-memory (they are short-lived, seconds to minutes).

Environment variables:
    GOOGLE_CLIENT_ID — Google Cloud Console OAuth 2.0 client ID
    GOOGLE_CLIENT_SECRET — Google Cloud Console OAuth 2.0 client secret
    EXTERNAL_URL — Base URL of the MCP server (e.g. https://mcp.example.com)
"""

import logging
import os
import secrets
import time
import uuid

import httpx
from sqlalchemy import delete, func, select
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

import db
from models import OAuthAccessToken, OAuthClient, OAuthRefreshToken, User
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

logger = logging.getLogger(__name__)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
EXTERNAL_URL = os.getenv("EXTERNAL_URL", "http://localhost:8000")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class OwAccessToken(AccessToken):
    """Extended access token carrying user identity."""
    user_id: uuid.UUID
    email: str
    is_admin: bool = False


class OwAuthorizationCode(AuthorizationCode):
    """Extended authorization code carrying user identity from Google."""
    user_id: uuid.UUID
    email: str
    is_admin: bool = False


# In-memory stores for short-lived / in-flight data only
_auth_codes: dict[str, OwAuthorizationCode] = {}

# Pending authorization flows: state → (client, params)
_pending_auths: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams]] = {}

# Pending admin login flows: state → True (just needs to exist)
_pending_admin_auths: set[str] = set()


class OwAuthProvider:
    """OAuth authorization server provider that delegates to Google for authentication."""

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with db.async_session() as session:
            row = (await session.execute(
                select(OAuthClient).where(OAuthClient.client_id == client_id)
            )).scalar_one_or_none()
        if row:
            return OAuthClientInformationFull.model_validate(row.client_info_json)
        return None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            client_info.client_id = secrets.token_hex(16)
        async with db.async_session() as session:
            async with session.begin():
                existing = (await session.execute(
                    select(OAuthClient).where(OAuthClient.client_id == client_info.client_id)
                )).scalar_one_or_none()
                if existing:
                    existing.client_info_json = client_info.model_dump(mode="json")
                else:
                    session.add(OAuthClient(
                        client_id=client_info.client_id,
                        client_info_json=client_info.model_dump(mode="json"),
                    ))

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        # Generate a state token to link the Google callback back to this flow
        state = secrets.token_urlsafe(32)
        _pending_auths[state] = (client, params)

        # Redirect to Google's OAuth consent screen
        google_params = {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": f"{EXTERNAL_URL}/auth/google/callback",
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return construct_redirect_uri(GOOGLE_AUTH_URL, **google_params)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> OwAuthorizationCode | None:
        code = _auth_codes.get(authorization_code)
        if code and code.client_id == client.client_id:
            return code
        return None

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: OwAuthorizationCode
    ) -> OAuthToken:
        # Generate tokens
        access_token_str = secrets.token_urlsafe(32)
        refresh_token_str = secrets.token_urlsafe(32)
        expires_in = 3600 * 24 * 7  # 7 days

        async with db.async_session() as session:
            async with session.begin():
                session.add(OAuthAccessToken(
                    token=access_token_str,
                    client_id=authorization_code.client_id,
                    scopes=authorization_code.scopes,
                    expires_at=int(time.time()) + expires_in,
                    user_id=authorization_code.user_id,
                    email=authorization_code.email,
                    is_admin=authorization_code.is_admin,
                ))
                session.add(OAuthRefreshToken(
                    token=refresh_token_str,
                    client_id=authorization_code.client_id,
                    scopes=authorization_code.scopes,
                ))

        # Clean up the used auth code
        _auth_codes.pop(authorization_code.code, None)

        return OAuthToken(
            access_token=access_token_str,
            token_type="Bearer",
            expires_in=expires_in,
            refresh_token=refresh_token_str,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        async with db.async_session() as session:
            row = (await session.execute(
                select(OAuthRefreshToken).where(OAuthRefreshToken.token == refresh_token)
            )).scalar_one_or_none()
        if row and row.client_id == client.client_id:
            return RefreshToken(
                token=row.token,
                client_id=row.client_id,
                scopes=row.scopes,
            )
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Find the old access token for this client to get user info
        async with db.async_session() as session:
            old_row = (await session.execute(
                select(OAuthAccessToken).where(OAuthAccessToken.client_id == client.client_id)
            )).scalars().first()

        if not old_row:
            from mcp.server.auth.provider import TokenError
            raise TokenError(error="invalid_grant", error_description="No associated access token found")

        # Rotate tokens
        new_access = secrets.token_urlsafe(32)
        new_refresh = secrets.token_urlsafe(32)
        expires_in = 3600 * 24 * 7

        async with db.async_session() as session:
            async with session.begin():
                session.add(OAuthAccessToken(
                    token=new_access,
                    client_id=client.client_id,
                    scopes=scopes or refresh_token.scopes,
                    expires_at=int(time.time()) + expires_in,
                    user_id=old_row.user_id,
                    email=old_row.email,
                    is_admin=old_row.is_admin,
                ))
                session.add(OAuthRefreshToken(
                    token=new_refresh,
                    client_id=client.client_id,
                    scopes=scopes or refresh_token.scopes,
                ))
                # Remove old tokens
                await session.execute(
                    delete(OAuthAccessToken).where(OAuthAccessToken.token == old_row.token)
                )
                await session.execute(
                    delete(OAuthRefreshToken).where(OAuthRefreshToken.token == refresh_token.token)
                )

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=expires_in,
            refresh_token=new_refresh,
            scope=" ".join(scopes) if scopes else None,
        )

    async def load_access_token(self, token: str) -> OwAccessToken | None:
        async with db.async_session() as session:
            row = (await session.execute(
                select(OAuthAccessToken).where(OAuthAccessToken.token == token)
            )).scalar_one_or_none()

        if not row:
            return None

        if row.expires_at and row.expires_at < int(time.time()):
            # Expired — clean up
            async with db.async_session() as session:
                async with session.begin():
                    await session.execute(
                        delete(OAuthAccessToken).where(OAuthAccessToken.token == token)
                    )
            return None

        return OwAccessToken(
            token=row.token,
            client_id=row.client_id,
            scopes=row.scopes,
            expires_at=row.expires_at,
            user_id=row.user_id,
            email=row.email,
            is_admin=row.is_admin,
        )

    async def revoke_token(self, token: OwAccessToken | RefreshToken) -> None:
        async with db.async_session() as session:
            async with session.begin():
                if isinstance(token, OwAccessToken):
                    await session.execute(
                        delete(OAuthAccessToken).where(OAuthAccessToken.token == token.token)
                    )
                elif isinstance(token, RefreshToken):
                    await session.execute(
                        delete(OAuthRefreshToken).where(OAuthRefreshToken.token == token.token)
                    )


async def _get_or_create_user(google_sub: str, email: str, name: str | None) -> User:
    """Find existing user by google_sub or create a new one."""
    async with db.async_session() as session:
        user = (
            await session.execute(
                select(User).where(User.google_sub == google_sub)
            )
        ).scalar_one_or_none()

        if user:
            # Update last login
            async with db.async_session() as s2:
                async with s2.begin():
                    u = (await s2.execute(select(User).where(User.id == user.id))).scalar_one()
                    u.last_login_at = func.now()
                    if name and not u.display_name:
                        u.display_name = name
            return user

    # Create new user
    async with db.async_session() as session:
        async with session.begin():
            # Check if this is the first user (auto-admin)
            count = (await session.execute(select(func.count(User.id)))).scalar_one()
            is_first = count == 0

            user = User(
                google_sub=google_sub,
                email=email,
                display_name=name,
                is_admin=is_first,
            )
            session.add(user)
        await session.refresh(user)
    return user


async def _exchange_google_code(code: str) -> dict | None:
    """Exchange a Google auth code for user info. Returns userinfo dict or None on failure."""
    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "redirect_uri": f"{EXTERNAL_URL}/auth/google/callback",
                    "grant_type": "authorization_code",
                },
            )
            resp.raise_for_status()
            token_data = resp.json()

            userinfo_resp = await http.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
            )
            userinfo_resp.raise_for_status()
            return userinfo_resp.json()
    except Exception:
        logger.exception("Failed to exchange Google auth code")
        return None


def start_admin_google_login() -> str:
    """Initiate a Google OAuth flow for admin login. Returns the Google auth URL."""
    state = "admin:" + secrets.token_urlsafe(32)
    _pending_admin_auths.add(state)

    google_params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{EXTERNAL_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return construct_redirect_uri(GOOGLE_AUTH_URL, **google_params)


async def google_callback(request: Request):
    """Handle the OAuth callback from Google.

    Dispatches to either the MCP auth flow or admin login flow based on
    the state parameter prefix.
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        return HTMLResponse(f"<h1>Authorization denied</h1><p>{error}</p>", status_code=403)

    if not code or not state:
        return HTMLResponse("<h1>Invalid callback</h1>", status_code=400)

    # Admin login flow
    if state in _pending_admin_auths:
        _pending_admin_auths.discard(state)
        return await _handle_admin_callback(code)

    # MCP auth flow
    pending = _pending_auths.pop(state, None)
    if not pending:
        return HTMLResponse("<h1>Invalid or expired state</h1>", status_code=400)

    return await _handle_mcp_callback(code, pending)


async def _handle_admin_callback(code: str):
    """Handle the Google callback for admin panel login."""
    from admin import ADMIN_SESSIONS, ADMIN_SESSION_COOKIE

    userinfo = await _exchange_google_code(code)
    if not userinfo:
        return HTMLResponse("<h1>Failed to authenticate with Google</h1>", status_code=500)

    user = await _get_or_create_user(userinfo["sub"], userinfo.get("email", ""), userinfo.get("name"))

    if user.is_disabled:
        return HTMLResponse("<h1>Account disabled</h1><p>Contact an administrator.</p>", status_code=403)

    if not user.is_admin:
        return HTMLResponse("<h1>Access denied</h1><p>You are not an admin.</p>", status_code=403)

    session_id = secrets.token_urlsafe(32)
    ADMIN_SESSIONS[session_id] = user.id
    resp = RedirectResponse("/admin/", status_code=303)
    resp.set_cookie(ADMIN_SESSION_COOKIE, session_id, httponly=True, samesite="lax")
    return resp


async def _handle_mcp_callback(code: str, pending: tuple):
    """Handle the Google callback for MCP OAuth flow."""
    client, params = pending

    userinfo = await _exchange_google_code(code)
    if not userinfo:
        return HTMLResponse("<h1>Failed to authenticate with Google</h1>", status_code=500)

    user = await _get_or_create_user(userinfo["sub"], userinfo.get("email", ""), userinfo.get("name"))

    if user.is_disabled:
        return HTMLResponse("<h1>Account disabled</h1><p>Contact an administrator.</p>", status_code=403)

    # Generate our authorization code
    our_code = secrets.token_urlsafe(32)
    _auth_codes[our_code] = OwAuthorizationCode(
        code=our_code,
        scopes=params.scopes or [],
        expires_at=time.time() + 300,  # 5 minutes
        client_id=client.client_id,
        code_challenge=params.code_challenge,
        redirect_uri=params.redirect_uri,
        redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
        user_id=user.id,
        email=user.email,
        is_admin=user.is_admin,
    )

    # Redirect back to the MCP client
    redirect_url = construct_redirect_uri(
        str(params.redirect_uri),
        code=our_code,
        state=params.state,
    )
    return RedirectResponse(redirect_url)
