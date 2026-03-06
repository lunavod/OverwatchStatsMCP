"""Tests for match CRUD operations: submit, get, edit, delete."""

import db
from tests.factories import create_test_match, make_players


# ---------------------------------------------------------------------------
# submit_match
# ---------------------------------------------------------------------------


class TestSubmitMatch:
    async def test_returns_match_id(self):
        match_id = await create_test_match()
        assert match_id  # non-empty UUID string

    async def test_stores_all_player_stats(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        assert len(match["player_stats"]) == 10
        allies = [p for p in match["player_stats"] if p["team"] == "ALLY"]
        enemies = [p for p in match["player_stats"] if p["team"] == "ENEMY"]
        assert len(allies) == 5
        assert len(enemies) == 5

    async def test_stores_hero_stats(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert self_player["hero_stat"] is not None
        assert self_player["hero_stat"]["hero_name"] == "Ana"
        assert len(self_player["hero_stat"]["values"]) == 2

    async def test_uppercases_mode_and_queue(self):
        from main import get_match

        match_id = await create_test_match(mode="push", queue_type="quickplay")
        match = await get_match(match_id)
        assert match["mode"] == "PUSH"
        assert match["queue_type"] == "QUICKPLAY"

    async def test_with_notes(self):
        from main import get_match

        match_id = await create_test_match(notes="Close game, clutch sleep dart")
        match = await get_match(match_id)
        assert match["notes"] == "Close game, clutch sleep dart"

    async def test_with_screenshots(self):
        from main import get_match

        urls = ["https://example.com/ss1.png", "https://example.com/ss2.png"]
        match_id = await create_test_match(screenshots=urls)
        match = await get_match(match_id)
        assert set(match["screenshots"]) == set(urls)

    async def test_with_backfill_flag(self):
        from main import get_match

        match_id = await create_test_match(is_backfill=True)
        match = await get_match(match_id)
        assert match["is_backfill"] is True

    async def test_defaults_notes_none_backfill_false_screenshots_empty(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        assert match["notes"] is None
        assert match["is_backfill"] is False
        assert match["screenshots"] == []

    async def test_no_played_at(self):
        from main import get_match

        match_id = await create_test_match(played_at=None)
        match = await get_match(match_id)
        assert match["played_at"] is None

    async def test_player_stat_values(self):
        from main import get_match

        players = make_players(
            self_stats={"eliminations": 42, "deaths": 3, "damage": 15000}
        )
        match_id = await create_test_match(players=players)
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert self_player["eliminations"] == 42
        assert self_player["deaths"] == 3
        assert self_player["damage"] == 15000


# ---------------------------------------------------------------------------
# get_match
# ---------------------------------------------------------------------------


class TestGetMatch:
    async def test_not_found(self):
        from main import get_match

        result = await get_match("00000000-0000-0000-0000-000000000000")
        assert result == {"error": "Match not found"}

    async def test_response_shape(self):
        from main import get_match

        match_id = await create_test_match(
            notes="note", screenshots=["https://example.com/s.png"], is_backfill=True
        )
        match = await get_match(match_id)
        expected_keys = {
            "id",
            "map_name",
            "duration",
            "mode",
            "queue_type",
            "result",
            "played_at",
            "created_at",
            "notes",
            "is_backfill",
            "source",
            "screenshots",
            "player_stats",
        }
        assert set(match.keys()) == expected_keys

    async def test_returns_correct_data(self):
        from main import get_match

        match_id = await create_test_match(
            map_name="Dorado", mode="ESCORT", result="DEFEAT", duration="08:45"
        )
        match = await get_match(match_id)
        assert match["map_name"] == "Dorado"
        assert match["mode"] == "ESCORT"
        assert match["result"] == "DEFEAT"
        assert match["duration"] == "08:45"


# ---------------------------------------------------------------------------
# edit_match
# ---------------------------------------------------------------------------


class TestEditMatch:
    async def test_update_basic_fields(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        await edit_match(match_id, map_name="Dorado", mode="escort", result="defeat")
        match = await get_match(match_id)
        assert match["map_name"] == "Dorado"
        assert match["mode"] == "ESCORT"
        assert match["result"] == "DEFEAT"

    async def test_update_notes(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        await edit_match(match_id, notes="Great game")
        match = await get_match(match_id)
        assert match["notes"] == "Great game"

    async def test_clear_notes_with_empty_string(self):
        from main import edit_match, get_match

        match_id = await create_test_match(notes="Some notes")
        await edit_match(match_id, notes="")
        match = await get_match(match_id)
        assert match["notes"] is None

    async def test_set_backfill_flag(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        await edit_match(match_id, is_backfill=True)
        match = await get_match(match_id)
        assert match["is_backfill"] is True

    async def test_add_screenshots(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        await edit_match(
            match_id, screenshots_to_add=["https://example.com/new.png"]
        )
        match = await get_match(match_id)
        assert "https://example.com/new.png" in match["screenshots"]

    async def test_remove_screenshots(self):
        from main import edit_match, get_match

        urls = ["https://example.com/a.png", "https://example.com/b.png"]
        match_id = await create_test_match(screenshots=urls)
        await edit_match(
            match_id, screenshots_to_remove=["https://example.com/a.png"]
        )
        match = await get_match(match_id)
        assert "https://example.com/a.png" not in match["screenshots"]
        assert "https://example.com/b.png" in match["screenshots"]

    async def test_add_and_remove_screenshots_together(self):
        from main import edit_match, get_match

        match_id = await create_test_match(
            screenshots=["https://example.com/old.png"]
        )
        await edit_match(
            match_id,
            screenshots_to_add=["https://example.com/new.png"],
            screenshots_to_remove=["https://example.com/old.png"],
        )
        match = await get_match(match_id)
        assert match["screenshots"] == ["https://example.com/new.png"]

    async def test_partial_update_preserves_other_fields(self):
        from main import edit_match, get_match

        match_id = await create_test_match(
            map_name="Lijiang Tower", mode="CONTROL", result="VICTORY"
        )
        await edit_match(match_id, notes="Added note")
        match = await get_match(match_id)
        assert match["map_name"] == "Lijiang Tower"
        assert match["mode"] == "CONTROL"
        assert match["result"] == "VICTORY"
        assert match["notes"] == "Added note"

    async def test_not_found(self):
        from main import edit_match

        result = await edit_match(
            "00000000-0000-0000-0000-000000000000", notes="x"
        )
        assert result == {"error": "Match not found"}

    async def test_clear_played_at(self):
        from main import edit_match, get_match

        match_id = await create_test_match(played_at="2026-01-15T20:00:00")
        await edit_match(match_id, played_at="")
        match = await get_match(match_id)
        assert match["played_at"] is None

    async def test_returns_updated_true(self):
        from main import edit_match

        match_id = await create_test_match()
        result = await edit_match(match_id, notes="hi")
        assert result == {"updated": True}


# ---------------------------------------------------------------------------
# delete_match
# ---------------------------------------------------------------------------


class TestDeleteMatch:
    async def test_delete_existing(self):
        from main import delete_match, get_match

        match_id = await create_test_match()
        result = await delete_match(match_id)
        assert result["deleted"] is True
        assert (await get_match(match_id)) == {"error": "Match not found"}

    async def test_delete_nonexistent(self):
        from main import delete_match

        result = await delete_match("00000000-0000-0000-0000-000000000000")
        assert result["deleted"] is False

    async def test_cascade_deletes_player_stats(self):
        from sqlalchemy import func, select

        from main import delete_match
        from models import PlayerStat

        match_id = await create_test_match()
        await delete_match(match_id)

        async with db.async_session() as session:
            count = (
                await session.execute(
                    select(func.count()).select_from(PlayerStat)
                )
            ).scalar_one()
        assert count == 0

    async def test_cascade_deletes_screenshots(self):
        from sqlalchemy import func, select

        from main import delete_match
        from models import Screenshot

        match_id = await create_test_match(
            screenshots=["https://example.com/s.png"]
        )
        await delete_match(match_id)

        async with db.async_session() as session:
            count = (
                await session.execute(
                    select(func.count()).select_from(Screenshot)
                )
            ).scalar_one()
        assert count == 0

    async def test_cascade_deletes_hero_stats(self):
        from sqlalchemy import func, select

        from main import delete_match
        from models import HeroStat, HeroStatValue

        match_id = await create_test_match()
        await delete_match(match_id)

        async with db.async_session() as session:
            hs_count = (
                await session.execute(
                    select(func.count()).select_from(HeroStat)
                )
            ).scalar_one()
            hsv_count = (
                await session.execute(
                    select(func.count()).select_from(HeroStatValue)
                )
            ).scalar_one()
        assert hs_count == 0
        assert hsv_count == 0
