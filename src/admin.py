"""Admin panel routes for user management.

Mounted at /admin/ on the Starlette app. Requires the requesting user
to be an admin (checked via session cookie or MCP auth context).
"""

import logging
import uuid
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import delete, func, select
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

import db
from models import Match, User

logger = logging.getLogger(__name__)

_templates_dir = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_templates_dir)), autoescape=True)

# Simple session store: cookie → user_id
# In production, use signed cookies or a proper session backend.
ADMIN_SESSIONS: dict[str, uuid.UUID] = {}
ADMIN_SESSION_COOKIE = "ow_admin_session"


def _render(template_name: str, **context) -> HTMLResponse:
    tpl = _jinja_env.get_template(template_name)
    return HTMLResponse(tpl.render(**context))


async def _get_admin_user(request: Request) -> User | None:
    """Get the admin user from the session cookie."""
    session_id = request.cookies.get(ADMIN_SESSION_COOKIE)
    if not session_id or session_id not in ADMIN_SESSIONS:
        return None

    user_id = ADMIN_SESSIONS[session_id]
    async with db.async_session() as session:
        user = (
            await session.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()

    if user and user.is_admin and not user.is_disabled:
        return user
    return None


def _require_admin(handler):
    """Decorator that returns 403 if user is not an admin."""
    async def wrapper(request: Request) -> Response:
        user = await _get_admin_user(request)
        if not user:
            return RedirectResponse("/admin/login", status_code=303)
        request.state.admin_user = user
        return await handler(request)
    return wrapper


async def admin_login(request: Request) -> Response:
    """Admin login — redirects to Google OAuth for identity verification."""
    from auth import GOOGLE_CLIENT_ID, start_admin_google_login

    if not GOOGLE_CLIENT_ID:
        return HTMLResponse(
            "<h1>OAuth not configured</h1><p>Set GOOGLE_CLIENT_ID to enable admin login.</p>",
            status_code=500,
        )

    url = start_admin_google_login()
    return RedirectResponse(url, status_code=303)


@_require_admin
async def admin_users(request: Request) -> Response:
    """List all users with match counts."""
    flash_message = request.query_params.get("flash")
    flash_type = request.query_params.get("flash_type", "success")

    async with db.async_session() as session:
        # Get users with match counts
        stmt = (
            select(
                User,
                func.count(Match.id).label("match_count"),
            )
            .outerjoin(Match, Match.user_id == User.id)
            .group_by(User.id)
            .order_by(User.created_at)
        )
        rows = (await session.execute(stmt)).all()

    users = []
    for user, match_count in rows:
        user.match_count = match_count
        users.append(user)

    return _render(
        "admin/users.html",
        users=users,
        flash_message=flash_message,
        flash_type=flash_type,
    )


@_require_admin
async def admin_toggle_admin(request: Request) -> Response:
    """Toggle admin status for a user."""
    user_id = uuid.UUID(request.path_params["user_id"])

    async with db.async_session() as session:
        async with session.begin():
            user = (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
            if user:
                user.is_admin = not user.is_admin

    return RedirectResponse(
        "/admin/?flash=Admin+status+toggled&flash_type=success",
        status_code=303,
    )


@_require_admin
async def admin_disable_user(request: Request) -> Response:
    """Toggle disabled status for a user."""
    user_id = uuid.UUID(request.path_params["user_id"])

    async with db.async_session() as session:
        async with session.begin():
            user = (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
            if user:
                user.is_disabled = not user.is_disabled

    return RedirectResponse(
        "/admin/?flash=User+status+toggled&flash_type=success",
        status_code=303,
    )


@_require_admin
async def admin_delete_user(request: Request) -> Response:
    """Delete a user and all their data."""
    user_id = uuid.UUID(request.path_params["user_id"])

    # Don't let admin delete themselves
    if request.state.admin_user.id == user_id:
        return RedirectResponse(
            "/admin/?flash=Cannot+delete+yourself&flash_type=error",
            status_code=303,
        )

    async with db.async_session() as session:
        async with session.begin():
            result = await session.execute(delete(User).where(User.id == user_id))

    if result.rowcount > 0:
        return RedirectResponse(
            "/admin/?flash=User+deleted&flash_type=success",
            status_code=303,
        )
    return RedirectResponse(
        "/admin/?flash=User+not+found&flash_type=error",
        status_code=303,
    )


@_require_admin
async def admin_set_quota(request: Request) -> Response:
    """Set max_stored_matches for a user."""
    user_id = uuid.UUID(request.path_params["user_id"])
    form = await request.form()
    quota = int(str(form.get("max_stored_matches", 0)))

    async with db.async_session() as session:
        async with session.begin():
            user = (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one_or_none()
            if user:
                user.max_stored_matches = max(0, quota)

    return RedirectResponse(
        "/admin/?flash=Quota+updated&flash_type=success",
        status_code=303,
    )
