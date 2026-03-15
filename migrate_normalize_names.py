"""Normalize existing hero and map names to canonical forms.

Applies fuzzy matching to all hero/map names already in the database,
updating them to canonical casing from heroes.txt / maps.txt.
Rows that cannot be matched are skipped (never deleted) and reported.

Usage:
    uv run python migrate_normalize_names.py              # dry-run (default)
    uv run python migrate_normalize_names.py --apply      # actually write changes

Reads DATABASE_URL from .env or environment, same as the MCP server.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Add src/ to path so we can import application modules
sys.path.insert(0, str(Path(__file__).parent / "src"))

from main import normalize_hero_name, normalize_map_name  # noqa: E402
from db import async_session  # noqa: E402

from sqlalchemy import text  # noqa: E402


async def run(*, apply: bool) -> None:
    updated_maps: list[tuple[str, str]] = []
    skipped_maps: list[str] = []
    updated_heroes: list[tuple[str, str, str]] = []  # (table, old, new)
    skipped_heroes: list[tuple[str, str]] = []  # (table, old)

    async with async_session() as session:
        # ---- Map names (matches.map_name) ----
        rows = (await session.execute(
            text("SELECT DISTINCT map_name FROM matches")
        )).fetchall()

        for (raw,) in rows:
            canonical = normalize_map_name(raw)
            if canonical is None:
                skipped_maps.append(raw)
            elif canonical != raw:
                updated_maps.append((raw, canonical))

        # ---- Hero names: player_stats.hero ----
        rows = (await session.execute(
            text("SELECT DISTINCT hero FROM player_stats WHERE hero IS NOT NULL")
        )).fetchall()

        for (raw,) in rows:
            canonical = normalize_hero_name(raw)
            if canonical is None:
                skipped_heroes.append(("player_stats.hero", raw))
            elif canonical != raw:
                updated_heroes.append(("player_stats.hero", raw, canonical))

        # ---- Hero names: hero_stats.hero_name ----
        rows = (await session.execute(
            text("SELECT DISTINCT hero_name FROM hero_stats")
        )).fetchall()

        for (raw,) in rows:
            canonical = normalize_hero_name(raw)
            if canonical is None:
                skipped_heroes.append(("hero_stats.hero_name", raw))
            elif canonical != raw:
                updated_heroes.append(("hero_stats.hero_name", raw, canonical))

        # ---- Report ----
        print("=" * 60)
        print("MAP NAME NORMALIZATION")
        print("=" * 60)
        if updated_maps:
            for old, new in updated_maps:
                print(f"  {old!r:40s} -> {new!r}")
        else:
            print("  (no changes)")
        if skipped_maps:
            print()
            print("  SKIPPED (no match found):")
            for raw in skipped_maps:
                print(f"    {raw!r}")

        print()
        print("=" * 60)
        print("HERO NAME NORMALIZATION")
        print("=" * 60)
        if updated_heroes:
            for table, old, new in updated_heroes:
                print(f"  [{table}] {old!r:30s} -> {new!r}")
        else:
            print("  (no changes)")
        if skipped_heroes:
            print()
            print("  SKIPPED (no match found):")
            for table, raw in skipped_heroes:
                print(f"    [{table}] {raw!r}")

        total_changes = len(updated_maps) + len(updated_heroes)
        total_skipped = len(skipped_maps) + len(skipped_heroes)
        print()
        print(f"Total: {total_changes} update(s), {total_skipped} skip(s)")

        if not apply:
            print()
            print("DRY RUN — no changes written. Re-run with --apply to commit.")
            return

        if total_changes == 0:
            print("Nothing to update.")
            return

        # ---- Apply updates ----
        async with session.begin():
            for old, new in updated_maps:
                await session.execute(
                    text("UPDATE matches SET map_name = :new WHERE map_name = :old"),
                    {"old": old, "new": new},
                )

            for table, old, new in updated_heroes:
                if table == "player_stats.hero":
                    await session.execute(
                        text("UPDATE player_stats SET hero = :new WHERE hero = :old"),
                        {"old": old, "new": new},
                    )
                elif table == "hero_stats.hero_name":
                    await session.execute(
                        text("UPDATE hero_stats SET hero_name = :new WHERE hero_name = :old"),
                        {"old": old, "new": new},
                    )

        print()
        print(f"APPLIED {total_changes} update(s) successfully.")


def main():
    parser = argparse.ArgumentParser(
        description="Normalize hero and map names in the database"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes (default is dry-run)",
    )
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
