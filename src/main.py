import asyncio
import base64
import difflib
import logging
import os
import re
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Float, Integer, String, case, cast, delete, func, select
from sqlalchemy.orm import aliased, joinedload
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

import db
from mcp.server.fastmcp import FastMCP
from models import HeroStat, HeroStatValue, Match, PlayerNote, PlayerStat, Screenshot
from scoreboard import render_scoreboard
from telegram import send_scoreboard as send_telegram_scoreboard, is_configured as telegram_configured
from webhook import fire_webhook

UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", "uploads"))
UPLOADS_DIR.mkdir(exist_ok=True)

mcp = FastMCP("OverwatchStats", json_response=True)

# Mount static file serving for uploaded screenshots
mcp._custom_starlette_routes.append(
    Mount("/uploads", app=StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")
)

# ---------------------------------------------------------------------------
# Name validation lists (loaded once at import)
# ---------------------------------------------------------------------------

_BASE_DIR = Path(__file__).resolve().parent.parent


def _load_names(filename: str) -> list[str]:
    return [line.strip() for line in (_BASE_DIR / filename).read_text().splitlines() if line.strip()]


VALID_HEROES = _load_names("heroes.txt")
VALID_MAPS = _load_names("maps.txt")

_HERO_LOOKUP: dict[str, str] = {h.lower(): h for h in VALID_HEROES}
_MAP_LOOKUP: dict[str, str] = {m.lower(): m for m in VALID_MAPS}


def normalize_hero_name(raw: str) -> str | None:
    """Fuzzy-match a hero name to the canonical list. Returns canonical name or None."""
    cleaned = raw.strip()
    if not cleaned:
        return None
    lower = cleaned.lower()
    if lower in _HERO_LOOKUP:
        return _HERO_LOOKUP[lower]
    matches = difflib.get_close_matches(lower, _HERO_LOOKUP.keys(), n=1, cutoff=0.6)
    if matches:
        return _HERO_LOOKUP[matches[0]]
    return None


def normalize_map_name(raw: str) -> str | None:
    """Strip parenthetical suffix and fuzzy-match to canonical map name. Returns canonical name or None."""
    cleaned = re.sub(r"\s*\(.*\)\s*$", "", raw).strip()
    if not cleaned:
        return None
    lower = cleaned.lower()
    if lower in _MAP_LOOKUP:
        return _MAP_LOOKUP[lower]
    matches = difflib.get_close_matches(lower, _MAP_LOOKUP.keys(), n=1, cutoff=0.6)
    if matches:
        return _MAP_LOOKUP[matches[0]]
    return None


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
    rank_min: str | None = None,
    rank_max: str | None = None,
    is_wide_match: bool | None = None,
    banned_heroes: list[str] | None = None,
    initial_team_side: str | None = None,
    score_progression: list[str] | None = None,
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
            title (optional string, e.g. player's competitive title),
            hero_name (optional string, hero played — case-insensitive),
            eliminations, assists, deaths, damage, healing, mitigation (all int|null),
            is_self (bool, default false),
            in_party (bool, default false — marks a player in the recording player's group),
            joined_at (int, seconds from match start when this player joined, default 0),
            heroes (optional array of hero dicts, each with hero_name, started_at [int array of seconds from match start], and stats [{label, value, is_featured}]),
            swap_snapshots (optional array of cumulative stat snapshots at each hero swap, each with time (int seconds), eliminations, assists, deaths, damage, healing, mitigation — all int)
        played_at: Optional ISO 8601 timestamp
        notes: Optional free-text notes about the match
        is_backfill: Whether this match was backfilled from historical data (default false)
        source: Optional source identifier for the match (e.g. "ocr", "manual")
        screenshots: Optional list of screenshot URLs (image download links)
        screenshot_uploads: Optional list of base64 image uploads, each with keys:
            data (base64-encoded image bytes), filename (optional, used for extension detection)
        rank_min: Optional minimum rank in the lobby (e.g. "Gold 3")
        rank_max: Optional maximum rank in the lobby (e.g. "Diamond 1")
        is_wide_match: Optional flag indicating a wide skill-range match
        banned_heroes: Optional list of banned hero names (case-insensitive, fuzzy-matched)
        initial_team_side: Optional initial side — ATTACK or DEFEND
        score_progression: Optional array of round scores as "X:Y" strings (e.g. ["1:0", "1:1", "2:1"])
    """
    # --- Validate & normalize map name ---
    normalized_map = normalize_map_name(map_name)
    if normalized_map is None:
        return {"error": f"Unknown map name: {map_name!r}"}
    map_name = normalized_map

    # --- Validate & normalize hero names ---
    errors: list[str] = []
    for p in players:
        hero_name_raw = p.get("hero_name")
        if hero_name_raw:
            matched = normalize_hero_name(hero_name_raw)
            if matched is None:
                errors.append(hero_name_raw)
            else:
                p["hero_name"] = matched
        for hero_entry in p.get("heroes") or []:
            h_raw = hero_entry.get("hero_name")
            if h_raw:
                matched = normalize_hero_name(h_raw)
                if matched is None:
                    errors.append(h_raw)
                else:
                    hero_entry["hero_name"] = matched
    if errors:
        return {"error": f"Unknown hero name(s): {', '.join(repr(e) for e in errors)}"}

    # --- Validate & normalize banned heroes ---
    normalized_banned: list[str] | None = None
    if banned_heroes:
        normalized_banned = []
        ban_errors: list[str] = []
        for raw in banned_heroes:
            matched = normalize_hero_name(raw)
            if matched is None:
                ban_errors.append(raw)
            else:
                normalized_banned.append(matched)
        if ban_errors:
            return {"error": f"Unknown banned hero name(s): {', '.join(repr(e) for e in ban_errors)}"}

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
                rank_min=rank_min,
                rank_max=rank_max,
                is_wide_match=is_wide_match,
                banned_heroes=normalized_banned,
                initial_team_side=initial_team_side.upper() if initial_team_side else None,
                score_progression=score_progression,
            )
            session.add(match)

            for p in players:
                heroes_list = p.get("heroes")

                # Resolve hero name for denormalized column
                hero_name_raw = p.get("hero_name")
                if not hero_name_raw and heroes_list:
                    hero_name_raw = heroes_list[0]["hero_name"]

                ps = PlayerStat(
                    match=match,
                    team=p["team"].upper(),
                    role=p["role"].upper(),
                    player_name=p["player_name"],
                    title=p.get("title"),
                    hero=hero_name_raw if hero_name_raw else None,
                    eliminations=p.get("eliminations"),
                    assists=p.get("assists"),
                    deaths=p.get("deaths"),
                    damage=p.get("damage"),
                    healing=p.get("healing"),
                    mitigation=p.get("mitigation"),
                    is_self=p.get("is_self", False),
                    in_party=p.get("in_party", False),
                    joined_at=p.get("joined_at", 0),
                    swap_snapshots=p.get("swap_snapshots"),
                )
                session.add(ps)

                for hero_entry in heroes_list or []:
                    hs = HeroStat(
                        player_stat=ps,
                        hero_name=hero_entry["hero_name"],
                        started_at=hero_entry.get("started_at", []),
                    )
                    session.add(hs)

                    for sv in hero_entry.get("stats", []):
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

    match_id_str = str(match.id)
    match_result = {"match_id": match_id_str}

    # Generate scoreboard images
    try:
        match_data = await get_match(match_id_str)
        if "error" not in match_data:
            scoreboard_dir = UPLOADS_DIR / "scoreboards"
            scoreboard_dir.mkdir(exist_ok=True)
            out_path = scoreboard_dir / f"{match.id}.png"
            outputs = render_scoreboard(match_data, str(out_path))

            scoreboard_url = f"/uploads/scoreboards/{match.id}.png"
            hero_stats_url = None
            if len(outputs) > 1:
                hero_stats_url = f"/uploads/scoreboards/{match.id}_hero.png"

            async with db.async_session() as session:
                async with session.begin():
                    stmt = select(Match).where(Match.id == match.id)
                    m = (await session.execute(stmt)).scalar_one()
                    m.scoreboard_url = scoreboard_url
                    m.hero_stats_url = hero_stats_url

            match_result["scoreboard_url"] = scoreboard_url
            if hero_stats_url:
                match_result["hero_stats_url"] = hero_stats_url

            # Send to Telegram (fire and forget)
            if telegram_configured():
                asyncio.create_task(send_telegram_scoreboard(outputs))
    except Exception:
        logging.getLogger(__name__).exception("Scoreboard generation failed")

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


def _parse_duration_seconds(duration: str) -> int:
    """Parse MM:SS duration string to total seconds."""
    try:
        parts = duration.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return 0


def _hero_timeline(hero_stats_list):
    """Build sorted [[hero_name, seconds], ...] from all started_at entries."""
    pairs = []
    for hs in hero_stats_list:
        for t in (hs.started_at or []):
            pairs.append([hs.hero_name, t])
    pairs.sort(key=lambda x: x[1])
    return pairs


def _primary_hero(hero_stats_list, match_duration_seconds):
    """Determine which hero was played longest."""
    if not hero_stats_list:
        return None
    if len(hero_stats_list) == 1:
        return hero_stats_list[0].hero_name

    timeline = _hero_timeline(hero_stats_list)
    if not timeline:
        return hero_stats_list[0].hero_name

    time_per_hero = {}
    for i, (hero, start) in enumerate(timeline):
        end = timeline[i + 1][1] if i + 1 < len(timeline) else match_duration_seconds
        time_per_hero[hero] = time_per_hero.get(hero, 0) + (end - start)

    return max(time_per_hero, key=time_per_hero.get)


def _compute_hero_segments(timeline, swap_snapshots, match_duration_seconds):
    """Compute per-segment stat deltas from timeline + cumulative swap snapshots.

    Each segment covers one contiguous hero play period. Stats are the difference
    between the cumulative snapshot at the segment end and the one at the segment start.
    """
    if not timeline or not swap_snapshots:
        return []

    _STAT_KEYS = ("eliminations", "assists", "deaths", "damage", "healing", "mitigation")

    # Index snapshots by time for O(1) lookup
    snap_by_time = {s["time"]: s for s in swap_snapshots}

    segments = []
    for i, (hero, start) in enumerate(timeline):
        end = timeline[i + 1][1] if i + 1 < len(timeline) else match_duration_seconds
        snap_start = snap_by_time.get(start)
        snap_end = snap_by_time.get(end)
        if snap_start is None or snap_end is None:
            continue
        duration_secs = end - start
        mins, secs = divmod(duration_secs, 60)
        seg = {
            "hero": hero,
            "from": start,
            "to": end,
            "duration": f"{mins}:{secs:02d}",
        }
        for k in _STAT_KEYS:
            seg[k] = (snap_end.get(k) or 0) - (snap_start.get(k) or 0)
        segments.append(seg)
    return segments


def _build_player_hero_fields(ps, match_duration_seconds):
    """Build computed hero fields for a player stat."""
    timeline = _hero_timeline(ps.hero_stats)
    result = {
        "heroes": [
            {
                "hero_name": hs.hero_name,
                "started_at": hs.started_at or [],
                "values": [
                    {"label": v.label, "value": v.value, "is_featured": v.is_featured}
                    for v in hs.values
                ],
            }
            for hs in ps.hero_stats
        ],
        "hero_timeline": timeline,
        "primary_hero": _primary_hero(ps.hero_stats, match_duration_seconds),
        "starting_hero": timeline[0][0] if timeline else None,
        "ending_hero": timeline[-1][0] if timeline else None,
    }
    if ps.swap_snapshots:
        result["swap_snapshots"] = ps.swap_snapshots
        result["hero_segments"] = _compute_hero_segments(
            timeline, ps.swap_snapshots, match_duration_seconds
        )
    return result


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
                .joinedload(PlayerStat.hero_stats)
                .joinedload(HeroStat.values),
                joinedload(Match.screenshots),
            )
        )
        result = await session.execute(stmt)
        match = result.unique().scalar_one_or_none()

        if not match:
            return {"error": "Match not found"}

        player_names = [ps.player_name for ps in match.player_stats]
        notes_map = await _fetch_player_notes(session, player_names)

    match_duration_secs = _parse_duration_seconds(match.duration)

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
        "scoreboard_url": match.scoreboard_url,
        "hero_stats_url": match.hero_stats_url,
        "rank_min": match.rank_min,
        "rank_max": match.rank_max,
        "is_wide_match": match.is_wide_match,
        "banned_heroes": match.banned_heroes,
        "initial_team_side": match.initial_team_side,
        "score_progression": match.score_progression,
        "final_score": match.score_progression[-1] if match.score_progression else None,
        "screenshots": [s.url for s in match.screenshots],
        "player_stats": [
            {
                "id": str(ps.id),
                "team": ps.team,
                "role": ps.role,
                "player_name": ps.player_name,
                "title": ps.title,
                "hero": ps.hero,
                "player_note": notes_map.get(ps.player_name),
                "eliminations": ps.eliminations,
                "assists": ps.assists,
                "deaths": ps.deaths,
                "damage": ps.damage,
                "healing": ps.healing,
                "mitigation": ps.mitigation,
                "is_self": ps.is_self,
                "in_party": ps.in_party,
                "joined_at": ps.joined_at,
                **_build_player_hero_fields(ps, match_duration_secs),
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
    player_name: str | None = None,
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
        player_name: Filter to matches containing this player (case-insensitive, any team)
        sort_by: Sort by a stat: eliminations, assists, deaths, damage, healing, mitigation
        sort_order: "asc" or "desc" (default "desc")
        limit: Max results (default 20, max 100)
        offset: Pagination offset (default 0)
    """
    limit = min(limit, 100)

    async with db.async_session() as session:
        base = select(Match)

        if map_name:
            base = base.where(func.lower(Match.map_name) == map_name.lower())
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

        if player_name:
            player_sub = (
                select(PlayerStat.match_id)
                .where(func.lower(PlayerStat.player_name) == player_name.lower())
            )
            base = base.where(Match.id.in_(player_sub))

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
                "scoreboard_url": m.scoreboard_url,
                "hero_stats_url": m.hero_stats_url,
                "rank_min": m.rank_min,
                "rank_max": m.rank_max,
                "is_wide_match": m.is_wide_match,
                "banned_heroes": m.banned_heroes,
                "initial_team_side": m.initial_team_side,
                "score_progression": m.score_progression,
                "final_score": m.score_progression[-1] if m.score_progression else None,
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
                    "scoreboard_url": m.scoreboard_url,
                    "hero_stats_url": m.hero_stats_url,
                    "rank_min": m.rank_min,
                    "rank_max": m.rank_max,
                    "is_wide_match": m.is_wide_match,
                    "banned_heroes": m.banned_heroes,
                    "initial_team_side": m.initial_team_side,
                    "score_progression": m.score_progression,
                    "final_score": m.score_progression[-1] if m.score_progression else None,
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
    player_name: str | None = None,
) -> dict:
    """Get aggregated stats for the logged-in player (is_self=True).

    Parameters:
        group_by: Group results by "role", "map", "mode", "hero", "week", "day", "hour", "weekday". None = overall.
        group_by_2: Optional second grouping dimension (same options as group_by). Requires group_by.
        queue_type: Filter to COMPETITIVE or QUICKPLAY
        from_date: ISO 8601 — only matches on or after this date
        to_date: ISO 8601 — only matches on or before this date
        last_n: Only consider the most recent N matches (after other filters)
        player_name: Filter to matches containing this player (case-insensitive, any team)
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

        if player_name:
            _PS = aliased(PlayerStat)
            player_sub = (
                select(_PS.match_id)
                .where(func.lower(_PS.player_name) == player_name.lower())
            )
            stmt = stmt.where(Match.id.in_(player_sub))

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
            if player_name:
                _PS2 = aliased(PlayerStat)
                recent_sub = recent_sub.where(Match.id.in_(
                    select(_PS2.match_id)
                    .where(func.lower(_PS2.player_name) == player_name.lower())
                ))
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

        player_names = [row.player_name for row in rows]
        notes_map = await _fetch_player_notes(session, player_names)

    teammates = []
    for row in rows:
        games = row.games or 0
        wins = row.wins or 0
        losses = row.losses or 0
        teammates.append(
            {
                "player_name": row.player_name,
                "player_note": notes_map.get(row.player_name),
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
                "player_note": None,  # populated after notes fetch
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

        all_player_names = [info["player_name"] for info in player_info.values()]
        notes_map = await _fetch_player_notes(session, all_player_names)

    for info in player_info.values():
        info["player_note"] = notes_map.get(info["player_name"])

    # Build result: group history rows by normalized_name
    history_by_name: dict[str, list[dict]] = {}
    appearances_by_name: dict[str, int] = {}
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
                "player_note": info["player_note"],
            })

    return {
        "match_id": match_id,
        "match_info": match_info,
        "players_with_history": players_with_history,
        "players_without_history": players_without_history,
    }


@mcp.tool()
async def get_player_history(
    player_names: list[str],
    match_history: int = 3,
) -> dict:
    """Look up match history for a list of players by username.

    Like get_match_player_history but accepts usernames directly instead of a match ID.
    Shows each player's recent matches with stats, with results shown from their perspective.

    Parameters:
        player_names: List of player usernames to look up
        match_history: Number of recent past matches to return per player (default 3)
    """
    if not player_names:
        return {"players_with_history": [], "players_without_history": []}

    # Normalize input names the same way DB names are normalized
    normalized_inputs = {
        re.sub(r"\s*\([^)]+\)\s*$", "", name).strip(): name
        for name in player_names
    }
    name_set = set(normalized_inputs.keys())

    normalized_name_col = func.regexp_replace(
        PlayerStat.player_name, r"\s*\([^)]+\)\s*$", "", "g"
    )

    async with db.async_session() as session:
        # CTE with window functions for per-player history
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
            .where(normalized_name_col.in_(name_set))
        ).cte("history")

        outer = (
            select(inner)
            .where(inner.c.rn <= match_history)
            .order_by(inner.c.normalized_name, inner.c.rn)
        )

        rows = (await session.execute(outer)).all()

        all_raw_names = list(normalized_inputs.values())
        notes_map = await _fetch_player_notes(session, all_raw_names)

    # Build result
    history_by_name: dict[str, list[dict]] = {}
    appearances_by_name: dict[str, int] = {}
    for row in rows:
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
    for nn, original_name in normalized_inputs.items():
        note = notes_map.get(original_name)
        if nn in history_by_name:
            players_with_history.append({
                "player_name": original_name,
                "normalized_name": nn,
                "player_note": note,
                "total_appearances": appearances_by_name[nn],
                "history": history_by_name[nn],
            })
        else:
            players_without_history.append({
                "player_name": original_name,
                "normalized_name": nn,
                "player_note": note,
            })

    return {
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
    rank_min: str | None = None,
    rank_max: str | None = None,
    is_wide_match: bool | None = None,
    banned_heroes: list[str] | None = None,
    initial_team_side: str | None = None,
    score_progression: list[str] | None = None,
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
        rank_min: New minimum rank (pass empty string to clear)
        rank_max: New maximum rank (pass empty string to clear)
        is_wide_match: New wide match flag
        banned_heroes: New list of banned hero names (pass empty list to clear)
        initial_team_side: New initial side — ATTACK or DEFEND (pass empty string to clear)
        score_progression: New score progression as array of "X:Y" strings (pass empty list to clear)
        screenshots_to_add: List of screenshot URLs to attach
        screenshot_uploads: List of base64 image uploads, each with keys:
            data (base64-encoded image bytes), filename (optional, used for extension detection)
        screenshots_to_remove: List of screenshot URLs to remove
        player_edits: List of player stat edits, each a dict with:
            player_stat_id (required UUID string) and any of:
            player_name, title (string or empty string to clear),
            hero (string or empty string to clear — hero played),
            team (ALLY/ENEMY), role (TANK/DPS/SUPPORT),
            eliminations, assists, deaths, damage, healing, mitigation (int|null),
            is_self (bool), in_party (bool), joined_at (int),
            swap_snapshots (array of cumulative stat snapshots, or empty array to clear),
            heroes (array to replace all hero stats for this player, each with hero_name, started_at, stats)
    """
    # --- Validate & normalize map name ---
    if map_name is not None:
        normalized_map = normalize_map_name(map_name)
        if normalized_map is None:
            return {"error": f"Unknown map name: {map_name!r}"}
        map_name = normalized_map

    # --- Validate & normalize hero names in player edits ---
    if player_edits:
        errors: list[str] = []
        for pe in player_edits:
            hero_raw = pe.get("hero")
            if hero_raw:
                matched = normalize_hero_name(hero_raw)
                if matched is None:
                    errors.append(hero_raw)
                else:
                    pe["hero"] = matched
            for hero_entry in pe.get("heroes") or []:
                h_raw = hero_entry.get("hero_name")
                if h_raw:
                    matched = normalize_hero_name(h_raw)
                    if matched is None:
                        errors.append(h_raw)
                    else:
                        hero_entry["hero_name"] = matched
        if errors:
            return {"error": f"Unknown hero name(s): {', '.join(repr(e) for e in errors)}"}

    # --- Validate & normalize banned heroes ---
    normalized_banned: list[str] | None = None
    if banned_heroes is not None:
        if banned_heroes:
            normalized_banned = []
            ban_errors: list[str] = []
            for raw in banned_heroes:
                matched = normalize_hero_name(raw)
                if matched is None:
                    ban_errors.append(raw)
                else:
                    normalized_banned.append(matched)
            if ban_errors:
                return {"error": f"Unknown banned hero name(s): {', '.join(repr(e) for e in ban_errors)}"}
        else:
            normalized_banned = []

    async with db.async_session() as session:
        async with session.begin():
            load_options = [joinedload(Match.screenshots)]
            if player_edits:
                load_options.append(
                    joinedload(Match.player_stats)
                    .joinedload(PlayerStat.hero_stats)
                    .joinedload(HeroStat.values)
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
            if rank_min is not None:
                match.rank_min = rank_min or None
            if rank_max is not None:
                match.rank_max = rank_max or None
            if is_wide_match is not None:
                match.is_wide_match = is_wide_match
            if banned_heroes is not None:
                match.banned_heroes = normalized_banned or None
            if initial_team_side is not None:
                match.initial_team_side = initial_team_side.upper() if initial_team_side else None
            if score_progression is not None:
                match.score_progression = score_progression or None

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
                    if "title" in pe:
                        ps.title = pe["title"] or None
                    if "hero" in pe:
                        ps.hero = pe["hero"] or None
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
                    if "in_party" in pe:
                        ps.in_party = pe["in_party"]
                    if "joined_at" in pe:
                        ps.joined_at = pe["joined_at"]
                    if "swap_snapshots" in pe:
                        ps.swap_snapshots = pe["swap_snapshots"] or None
                    if "heroes" in pe:
                        for existing_hs in list(ps.hero_stats):
                            await session.delete(existing_hs)
                        await session.flush()
                        for hero_entry in pe["heroes"]:
                            hs = HeroStat(
                                player_stat=ps,
                                hero_name=hero_entry["hero_name"],
                                started_at=hero_entry.get("started_at", []),
                            )
                            session.add(hs)
                            for sv in hero_entry.get("stats", []):
                                session.add(HeroStatValue(
                                    hero_stat=hs,
                                    label=sv["label"],
                                    value=str(sv["value"]),
                                    is_featured=sv.get("is_featured", False),
                                ))

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


# ---------------------------------------------------------------------------
# Player notes helpers
# ---------------------------------------------------------------------------


async def _fetch_player_notes(session, player_names: list[str]) -> dict[str, str]:
    """Fetch notes for a list of player names. Returns {name: note} mapping."""
    if not player_names:
        return {}
    stmt = select(PlayerNote).where(PlayerNote.player_name.in_(player_names))
    rows = (await session.execute(stmt)).scalars().all()
    return {r.player_name: r.note for r in rows}


# ---------------------------------------------------------------------------
# Player notes CRUD
# ---------------------------------------------------------------------------


@mcp.tool()
async def set_player_note(player_name: str, note: str) -> dict:
    """Set or update a note for a player. Notes are global, not per-match.

    Parameters:
        player_name: The player's username (exact match)
        note: The note text (pass empty string to delete the note)
    """
    async with db.async_session() as session:
        async with session.begin():
            stmt = select(PlayerNote).where(PlayerNote.player_name == player_name)
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if not note:
                if existing:
                    await session.delete(existing)
                    return {"deleted": True}
                return {"deleted": False}
            if existing:
                existing.note = note
            else:
                session.add(PlayerNote(player_name=player_name, note=note))
    return {"player_name": player_name, "note": note}


@mcp.tool()
async def get_player_note(player_name: str) -> dict:
    """Get the note for a player.

    Parameters:
        player_name: The player's username (exact match)
    """
    async with db.async_session() as session:
        stmt = select(PlayerNote).where(PlayerNote.player_name == player_name)
        row = (await session.execute(stmt)).scalar_one_or_none()
    if not row:
        return {"player_name": player_name, "note": None}
    return {"player_name": row.player_name, "note": row.note}


@mcp.tool()
async def list_player_notes() -> dict:
    """List all player notes."""
    async with db.async_session() as session:
        stmt = select(PlayerNote).order_by(PlayerNote.player_name)
        rows = (await session.execute(stmt)).scalars().all()
    return {
        "notes": [
            {"player_name": r.player_name, "note": r.note}
            for r in rows
        ]
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    mcp.settings.host = args.host
    mcp.settings.port = args.port

    # Allow external hostname through DNS rebinding protection
    external_host = os.getenv("MCP_EXTERNAL_HOST")
    if external_host:
        mcp.settings.transport_security.allowed_hosts.append(external_host)
        mcp.settings.transport_security.allowed_origins.append(f"https://{external_host}")
        mcp.settings.transport_security.allowed_origins.append(f"http://{external_host}")

    mcp.run(transport="streamable-http")
