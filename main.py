import base64
import os
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Float, Integer, String, case, cast, delete, func, select
from sqlalchemy.orm import joinedload
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

import db
from mcp.server.fastmcp import FastMCP
from models import HeroStat, HeroStatValue, Match, PlayerStat, Screenshot
from webhook import fire_webhook

UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", "uploads"))
UPLOADS_DIR.mkdir(exist_ok=True)

mcp = FastMCP("OverwatchStats", json_response=True)

# Mount static file serving for uploaded screenshots
mcp._custom_starlette_routes.append(
    Mount("/uploads", app=StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _f(v):
    """Round a numeric value to 2 decimal places, defaulting to 0.0."""
    return round(float(v), 2) if v is not None else 0.0


def _apply_match_filters(stmt, queue_type=None, from_date=None, to_date=None):
    """Apply common match-level filters to a statement that already joins Match."""
    if queue_type:
        stmt = stmt.where(Match.queue_type == queue_type.upper())
    if from_date:
        stmt = stmt.where(Match.played_at >= datetime.fromisoformat(from_date))
    if to_date:
        stmt = stmt.where(Match.played_at <= datetime.fromisoformat(to_date))
    return stmt


def _flip_result(match_result, player_team):
    """Return match result from a player's perspective.

    match_result is stored from self-player's perspective.
    ALLY team sees the same result; ENEMY team sees the opposite.
    """
    if player_team == "ALLY" or match_result == "UNKNOWN":
        return match_result
    if match_result == "VICTORY":
        return "DEFEAT"
    if match_result == "DEFEAT":
        return "VICTORY"
    return match_result


_TIME_GROUP_BYS = {"week", "day", "hour", "weekday"}


def _resolve_group_col(name):
    """Map a group_by string to a SQLAlchemy column expression.

    Returns (col_expr, needs_hero_join: bool, is_time_based: bool).
    """
    if name == "role":
        return PlayerStat.role, False, False
    if name == "map":
        return Match.map_name, False, False
    if name == "mode":
        return Match.mode, False, False
    if name == "hero":
        return HeroStat.hero_name, True, False
    if name == "week":
        return func.date_trunc("week", Match.played_at), False, True
    if name == "day":
        return func.date_trunc("day", Match.played_at), False, True
    if name == "hour":
        return func.extract("hour", Match.played_at), False, True
    if name == "weekday":
        return func.extract("isodow", Match.played_at), False, True
    return None, False, False


def _duration_seconds():
    """SQL expression: parse 'MM:SS' duration string into total seconds."""
    return (
        cast(func.split_part(Match.duration, ":", 1), Integer) * 60
        + cast(func.split_part(Match.duration, ":", 2), Integer)
    )


def _safe_float_cast():
    """SQL expression: safely normalize hero stat value string to a castable numeric string.

    Uses regexp_replace to normalize the value, then checks if the result is a valid number.
    This avoids CASE short-circuit issues in PostgreSQL aggregates.

    Returns a Float column expression (NULL for unparseable values).
    """
    v = HeroStatValue.value
    # Step 1: Normalize based on format
    # For MM:SS → convert to seconds as text
    # For everything else → strip commas and %
    normalized = case(
        (
            v.op("~")(r"^\d+:\d+$"),
            # Convert MM:SS to seconds as text: minutes*60 + seconds
            cast(
                cast(func.split_part(v, ":", 1), Integer) * 60
                + cast(func.split_part(v, ":", 2), Integer),
                String,
            ),
        ),
        else_=func.regexp_replace(v, r"[,%]", "", "g"),
    )
    # Step 2: Check if the normalized result is a valid number, then cast
    return case(
        (
            normalized.op("~")(r"^-?\d+\.?\d*$"),
            cast(normalized, Float),
        ),
        else_=None,
    )


# ---------------------------------------------------------------------------
# Screenshot upload helpers
# ---------------------------------------------------------------------------

_ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}


def _save_screenshot(data_b64: str, filename_hint: str | None = None) -> str:
    """Decode a base64 image and save it to UPLOADS_DIR. Returns the relative URL path."""
    raw = base64.b64decode(data_b64)

    # Detect extension from filename hint or default to png
    ext = "png"
    if filename_hint:
        parts = filename_hint.rsplit(".", 1)
        if len(parts) == 2 and parts[1].lower() in _ALLOWED_EXTENSIONS:
            ext = parts[1].lower()

    name = f"{uuid.uuid4().hex}.{ext}"
    (UPLOADS_DIR / name).write_bytes(raw)
    return f"/uploads/{name}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def ping() -> str:
    """Health check - returns pong."""
    return "pong"


@mcp.tool()
async def submit_match(
    map_name: str,
    duration: str,
    mode: str,
    queue_type: str,
    result: str,
    players: list[dict],
    played_at: str | None = None,
    notes: str | None = None,
    is_backfill: bool = False,
    source: str = "",
    screenshots: list[str] | None = None,
    screenshot_uploads: list[dict] | None = None,
) -> dict:
    """Submit a completed Overwatch match with all player stats.

    Parameters:
        map_name: Map name (e.g. "Lijiang Tower")
        duration: Match duration as MM:SS
        mode: Game mode — PUSH, CONTROL, ESCORT, HYBRID, CLASH, FLASHPOINT
        queue_type: COMPETITIVE or QUICKPLAY
        result: VICTORY, DEFEAT, or UNKNOWN
        players: Array of 10 player objects with keys:
            team (ALLY/ENEMY), role (TANK/DPS/SUPPORT), player_name,
            eliminations, assists, deaths, damage, healing, mitigation (all int|null),
            is_self (bool, default false),
            hero (optional dict with hero_name and stats list of {label, value, is_featured})
        played_at: Optional ISO 8601 timestamp
        notes: Optional free-text notes about the match
        is_backfill: Whether this match was backfilled from historical data (default false)
        source: Optional source identifier for the match (e.g. "ocr", "manual")
        screenshots: Optional list of screenshot URLs (image download links)
        screenshot_uploads: Optional list of base64 image uploads, each with keys:
            data (base64-encoded image bytes), filename (optional, used for extension detection)
    """
    async with db.async_session() as session:
        async with session.begin():
            match = Match(
                map_name=map_name,
                duration=duration,
                mode=mode.upper(),
                queue_type=queue_type.upper(),
                result=result.upper(),
                played_at=datetime.fromisoformat(played_at) if played_at else None,
                notes=notes,
                is_backfill=is_backfill,
                source=source,
            )
            session.add(match)

            for p in players:
                ps = PlayerStat(
                    match=match,
                    team=p["team"].upper(),
                    role=p["role"].upper(),
                    player_name=p["player_name"],
                    eliminations=p.get("eliminations"),
                    assists=p.get("assists"),
                    deaths=p.get("deaths"),
                    damage=p.get("damage"),
                    healing=p.get("healing"),
                    mitigation=p.get("mitigation"),
                    is_self=p.get("is_self", False),
                )
                session.add(ps)

                hero = p.get("hero")
                if hero:
                    hs = HeroStat(
                        player_stat=ps,
                        hero_name=hero["hero_name"],
                    )
                    session.add(hs)

                    for sv in hero.get("stats", []):
                        session.add(
                            HeroStatValue(
                                hero_stat=hs,
                                label=sv["label"],
                                value=str(sv["value"]),
                                is_featured=sv.get("is_featured", False),
                            )
                        )

            for url in screenshots or []:
                session.add(Screenshot(match=match, url=url))

            for upload in screenshot_uploads or []:
                path = _save_screenshot(upload["data"], upload.get("filename"))
                session.add(Screenshot(match=match, url=path))

    match_result = {"match_id": str(match.id)}

    await fire_webhook({
        **match_result,
        "map_name": map_name,
        "duration": duration,
        "mode": mode.upper(),
        "queue_type": queue_type.upper(),
        "result": result.upper(),
        "played_at": played_at,
        "notes": notes,
        "is_backfill": is_backfill,
        "source": source,
    })

    return match_result


@mcp.tool()
async def get_match(match_id: str) -> dict:
    """Get full details of a match by ID, including all player stats and hero stats.

    Parameters:
        match_id: UUID of the match
    """
    async with db.async_session() as session:
        stmt = (
            select(Match)
            .where(Match.id == uuid.UUID(match_id))
            .options(
                joinedload(Match.player_stats)
                .joinedload(PlayerStat.hero_stat)
                .joinedload(HeroStat.values),
                joinedload(Match.screenshots),
            )
        )
        result = await session.execute(stmt)
        match = result.unique().scalar_one_or_none()

    if not match:
        return {"error": "Match not found"}

    return {
        "id": str(match.id),
        "map_name": match.map_name,
        "duration": match.duration,
        "mode": match.mode,
        "queue_type": match.queue_type,
        "result": match.result,
        "played_at": match.played_at.isoformat() if match.played_at else None,
        "created_at": match.created_at.isoformat() if match.created_at else None,
        "notes": match.notes,
        "is_backfill": match.is_backfill,
        "source": match.source,
        "screenshots": [s.url for s in match.screenshots],
        "player_stats": [
            {
                "id": str(ps.id),
                "team": ps.team,
                "role": ps.role,
                "player_name": ps.player_name,
                "eliminations": ps.eliminations,
                "assists": ps.assists,
                "deaths": ps.deaths,
                "damage": ps.damage,
                "healing": ps.healing,
                "mitigation": ps.mitigation,
                "is_self": ps.is_self,
                "hero_stat": (
                    {
                        "hero_name": ps.hero_stat.hero_name,
                        "values": [
                            {
                                "label": v.label,
                                "value": v.value,
                                "is_featured": v.is_featured,
                            }
                            for v in ps.hero_stat.values
                        ],
                    }
                    if ps.hero_stat
                    else None
                ),
            }
            for ps in match.player_stats
        ],
    }


@mcp.tool()
async def list_matches(
    map_name: str | None = None,
    mode: str | None = None,
    queue_type: str | None = None,
    result: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    hero_name: str | None = None,
    sort_by: str | None = None,
    sort_order: str = "desc",
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """List matches with optional filters and pagination.

    Parameters:
        map_name: Filter by map name
        mode: Filter by mode (PUSH, CONTROL, ESCORT, HYBRID, CLASH, FLASHPOINT)
        queue_type: Filter by COMPETITIVE or QUICKPLAY
        result: Filter by VICTORY, DEFEAT, or UNKNOWN
        from_date: ISO 8601 — only matches on or after this date
        to_date: ISO 8601 — only matches on or before this date
        hero_name: Filter to matches where self-player played this hero (case-insensitive)
        sort_by: Sort by a stat: eliminations, assists, deaths, damage, healing, mitigation
        sort_order: "asc" or "desc" (default "desc")
        limit: Max results (default 20, max 100)
        offset: Pagination offset (default 0)
    """
    limit = min(limit, 100)

    async with db.async_session() as session:
        base = select(Match)

        if map_name:
            base = base.where(Match.map_name == map_name)
        if mode:
            base = base.where(Match.mode == mode.upper())
        if queue_type:
            base = base.where(Match.queue_type == queue_type.upper())
        if result:
            base = base.where(Match.result == result.upper())
        if from_date:
            base = base.where(Match.played_at >= datetime.fromisoformat(from_date))
        if to_date:
            base = base.where(Match.played_at <= datetime.fromisoformat(to_date))

        # B2: hero_name filter
        if hero_name:
            hero_sub = (
                select(PlayerStat.match_id)
                .join(HeroStat)
                .where(PlayerStat.is_self == True)  # noqa: E712
                .where(func.lower(HeroStat.hero_name) == hero_name.lower())
            )
            base = base.where(Match.id.in_(hero_sub))

        # B1: sort_by stat
        _SORTABLE = {
            "eliminations": PlayerStat.eliminations,
            "assists": PlayerStat.assists,
            "deaths": PlayerStat.deaths,
            "damage": PlayerStat.damage,
            "healing": PlayerStat.healing,
            "mitigation": PlayerStat.mitigation,
        }
        sort_col_expr = _SORTABLE.get(sort_by) if sort_by else None

        if sort_col_expr is not None:
            # Subquery: self-player stat per match
            stat_sub = (
                select(
                    PlayerStat.match_id.label("match_id"),
                    sort_col_expr.label("sort_value"),
                )
                .where(PlayerStat.is_self == True)  # noqa: E712
                .subquery()
            )
            base = base.outerjoin(stat_sub, Match.id == stat_sub.c.match_id)
            order_expr = stat_sub.c.sort_value
            if sort_order.lower() == "asc":
                order_expr = order_expr.asc().nullslast()
            else:
                order_expr = order_expr.desc().nullslast()
            # Add sort_value to select so we can include it in results
            base = base.add_columns(stat_sub.c.sort_value)

        count_base = base.with_only_columns(func.count(func.distinct(Match.id)))
        total = (await session.execute(count_base)).scalar_one()

        if sort_col_expr is not None:
            rows_stmt = base.order_by(order_expr).limit(limit).offset(offset)
        else:
            rows_stmt = base.order_by(Match.created_at.desc()).limit(limit).offset(offset)

        raw_rows = (await session.execute(rows_stmt)).all() if sort_col_expr is not None else None
        if sort_col_expr is None:
            rows = (await session.execute(rows_stmt)).scalars().all()
        else:
            rows = None

    matches_out = []
    if sort_col_expr is not None and raw_rows is not None:
        for row in raw_rows:
            m = row[0]
            sv = row[1]
            d = {
                "id": str(m.id),
                "map_name": m.map_name,
                "duration": m.duration,
                "mode": m.mode,
                "queue_type": m.queue_type,
                "result": m.result,
                "played_at": m.played_at.isoformat() if m.played_at else None,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "notes": m.notes,
                "is_backfill": m.is_backfill,
                "sort_value": int(sv) if sv is not None else None,
            }
            matches_out.append(d)
    else:
        for m in rows:
            matches_out.append(
                {
                    "id": str(m.id),
                    "map_name": m.map_name,
                    "duration": m.duration,
                    "mode": m.mode,
                    "queue_type": m.queue_type,
                    "result": m.result,
                    "played_at": m.played_at.isoformat() if m.played_at else None,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "notes": m.notes,
                    "is_backfill": m.is_backfill,
                }
            )

    return {"matches": matches_out, "total": total}


@mcp.tool()
async def get_stats_summary(
    group_by: str | None = None,
    group_by_2: str | None = None,
    queue_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    last_n: int | None = None,
) -> dict:
    """Get aggregated stats for the logged-in player (is_self=True).

    Parameters:
        group_by: Group results by "role", "map", "mode", "hero", "week", "day", "hour", "weekday". None = overall.
        group_by_2: Optional second grouping dimension (same options as group_by). Requires group_by.
        queue_type: Filter to COMPETITIVE or QUICKPLAY
        from_date: ISO 8601 — only matches on or after this date
        to_date: ISO 8601 — only matches on or before this date
        last_n: Only consider the most recent N matches (after other filters)
    """
    if group_by_2 and not group_by:
        return {"error": "group_by_2 requires group_by to be set"}

    async with db.async_session() as session:
        # Resolve group columns
        group_col, needs_hero_1, is_time_1 = _resolve_group_col(group_by) if group_by else (None, False, False)
        group_col_2, needs_hero_2, is_time_2 = _resolve_group_col(group_by_2) if group_by_2 else (None, False, False)
        needs_hero_join = needs_hero_1 or needs_hero_2
        is_time_based = is_time_1 or is_time_2

        # Build columns
        columns = [
            func.count(func.distinct(Match.id)).label("matches"),
            func.sum(case((Match.result == "VICTORY", 1), else_=0)).label("wins"),
            func.sum(case((Match.result == "DEFEAT", 1), else_=0)).label("losses"),
            func.avg(PlayerStat.eliminations).label("avg_eliminations"),
            func.avg(PlayerStat.assists).label("avg_assists"),
            func.avg(PlayerStat.deaths).label("avg_deaths"),
            func.avg(PlayerStat.damage).label("avg_damage"),
            func.avg(PlayerStat.healing).label("avg_healing"),
            func.avg(PlayerStat.mitigation).label("avg_mitigation"),
        ]

        if group_col is not None:
            columns.insert(0, group_col.label("group_key"))
        if group_col_2 is not None:
            columns.insert(1 if group_col is not None else 0, group_col_2.label("group_key_2"))

        stmt = select(*columns).select_from(PlayerStat).join(Match)

        if needs_hero_join:
            stmt = stmt.join(HeroStat)

        stmt = stmt.where(PlayerStat.is_self == True)  # noqa: E712

        # Filter out NULL played_at when using time-based grouping
        if is_time_based:
            stmt = stmt.where(Match.played_at.isnot(None))

        stmt = _apply_match_filters(stmt, queue_type, from_date, to_date)

        # A1: last_n — restrict to N most recent matches
        if last_n is not None:
            recent_sub = (
                select(Match.id)
                .select_from(PlayerStat)
                .join(Match)
                .where(PlayerStat.is_self == True)  # noqa: E712
            )
            if is_time_based:
                recent_sub = recent_sub.where(Match.played_at.isnot(None))
            recent_sub = _apply_match_filters(recent_sub, queue_type, from_date, to_date)
            recent_sub = recent_sub.order_by(Match.played_at.desc()).limit(last_n)
            stmt = stmt.where(Match.id.in_(recent_sub))

        # Group by
        group_by_clauses = []
        order_by_clauses = []
        if group_col is not None:
            group_by_clauses.append(group_col)
            if is_time_1:
                order_by_clauses.append(group_col)
        if group_col_2 is not None:
            group_by_clauses.append(group_col_2)
            if is_time_2:
                order_by_clauses.append(group_col_2)

        if group_by_clauses:
            stmt = stmt.group_by(*group_by_clauses)
        if order_by_clauses:
            stmt = stmt.order_by(*order_by_clauses)

        rows = (await session.execute(stmt)).all()

    groups = []
    for row in rows:
        matches = row.matches or 0
        wins = row.wins or 0
        losses = row.losses or 0

        gk = row.group_key if group_col is not None else "overall"
        # Serialize datetime/numeric group keys
        if hasattr(gk, "isoformat"):
            gk = gk.isoformat()
        elif isinstance(gk, (float, Decimal)):
            gk = int(gk)

        entry = {
            "group_key": gk,
            "matches": matches,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / matches, 4) if matches else 0.0,
            "avg_eliminations": _f(row.avg_eliminations),
            "avg_assists": _f(row.avg_assists),
            "avg_deaths": _f(row.avg_deaths),
            "avg_damage": _f(row.avg_damage),
            "avg_healing": _f(row.avg_healing),
            "avg_mitigation": _f(row.avg_mitigation),
        }

        if group_col_2 is not None:
            gk2 = row.group_key_2
            if hasattr(gk2, "isoformat"):
                gk2 = gk2.isoformat()
            elif isinstance(gk2, (float, Decimal)):
                gk2 = int(gk2)
            entry["group_key_2"] = gk2

        groups.append(entry)

    return {"groups": groups}


@mcp.tool()
async def get_hero_detail_stats(
    hero_name: str | None = None,
    label: str | None = None,
    queue_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """Get detailed per-hero stat breakdowns from hero_stat_values for the self-player.

    Parses string stat values (percentages, MM:SS times, numbers with commas) into
    numeric form and returns count/avg/min/max grouped by hero and stat label.

    Parameters:
        hero_name: Filter to a specific hero (case-insensitive)
        label: Filter to a specific stat label (case-insensitive)
        queue_type: Filter to COMPETITIVE or QUICKPLAY
        from_date: ISO 8601 — only matches on or after this date
        to_date: ISO 8601 — only matches on or before this date
    """
    numeric_val = _safe_float_cast()

    async with db.async_session() as session:
        # CTE: parse values first, then aggregate (avoids PG CASE eval issues in aggs)
        parsed = (
            select(
                HeroStat.hero_name.label("hero_name"),
                HeroStatValue.label.label("label"),
                numeric_val.label("numeric_value"),
                HeroStatValue.value.contains("%").label("is_percent"),
                HeroStatValue.value.op("~")(r"^\d+:\d+$").label("is_time"),
            )
            .select_from(HeroStatValue)
            .join(HeroStat)
            .join(PlayerStat)
            .join(Match)
            .where(PlayerStat.is_self == True)  # noqa: E712
        )

        parsed = _apply_match_filters(parsed, queue_type, from_date, to_date)

        if hero_name:
            parsed = parsed.where(func.lower(HeroStat.hero_name) == hero_name.lower())
        if label:
            parsed = parsed.where(func.lower(HeroStatValue.label) == label.lower())

        parsed = parsed.cte("parsed")

        stmt = (
            select(
                parsed.c.hero_name,
                parsed.c.label,
                func.count().label("count"),
                func.avg(parsed.c.numeric_value).label("avg"),
                func.min(parsed.c.numeric_value).label("min"),
                func.max(parsed.c.numeric_value).label("max"),
                func.bool_or(parsed.c.is_percent).label("is_percent"),
                func.bool_or(parsed.c.is_time).label("is_time"),
            )
            .where(parsed.c.numeric_value.isnot(None))
            .group_by(parsed.c.hero_name, parsed.c.label)
        )

        rows = (await session.execute(stmt)).all()

    stats = []
    for row in rows:
        unit = "percent" if row.is_percent else ("time" if row.is_time else "number")
        stats.append(
            {
                "hero_name": row.hero_name,
                "label": row.label,
                "unit": unit,
                "count": row.count,
                "avg": _f(row.avg),
                "min": _f(row.min),
                "max": _f(row.max),
            }
        )

    return {"stats": stats}


@mcp.tool()
async def get_teammate_stats(
    queue_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    min_games: int = 1,
    limit: int = 50,
) -> dict:
    """Get win/loss stats grouped by teammate (ally players who aren't self).

    Normalizes names by stripping title suffixes like " (Bronze)" from player names.

    Parameters:
        queue_type: Filter to COMPETITIVE or QUICKPLAY
        from_date: ISO 8601 — only matches on or after this date
        to_date: ISO 8601 — only matches on or before this date
        min_games: Minimum games together to include (default 1)
        limit: Max teammates to return (default 50)
    """
    normalized_name = func.regexp_replace(
        PlayerStat.player_name, r"\s*\([^)]+\)\s*$", "", "g"
    )

    async with db.async_session() as session:
        stmt = (
            select(
                normalized_name.label("player_name"),
                func.count(func.distinct(Match.id)).label("games"),
                func.sum(case((Match.result == "VICTORY", 1), else_=0)).label("wins"),
                func.sum(case((Match.result == "DEFEAT", 1), else_=0)).label("losses"),
            )
            .select_from(PlayerStat)
            .join(Match)
            .where(PlayerStat.team == "ALLY")
            .where(PlayerStat.is_self == False)  # noqa: E712
        )

        stmt = _apply_match_filters(stmt, queue_type, from_date, to_date)

        stmt = (
            stmt.group_by(normalized_name)
            .having(func.count(func.distinct(Match.id)) >= min_games)
            .order_by(func.count(func.distinct(Match.id)).desc())
            .limit(limit)
        )

        rows = (await session.execute(stmt)).all()

    teammates = []
    for row in rows:
        games = row.games or 0
        wins = row.wins or 0
        losses = row.losses or 0
        teammates.append(
            {
                "player_name": row.player_name,
                "games": games,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / games, 4) if games else 0.0,
            }
        )

    return {"teammates": teammates}


@mcp.tool()
async def get_match_player_history(
    match_id: str,
    match_history: int = 3,
) -> dict:
    """Given a match ID, find all non-self players who appeared in other recorded matches and show their recent history with stats.

    Useful for recognizing recurring opponents/allies and reviewing how they performed previously.

    Parameters:
        match_id: UUID of the target match
        match_history: Number of recent past matches to return per player (default 3)
    """
    target_id = uuid.UUID(match_id)
    normalized_name = func.regexp_replace(
        PlayerStat.player_name, r"\s*\([^)]+\)\s*$", "", "g"
    )

    async with db.async_session() as session:
        # Query 1: target match info + all non-self players with normalized names
        q1 = (
            select(
                Match.map_name,
                Match.mode,
                Match.queue_type,
                Match.result,
                Match.played_at,
                PlayerStat.player_name,
                normalized_name.label("normalized_name"),
                PlayerStat.team,
                PlayerStat.role,
                PlayerStat.eliminations,
                PlayerStat.assists,
                PlayerStat.deaths,
                PlayerStat.damage,
                PlayerStat.healing,
                PlayerStat.mitigation,
            )
            .select_from(PlayerStat)
            .join(Match)
            .where(Match.id == target_id)
            .where(PlayerStat.is_self == False)  # noqa: E712
        )
        rows_q1 = (await session.execute(q1)).all()

        if not rows_q1:
            return {"error": "Match not found"}

        # Extract match info from the first row
        r0 = rows_q1[0]
        match_info = {
            "map_name": r0.map_name,
            "mode": r0.mode,
            "queue_type": r0.queue_type,
            "result": r0.result,
            "played_at": r0.played_at.isoformat() if r0.played_at else None,
        }

        # Collect player info keyed by normalized name
        player_info = {}
        name_set = set()
        for row in rows_q1:
            name_set.add(row.normalized_name)
            player_info[row.normalized_name] = {
                "player_name": row.player_name,
                "normalized_name": row.normalized_name,
                "team": row.team,
                "role": row.role,
                "current_match_stats": {
                    "eliminations": row.eliminations,
                    "assists": row.assists,
                    "deaths": row.deaths,
                    "damage": row.damage,
                    "healing": row.healing,
                    "mitigation": row.mitigation,
                },
            }

        # Query 2: CTE with window functions for per-player history
        norm_col = func.regexp_replace(
            PlayerStat.player_name, r"\s*\([^)]+\)\s*$", "", "g"
        ).label("normalized_name")

        rn = func.row_number().over(
            partition_by=norm_col,
            order_by=Match.played_at.desc().nullslast(),
        ).label("rn")

        total_appearances = func.count().over(
            partition_by=norm_col,
        ).label("total_appearances")

        inner = (
            select(
                norm_col,
                PlayerStat.player_name,
                PlayerStat.team,
                PlayerStat.role,
                PlayerStat.eliminations,
                PlayerStat.assists,
                PlayerStat.deaths,
                PlayerStat.damage,
                PlayerStat.healing,
                PlayerStat.mitigation,
                Match.id.label("hist_match_id"),
                Match.map_name,
                Match.mode,
                Match.queue_type,
                Match.result,
                Match.played_at,
                Match.duration,
                rn,
                total_appearances,
            )
            .select_from(PlayerStat)
            .join(Match)
            .where(PlayerStat.is_self == False)  # noqa: E712
            .where(Match.id != target_id)
            .where(
                func.regexp_replace(
                    PlayerStat.player_name, r"\s*\([^)]+\)\s*$", "", "g"
                ).in_(name_set)
            )
        ).cte("history")

        outer = (
            select(inner)
            .where(inner.c.rn <= match_history)
            .order_by(inner.c.normalized_name, inner.c.rn)
        )

        rows_q2 = (await session.execute(outer)).all()

    # Build result: group history rows by normalized_name
    history_by_name = {}
    appearances_by_name = {}
    for row in rows_q2:
        nn = row.normalized_name
        appearances_by_name[nn] = row.total_appearances

        result_for_player = _flip_result(row.result, row.team) if row.result else None

        entry = {
            "match_id": str(row.hist_match_id),
            "map_name": row.map_name,
            "mode": row.mode,
            "queue_type": row.queue_type,
            "result_for_player": result_for_player,
            "played_at": row.played_at.isoformat() if row.played_at else None,
            "duration": row.duration,
            "team": row.team,
            "role": row.role,
            "stats": {
                "eliminations": row.eliminations,
                "assists": row.assists,
                "deaths": row.deaths,
                "damage": row.damage,
                "healing": row.healing,
                "mitigation": row.mitigation,
            },
        }
        history_by_name.setdefault(nn, []).append(entry)

    players_with_history = []
    players_without_history = []
    for nn, info in player_info.items():
        if nn in history_by_name:
            players_with_history.append({
                **info,
                "total_appearances": appearances_by_name[nn],
                "history": history_by_name[nn],
            })
        else:
            players_without_history.append({
                "player_name": info["player_name"],
                "normalized_name": info["normalized_name"],
                "team": info["team"],
                "role": info["role"],
            })

    return {
        "match_id": match_id,
        "match_info": match_info,
        "players_with_history": players_with_history,
        "players_without_history": players_without_history,
    }


@mcp.tool()
async def get_match_rankings(
    queue_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """Get average lobby rankings for the self-player across all matches.

    For each of 6 stats (eliminations, assists, deaths, damage, healing, mitigation),
    computes the self-player's rank within each match lobby and averages across matches.
    Also computes percentiles: (lobby_size - rank) / (lobby_size - 1).

    Parameters:
        queue_type: Filter to COMPETITIVE or QUICKPLAY
        from_date: ISO 8601 — only matches on or after this date
        to_date: ISO 8601 — only matches on or before this date
    """
    stat_names = ["eliminations", "assists", "deaths", "damage", "healing", "mitigation"]

    async with db.async_session() as session:
        # Build CTE with window functions for each stat
        stat_cols = {
            name: getattr(PlayerStat, name) for name in stat_names
        }

        rank_cols = []
        for name, col in stat_cols.items():
            rank_cols.append(
                func.rank().over(
                    partition_by=PlayerStat.match_id,
                    order_by=col.desc().nullslast(),
                ).label(f"{name}_rank")
            )

        lobby_size = func.count().over(partition_by=PlayerStat.match_id).label("lobby_size")

        cte = (
            select(
                PlayerStat.match_id,
                PlayerStat.is_self,
                lobby_size,
                *rank_cols,
            )
            .join(Match)
        )

        cte = _apply_match_filters(cte, queue_type, from_date, to_date)
        cte = cte.cte("ranked")

        # Query the CTE filtered to self
        agg_cols = [func.count().label("matches_analyzed")]
        for name in stat_names:
            agg_cols.append(func.avg(cast(cte.c[f"{name}_rank"], Float)).label(f"avg_{name}_rank"))
            # Percentile: (lobby_size - rank) / (lobby_size - 1), guarding against solo lobbies
            pct_expr = case(
                (cte.c.lobby_size > 1,
                 cast(cte.c.lobby_size - cte.c[f"{name}_rank"], Float)
                 / cast(cte.c.lobby_size - 1, Float)),
                else_=None,
            )
            agg_cols.append(func.avg(pct_expr).label(f"avg_{name}_pct"))

        result_stmt = (
            select(*agg_cols)
            .select_from(cte)
            .where(cte.c.is_self == True)  # noqa: E712
        )

        row = (await session.execute(result_stmt)).one()

    rankings = {}
    for name in stat_names:
        rankings[name] = {
            "avg_rank": _f(getattr(row, f"avg_{name}_rank")),
            "avg_percentile": _f(getattr(row, f"avg_{name}_pct")),
        }

    return {
        "matches_analyzed": row.matches_analyzed or 0,
        "rankings": rankings,
    }


@mcp.tool()
async def get_duration_stats(
    queue_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    bucket_size: int = 120,
) -> dict:
    """Get win rates and average stats bucketed by match duration.

    Parses MM:SS duration into seconds, groups into buckets, and computes
    win/loss rates and average self-player stats per bucket.

    Parameters:
        queue_type: Filter to COMPETITIVE or QUICKPLAY
        from_date: ISO 8601 — only matches on or after this date
        to_date: ISO 8601 — only matches on or before this date
        bucket_size: Bucket width in seconds (default 120 = 2 minutes)
    """
    dur_sec = _duration_seconds()
    bucket_expr = (func.floor(dur_sec / bucket_size) * bucket_size)

    async with db.async_session() as session:
        stmt = (
            select(
                cast(bucket_expr, Integer).label("bucket"),
                func.count(func.distinct(Match.id)).label("matches"),
                func.sum(case((Match.result == "VICTORY", 1), else_=0)).label("wins"),
                func.sum(case((Match.result == "DEFEAT", 1), else_=0)).label("losses"),
                func.avg(PlayerStat.eliminations).label("avg_eliminations"),
                func.avg(PlayerStat.assists).label("avg_assists"),
                func.avg(PlayerStat.deaths).label("avg_deaths"),
                func.avg(PlayerStat.damage).label("avg_damage"),
                func.avg(PlayerStat.healing).label("avg_healing"),
                func.avg(PlayerStat.mitigation).label("avg_mitigation"),
            )
            .select_from(PlayerStat)
            .join(Match)
            .where(PlayerStat.is_self == True)  # noqa: E712
            .where(Match.duration.op("~")(r"^\d+:\d+$"))
        )

        stmt = _apply_match_filters(stmt, queue_type, from_date, to_date)
        stmt = stmt.group_by(bucket_expr).order_by(bucket_expr)

        rows = (await session.execute(stmt)).all()

    def _fmt_time(seconds):
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}"

    buckets = []
    for row in rows:
        b = row.bucket
        matches = row.matches or 0
        wins = row.wins or 0
        losses = row.losses or 0
        buckets.append(
            {
                "range": f"{_fmt_time(b)}-{_fmt_time(b + bucket_size)}",
                "matches": matches,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / matches, 4) if matches else 0.0,
                "avg_eliminations": _f(row.avg_eliminations),
                "avg_assists": _f(row.avg_assists),
                "avg_deaths": _f(row.avg_deaths),
                "avg_damage": _f(row.avg_damage),
                "avg_healing": _f(row.avg_healing),
                "avg_mitigation": _f(row.avg_mitigation),
            }
        )

    return {"bucket_size_seconds": bucket_size, "buckets": buckets}


