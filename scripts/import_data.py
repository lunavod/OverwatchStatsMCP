"""Import previously exported matches and player notes, assigning them to a user.

Usage:
    python scripts/import_data.py user@gmail.com backup.json
"""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

# Add src to path so we can import the app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select

import db
from models import (
    HeroStat,
    HeroStatValue,
    Match,
    MatchFile,
    PlayerNote,
    PlayerStat,
    RankUpdate,
    Screenshot,
)


async def find_user_by_email(email: str):
    """Look up a user by email. Returns the User or None."""
    # Import here because the User model won't exist until after migration
    from models import User

    async with db.async_session() as session:
        result = await session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()


async def import_data(user_id: uuid.UUID, data: dict) -> tuple[int, int]:
    """Import matches and player notes, assigning them to the given user.

    Returns (match_count, note_count).
    """
    matches = data.get("matches", [])
    notes = data.get("player_notes", [])

    async with db.async_session() as session:
        async with session.begin():
            for m in matches:
                match = Match(
                    id=uuid.UUID(m["id"]) if m.get("id") else uuid.uuid4(),
                    user_id=user_id,
                    map_name=m["map_name"],
                    duration=m["duration"],
                    mode=m["mode"],
                    queue_type=m["queue_type"],
                    result=m["result"],
                    played_at=datetime.fromisoformat(m["played_at"]) if m.get("played_at") else None,
                    notes=m.get("notes"),
                    is_backfill=m.get("is_backfill", False),
                    source=m.get("source", ""),
                    scoreboard_url=m.get("scoreboard_url"),
                    hero_stats_url=m.get("hero_stats_url"),
                    rank_min=m.get("rank_min"),
                    rank_max=m.get("rank_max"),
                    is_wide_match=m.get("is_wide_match"),
                    banned_heroes=m.get("banned_heroes"),
                    initial_team_side=m.get("initial_team_side"),
                    score_progression=m.get("score_progression"),
                    has_attachments=m.get("has_attachments", False),
                )
                session.add(match)

                for ps_data in m.get("player_stats", []):
                    ps = PlayerStat(
                        match_id=match.id,
                        team=ps_data["team"],
                        role=ps_data["role"],
                        player_name=ps_data["player_name"],
                        eliminations=ps_data.get("eliminations"),
                        assists=ps_data.get("assists"),
                        deaths=ps_data.get("deaths"),
                        damage=ps_data.get("damage"),
                        healing=ps_data.get("healing"),
                        mitigation=ps_data.get("mitigation"),
                        is_self=ps_data.get("is_self", False),
                        in_party=ps_data.get("in_party", False),
                        joined_at=ps_data.get("joined_at", 0),
                        title=ps_data.get("title"),
                        hero=ps_data.get("hero"),
                        swap_snapshots=ps_data.get("swap_snapshots"),
                    )
                    session.add(ps)

                    for hs_data in ps_data.get("hero_stats", []):
                        hs = HeroStat(
                            player_stat_id=ps.id,
                            hero_name=hs_data["hero_name"],
                            started_at=hs_data.get("started_at"),
                        )
                        session.add(hs)

                        for v_data in hs_data.get("values", []):
                            session.add(HeroStatValue(
                                hero_stat_id=hs.id,
                                label=v_data["label"],
                                value=v_data["value"],
                                is_featured=v_data.get("is_featured", False),
                            ))

                for sc_data in m.get("screenshots", []):
                    session.add(Screenshot(
                        match_id=match.id,
                        url=sc_data["url"],
                    ))

                if m.get("rank_update"):
                    ru = m["rank_update"]
                    session.add(RankUpdate(
                        match_id=match.id,
                        rank=ru["rank"],
                        division=ru["division"],
                        progress_pct=ru["progress_pct"],
                        delta_pct=ru["delta_pct"],
                        demotion_protection=ru.get("demotion_protection", False),
                        modifiers=ru.get("modifiers"),
                    ))

                for f_data in m.get("files", []):
                    session.add(MatchFile(
                        match_id=match.id,
                        filename=f_data["filename"],
                        size=f_data["size"],
                        tus_id=f_data["tus_id"],
                    ))

            for n in notes:
                # Check if note already exists for this user+player
                existing = (await session.execute(
                    select(PlayerNote).where(
                        PlayerNote.user_id == user_id,
                        PlayerNote.player_name == n["player_name"],
                    )
                )).scalar_one_or_none()

                if existing:
                    existing.note = n["note"]
                else:
                    session.add(PlayerNote(
                        user_id=user_id,
                        player_name=n["player_name"],
                        note=n["note"],
                    ))

    return len(matches), len(notes)


def main():
    parser = argparse.ArgumentParser(description="Import Overwatch stats data from JSON")
    parser.add_argument("email", help="Email of the user to assign data to")
    parser.add_argument("file", help="JSON file to import")
    args = parser.parse_args()

    data = json.loads(Path(args.file).read_text(encoding="utf-8"))

    user = asyncio.run(find_user_by_email(args.email))
    if user is None:
        print(f"Error: No user found with email '{args.email}'. Register first, then import.", file=sys.stderr)
        sys.exit(1)

    match_count, note_count = asyncio.run(import_data(user.id, data))
    print(f"Imported {match_count} matches and {note_count} player notes for {args.email}", file=sys.stderr)


if __name__ == "__main__":
    main()
