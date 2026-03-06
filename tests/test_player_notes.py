"""Tests for player notes CRUD and integration with match/teammate tools."""

from tests.factories import create_test_match


class TestPlayerNoteCRUD:
    async def test_set_and_get_note(self):
        from main import set_player_note, get_player_note

        await set_player_note("SomePlayer", "Toxic thrower")
        result = await get_player_note("SomePlayer")
        assert result == {"player_name": "SomePlayer", "note": "Toxic thrower"}

    async def test_update_existing_note(self):
        from main import set_player_note, get_player_note

        await set_player_note("Player1", "Good tank")
        await set_player_note("Player1", "Great tank")
        result = await get_player_note("Player1")
        assert result["note"] == "Great tank"

    async def test_delete_note_with_empty_string(self):
        from main import set_player_note, get_player_note

        await set_player_note("Player2", "Some note")
        result = await set_player_note("Player2", "")
        assert result == {"deleted": True}
        result = await get_player_note("Player2")
        assert result["note"] is None

    async def test_delete_nonexistent_note(self):
        from main import set_player_note

        result = await set_player_note("Ghost", "")
        assert result == {"deleted": False}

    async def test_get_nonexistent_note(self):
        from main import get_player_note

        result = await get_player_note("Nobody")
        assert result == {"player_name": "Nobody", "note": None}

    async def test_list_notes(self):
        from main import set_player_note, list_player_notes

        await set_player_note("Alice", "Friendly")
        await set_player_note("Bob", "Good healer")
        result = await list_player_notes()
        names = [n["player_name"] for n in result["notes"]]
        assert "Alice" in names
        assert "Bob" in names


class TestPlayerNotesInGetMatch:
    async def test_notes_included_in_get_match(self):
        from main import set_player_note, get_match

        match_id = await create_test_match()
        await set_player_note("Ally1", "Great teammate")
        match = await get_match(match_id)
        ally1 = next(p for p in match["player_stats"] if p["player_name"] == "Ally1")
        assert ally1["player_note"] == "Great teammate"

    async def test_no_note_returns_none(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        enemy = next(p for p in match["player_stats"] if p["player_name"] == "Enemy1")
        assert enemy["player_note"] is None


class TestPlayerNotesInTeammateStats:
    async def test_notes_included_in_teammate_stats(self):
        from main import set_player_note, get_teammate_stats

        await create_test_match()
        await set_player_note("Ally1", "Reliable")
        result = await get_teammate_stats()
        ally1 = next(t for t in result["teammates"] if t["player_name"] == "Ally1")
        assert ally1["player_note"] == "Reliable"


class TestPlayerNotesInPlayerHistory:
    async def test_notes_included_in_player_history(self):
        from main import set_player_note, get_match_player_history

        # Create two matches with overlapping players so history exists
        match_id1 = await create_test_match(played_at="2026-01-15T20:00:00")
        match_id2 = await create_test_match(played_at="2026-01-16T20:00:00")
        await set_player_note("Ally1", "Duo partner")
        result = await get_match_player_history(match_id2)
        # Ally1 should appear in players_with_history since they were in match 1
        ally1 = next(
            (p for p in result["players_with_history"] if p["player_name"] == "Ally1"),
            None,
        )
        assert ally1 is not None
        assert ally1["player_note"] == "Duo partner"

    async def test_notes_in_players_without_history(self):
        from main import set_player_note, get_match_player_history

        match_id = await create_test_match(
            players=[
                {
                    "team": "ALLY", "role": "SUPPORT", "player_name": "UniqueSolo",
                    "eliminations": 10, "assists": 5, "deaths": 3,
                    "damage": 3000, "healing": 8000, "mitigation": 0, "is_self": True,
                },
            ] + [
                {
                    "team": "ALLY", "role": "DPS", "player_name": f"UniqueAlly{i}",
                    "eliminations": 10, "assists": 5, "deaths": 3,
                    "damage": 5000, "healing": 0, "mitigation": 0, "is_self": False,
                }
                for i in range(4)
            ] + [
                {
                    "team": "ENEMY", "role": "DPS", "player_name": f"UniqueEnemy{i}",
                    "eliminations": 8, "assists": 4, "deaths": 5,
                    "damage": 4000, "healing": 0, "mitigation": 0, "is_self": False,
                }
                for i in range(5)
            ],
        )
        await set_player_note("UniqueAlly0", "First timer")
        result = await get_match_player_history(match_id)
        # No prior matches, so all should be in players_without_history
        ally0 = next(
            (p for p in result["players_without_history"]
             if p["player_name"] == "UniqueAlly0"),
            None,
        )
        assert ally0 is not None
        assert ally0["player_note"] == "First timer"