@mcp.tool()
async def edit_match(
    match_id: str,
    map_name: str | None = None,
    duration: str | None = None,
    mode: str | None = None,
    queue_type: str | None = None,
    result: str | None = None,
    played_at: str | None = None,
    notes: str | None = None,
    is_backfill: bool | None = None,
    source: str | None = None,
    screenshots_to_add: list[str] | None = None,
    screenshot_uploads: list[dict] | None = None,
    screenshots_to_remove: list[str] | None = None,
    player_edits: list[dict] | None = None,
) -> dict:
    """Edit an existing match's metadata. Only provided fields are updated.

    Parameters:
        match_id: UUID of the match to edit
        map_name: New map name
        duration: New duration as MM:SS
        mode: New game mode
        queue_type: New queue type (COMPETITIVE or QUICKPLAY)
        result: New result (VICTORY, DEFEAT, or UNKNOWN)
        played_at: New ISO 8601 timestamp (pass empty string to clear)
        notes: New notes text (pass empty string to clear)
        is_backfill: New backfill flag
        source: New source identifier
        screenshots_to_add: List of screenshot URLs to attach
        screenshot_uploads: List of base64 image uploads, each with keys:
            data (base64-encoded image bytes), filename (optional, used for extension detection)
        screenshots_to_remove: List of screenshot URLs to remove
        player_edits: List of player stat edits, each a dict with:
            player_stat_id (required UUID string) and any of:
            player_name, team (ALLY/ENEMY), role (TANK/DPS/SUPPORT),
            eliminations, assists, deaths, damage, healing, mitigation (int|null),
            is_self (bool), hero_name (string to set/change hero, or empty string to clear)
    """
    async with db.async_session() as session:
        async with session.begin():
            load_options = [joinedload(Match.screenshots)]
            if player_edits:
                load_options.append(
                    joinedload(Match.player_stats).joinedload(PlayerStat.hero_stat)
                )
            stmt = (
                select(Match)
                .where(Match.id == uuid.UUID(match_id))
                .options(*load_options)
            )
            match = (await session.execute(stmt)).unique().scalar_one_or_none()
            if not match:
                return {"error": "Match not found"}

            if map_name is not None:
                match.map_name = map_name
            if duration is not None:
                match.duration = duration
            if mode is not None:
                match.mode = mode.upper()
            if queue_type is not None:
                match.queue_type = queue_type.upper()
            if result is not None:
                match.result = result.upper()
            if played_at is not None:
                match.played_at = datetime.fromisoformat(played_at) if played_at else None
            if notes is not None:
                match.notes = notes or None
            if is_backfill is not None:
                match.is_backfill = is_backfill
            if source is not None:
                match.source = source

            if screenshots_to_remove:
                remove_set = set(screenshots_to_remove)
                for s in list(match.screenshots):
                    if s.url in remove_set:
                        await session.delete(s)

            for url in screenshots_to_add or []:
                session.add(Screenshot(match=match, url=url))

            for upload in screenshot_uploads or []:
                path = _save_screenshot(upload["data"], upload.get("filename"))
                session.add(Screenshot(match=match, url=path))

            if player_edits:
                ps_by_id = {str(ps.id): ps for ps in match.player_stats}
                for pe in player_edits:
                    ps = ps_by_id.get(pe["player_stat_id"])
                    if not ps:
                        continue
                    if "player_name" in pe:
                        ps.player_name = pe["player_name"]
                    if "team" in pe:
                        ps.team = pe["team"].upper()
                    if "role" in pe:
                        ps.role = pe["role"].upper()
                    if "eliminations" in pe:
                        ps.eliminations = pe["eliminations"]
                    if "assists" in pe:
                        ps.assists = pe["assists"]
                    if "deaths" in pe:
                        ps.deaths = pe["deaths"]
                    if "damage" in pe:
                        ps.damage = pe["damage"]
                    if "healing" in pe:
                        ps.healing = pe["healing"]
                    if "mitigation" in pe:
                        ps.mitigation = pe["mitigation"]
                    if "is_self" in pe:
                        ps.is_self = pe["is_self"]
                    if "hero_name" in pe:
                        if pe["hero_name"]:
                            if ps.hero_stat:
                                ps.hero_stat.hero_name = pe["hero_name"]
                            else:
                                session.add(HeroStat(player_stat=ps, hero_name=pe["hero_name"]))
                        elif ps.hero_stat:
                            await session.delete(ps.hero_stat)

    return {"updated": True}


@mcp.tool()
async def upload_screenshot(
    match_id: str,
    data: str,
    filename: str | None = None,
) -> dict:
    """Upload a base64-encoded screenshot and attach it to a match.

    Parameters:
        match_id: UUID of the match to attach the screenshot to
        data: Base64-encoded image bytes
        filename: Optional filename hint for extension detection (e.g. "screen.jpg")
    """
    path = _save_screenshot(data, filename)

    async with db.async_session() as session:
        async with session.begin():
            stmt = select(Match).where(Match.id == uuid.UUID(match_id))
            match = (await session.execute(stmt)).scalar_one_or_none()
            if not match:
                return {"error": "Match not found"}
            session.add(Screenshot(match=match, url=path))

    return {"url": path}


@mcp.tool()
async def delete_match(match_id: str) -> dict:
    """Delete a match and all associated data by ID.

    Parameters:
        match_id: UUID of the match to delete
    """
    async with db.async_session() as session:
        async with session.begin():
            stmt = delete(Match).where(Match.id == uuid.UUID(match_id))
            result = await session.execute(stmt)

    return {"deleted": result.rowcount > 0}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport="streamable-http")
