"""Export all matches and player notes to JSON for backup before multi-user migration.

Usage:
    python scripts/export_data.py -o backup.json
    python scripts/export_data.py  # prints to stdout
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add src to path so we can import the app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select
from sqlalchemy.orm import joinedload

import db
from models import Match, PlayerNote, PlayerStat


async def export_all() -> dict:
    """Export all matches (with nested data) and player notes."""
    async with db.async_session() as session:
        # Load matches with all nested relationships
        matches_result = await session.execute(
            select(Match)
            .options(
                joinedload(Match.player_stats)
                .joinedload(PlayerStat.hero_stats),
                joinedload(Match.screenshots),
                joinedload(Match.rank_update),
                joinedload(Match.files),
            )
            .order_by(Match.created_at)
        )
        matches = matches_result.unique().scalars().all()

        # Need a second pass for hero_stat_values since we can't triple-nest joinedload easily
        for match in matches:
            for ps in match.player_stats:
                for hs in ps.hero_stats:
                    await session.refresh(hs, ["values"])

        exported_matches = []
        for m in matches:
            match_data = {
                "id": str(m.id),
                "map_name": m.map_name,
                "duration": m.duration,
                "mode": m.mode,
                "queue_type": m.queue_type,
                "result": m.result,
                "played_at": m.played_at.isoformat() if m.played_at else None,
                "notes": m.notes,
                "is_backfill": m.is_backfill,
                "source": m.source,
                "scoreboard_url": m.scoreboard_url,
                "hero_stats_url": m.hero_stats_url,
                "rank_min": m.rank_min,
                "rank_max": m.rank_max,
                "is_wide_match": m.is_wide_match,
                "banned_heroes": m.banned_heroes,
                "initial_team_side": m.initial_team_side,
                "score_progression": m.score_progression,
                "has_attachments": m.has_attachments,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "player_stats": [],
                "screenshots": [],
                "rank_update": None,
                "files": [],
            }

            for ps in m.player_stats:
                ps_data = {
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
                    "in_party": ps.in_party,
                    "joined_at": ps.joined_at,
                    "title": ps.title,
                    "hero": ps.hero,
                    "swap_snapshots": ps.swap_snapshots,
                    "hero_stats": [],
                }
                for hs in ps.hero_stats:
                    hs_data = {
                        "hero_name": hs.hero_name,
                        "started_at": hs.started_at,
                        "values": [
                            {
                                "label": v.label,
                                "value": v.value,
                                "is_featured": v.is_featured,
                            }
                            for v in hs.values
                        ],
                    }
                    ps_data["hero_stats"].append(hs_data)
                match_data["player_stats"].append(ps_data)

            for sc in m.screenshots:
                match_data["screenshots"].append({
                    "url": sc.url,
                    "created_at": sc.created_at.isoformat() if sc.created_at else None,
                })

            if m.rank_update:
                ru = m.rank_update
                match_data["rank_update"] = {
                    "rank": ru.rank,
                    "division": ru.division,
                    "progress_pct": ru.progress_pct,
                    "delta_pct": ru.delta_pct,
                    "demotion_protection": ru.demotion_protection,
                    "modifiers": ru.modifiers,
                }

            for f in m.files:
                match_data["files"].append({
                    "filename": f.filename,
                    "size": f.size,
                    "tus_id": f.tus_id,
                    "uploaded_at": f.uploaded_at.isoformat() if f.uploaded_at else None,
                })

            exported_matches.append(match_data)

        # Export player notes
        notes_result = await session.execute(
            select(PlayerNote).order_by(PlayerNote.player_name)
        )
        notes = notes_result.scalars().all()
        exported_notes = [
            {
                "player_name": n.player_name,
                "note": n.note,
                "updated_at": n.updated_at.isoformat() if n.updated_at else None,
            }
            for n in notes
        ]

    return {
        "matches": exported_matches,
        "player_notes": exported_notes,
    }


def main():
    parser = argparse.ArgumentParser(description="Export Overwatch stats data to JSON")
    parser.add_argument("-o", "--output", help="Output file path (default: stdout)")
    args = parser.parse_args()

    data = asyncio.run(export_all())

    output = json.dumps(data, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Exported {len(data['matches'])} matches and {len(data['player_notes'])} player notes to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
