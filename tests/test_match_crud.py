"""Tests for match CRUD operations: submit, get, edit, delete."""

import db
from tests.factories import create_test_match, make_players


# ---------------------------------------------------------------------------
# scoreboard helpers
# ---------------------------------------------------------------------------


class TestStripBattletag:
    def test_strips_discriminator(self):
        from scoreboard import _strip_battletag

        assert _strip_battletag("player#12345") == "player"

    def test_no_discriminator(self):
        from scoreboard import _strip_battletag

        assert _strip_battletag("player") == "player"

    def test_empty_string(self):
        from scoreboard import _strip_battletag

        assert _strip_battletag("") == ""

    def test_multiple_hashes(self):
        from scoreboard import _strip_battletag

        assert _strip_battletag("play#er#123") == "play"


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
        assert len(self_player["heroes"]) == 1
        assert self_player["heroes"][0]["hero_name"] == "Ana"
        assert self_player["heroes"][0]["started_at"] == [0]
        assert len(self_player["heroes"][0]["values"]) == 2

    async def test_submit_multi_hero(self):
        from main import get_match

        players = make_players(
            self_heroes=[
                {"hero_name": "Reinhardt", "started_at": [0], "stats": [
                    {"label": "Charge Kills", "value": "3", "is_featured": False},
                ]},
                {"hero_name": "Moira", "started_at": [180], "stats": [
                    {"label": "Players Saved", "value": "17", "is_featured": True},
                ]},
            ],
        )
        match_id = await create_test_match(players=players)
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert len(self_player["heroes"]) == 2
        hero_names = [h["hero_name"] for h in self_player["heroes"]]
        assert "Reinhardt" in hero_names
        assert "Moira" in hero_names

    async def test_hero_timeline_computed(self):
        from main import get_match

        players = make_players(
            self_heroes=[
                {"hero_name": "Ana", "started_at": [0, 300], "stats": []},
                {"hero_name": "Moira", "started_at": [120], "stats": []},
            ],
        )
        match_id = await create_test_match(duration="10:00", players=players)
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert self_player["hero_timeline"] == [["Ana", 0], ["Moira", 120], ["Ana", 300]]
        assert self_player["starting_hero"] == "Ana"
        assert self_player["ending_hero"] == "Ana"

    async def test_primary_hero_calculation(self):
        from main import get_match

        # Ana: 0-120 (120s) + 300-600 (300s) = 420s
        # Moira: 120-300 (180s)
        # Ana played most
        players = make_players(
            self_heroes=[
                {"hero_name": "Ana", "started_at": [0, 300], "stats": []},
                {"hero_name": "Moira", "started_at": [120], "stats": []},
            ],
        )
        match_id = await create_test_match(duration="10:00", players=players)
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert self_player["primary_hero"] == "Ana"

    async def test_no_heroes_array_means_no_hero_stats(self):
        """Player without heroes array gets empty heroes list."""
        from main import submit_match, get_match

        players = make_players()
        # Remove heroes from self player
        for p in players:
            p.pop("heroes", None)
        match_result = await submit_match(
            map_name="Dorado", duration="10:00", mode="ESCORT",
            queue_type="COMPETITIVE", result="VICTORY", players=players,
        )
        match = await get_match(match_result["match_id"])
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert self_player["heroes"] == []

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

    async def test_stores_player_title(self):
        from main import get_match

        players = make_players()
        players[0]["title"] = "Champion"
        players[1]["title"] = "Gold"
        match_id = await create_test_match(players=players)
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert self_player["title"] == "Champion"
        ally1 = next(p for p in match["player_stats"] if p["player_name"] == "Ally1")
        assert ally1["title"] == "Gold"

    async def test_title_defaults_to_none(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        for ps in match["player_stats"]:
            assert ps["title"] is None

    async def test_stores_player_hero(self):
        from main import get_match

        players = make_players()
        players[1]["hero_name"] = "Reinhardt"
        players[5]["hero_name"] = "genji"  # case-insensitive input
        match_id = await create_test_match(players=players)
        match = await get_match(match_id)
        ally1 = next(p for p in match["player_stats"] if p["player_name"] == "Ally1")
        assert ally1["hero"] == "Reinhardt"
        enemy1 = next(p for p in match["player_stats"] if p["player_name"] == "Enemy1")
        assert enemy1["hero"] == "Genji"  # normalized from lowercase input

    async def test_hero_auto_populated_from_hero_dict(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        # Self player has hero dict with hero_name="Ana", should auto-populate hero field
        assert self_player["hero"] == "Ana"

    async def test_hero_defaults_to_none(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        # Non-self players without hero_name should have None
        non_self = [p for p in match["player_stats"] if not p["is_self"]]
        for ps in non_self:
            assert ps["hero"] is None
            assert ps["heroes"] == []
            assert ps["hero_timeline"] == []
            assert ps["primary_hero"] is None
            assert ps["starting_hero"] is None
            assert ps["ending_hero"] is None

    async def test_joined_at_defaults_to_zero(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        for ps in match["player_stats"]:
            assert ps["joined_at"] == 0

    async def test_joined_at_set_on_submit(self):
        from main import get_match

        players = make_players()
        players[1]["joined_at"] = 300  # joined 5 minutes in
        match_id = await create_test_match(players=players)
        match = await get_match(match_id)
        ally1 = next(p for p in match["player_stats"] if p["player_name"] == "Ally1")
        assert ally1["joined_at"] == 300
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert self_player["joined_at"] == 0

    async def test_in_party_defaults_to_false(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        for ps in match["player_stats"]:
            assert ps["in_party"] is False

    async def test_in_party_set_on_submit(self):
        from main import get_match

        players = make_players()
        players[1]["in_party"] = True
        match_id = await create_test_match(players=players)
        match = await get_match(match_id)
        ally1 = next(p for p in match["player_stats"] if p["player_name"] == "Ally1")
        assert ally1["in_party"] is True
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert self_player["in_party"] is False

    async def test_banned_heroes_on_submit(self):
        from main import get_match

        match_id = await create_test_match(banned_heroes=["Ana", "Reinhardt"])
        match = await get_match(match_id)
        assert match["banned_heroes"] == ["Ana", "Reinhardt"]

    async def test_banned_heroes_defaults_to_none(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        assert match["banned_heroes"] is None

    async def test_banned_heroes_fuzzy_matches(self):
        from main import get_match

        match_id = await create_test_match(banned_heroes=["ana", "Brigite"])
        match = await get_match(match_id)
        assert match["banned_heroes"] == ["Ana", "Brigitte"]

    async def test_banned_heroes_rejects_unknown(self):
        from main import submit_match

        result = await submit_match(
            map_name="Lijiang Tower",
            duration="10:00",
            mode="CONTROL",
            queue_type="COMPETITIVE",
            result="VICTORY",
            players=make_players(),
            banned_heroes=["NotAHero"],
        )
        assert "error" in result
        assert "banned hero" in result["error"].lower()

    async def test_swap_snapshots_on_submit(self):
        from main import get_match

        snapshots = [
            {"time": 0, "eliminations": 0, "assists": 0, "deaths": 0, "damage": 0, "healing": 0, "mitigation": 0},
            {"time": 120, "eliminations": 5, "assists": 3, "deaths": 1, "damage": 3000, "healing": 8000, "mitigation": 0},
            {"time": 400, "eliminations": 15, "assists": 20, "deaths": 5, "damage": 5000, "healing": 12000, "mitigation": 0},
            {"time": 600, "eliminations": 16, "assists": 21, "deaths": 7, "damage": 5500, "healing": 13000, "mitigation": 0},
        ]
        players = make_players(
            self_heroes=[
                {"hero_name": "Ana", "started_at": [0, 400], "stats": []},
                {"hero_name": "Moira", "started_at": [120], "stats": []},
            ],
        )
        players[0]["swap_snapshots"] = snapshots
        match_id = await create_test_match(duration="10:00", players=players)
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert self_player["swap_snapshots"] == snapshots
        assert "hero_segments" in self_player
        segments = self_player["hero_segments"]
        assert len(segments) == 3
        # First segment: Ana 0-120
        assert segments[0]["hero"] == "Ana"
        assert segments[0]["from"] == 0
        assert segments[0]["to"] == 120
        assert segments[0]["duration"] == "2:00"
        assert segments[0]["eliminations"] == 5
        # Second segment: Moira 120-400
        assert segments[1]["hero"] == "Moira"
        assert segments[1]["eliminations"] == 10
        # Third segment: Ana 400-600
        assert segments[2]["hero"] == "Ana"
        assert segments[2]["eliminations"] == 1
        assert segments[2]["deaths"] == 2

    async def test_swap_snapshots_absent_means_no_segments(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert "hero_segments" not in self_player
        assert "swap_snapshots" not in self_player

    async def test_with_rank_fields(self):
        from main import get_match

        match_id = await create_test_match(
            rank_min="Gold 3", rank_max="Diamond 1", is_wide_match=True
        )
        match = await get_match(match_id)
        assert match["rank_min"] == "Gold 3"
        assert match["rank_max"] == "Diamond 1"
        assert match["is_wide_match"] is True

    async def test_rank_fields_default_to_none(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        assert match["rank_min"] is None
        assert match["rank_max"] is None
        assert match["is_wide_match"] is None

    async def test_partial_rank_fields(self):
        from main import get_match

        match_id = await create_test_match(rank_min="Silver 2")
        match = await get_match(match_id)
        assert match["rank_min"] == "Silver 2"
        assert match["rank_max"] is None
        assert match["is_wide_match"] is None

    async def test_initial_team_side_on_submit(self):
        from main import get_match

        match_id = await create_test_match(initial_team_side="ATTACK")
        match = await get_match(match_id)
        assert match["initial_team_side"] == "ATTACK"

    async def test_initial_team_side_defaults_to_none(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        assert match["initial_team_side"] is None

    async def test_initial_team_side_uppercased(self):
        from main import get_match

        match_id = await create_test_match(initial_team_side="defend")
        match = await get_match(match_id)
        assert match["initial_team_side"] == "DEFEND"

    async def test_score_progression_on_submit(self):
        from main import get_match

        match_id = await create_test_match(score_progression=["1:0", "1:1", "2:1"])
        match = await get_match(match_id)
        assert match["score_progression"] == ["1:0", "1:1", "2:1"]
        assert match["final_score"] == "2:1"

    async def test_score_progression_defaults_to_none(self):
        from main import get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        assert match["score_progression"] is None
        assert match["final_score"] is None

    async def test_normalizes_map_name(self):
        from main import get_match

        match_id = await create_test_match(map_name="lijiang tower")
        match = await get_match(match_id)
        assert match["map_name"] == "Lijiang Tower"

    async def test_strips_map_parenthetical(self):
        from main import get_match

        match_id = await create_test_match(map_name="Lijiang Tower (Lunar New Year)")
        match = await get_match(match_id)
        assert match["map_name"] == "Lijiang Tower"

    async def test_fuzzy_matches_map_name(self):
        from main import get_match

        match_id = await create_test_match(map_name="Lijang Tower")  # typo
        match = await get_match(match_id)
        assert match["map_name"] == "Lijiang Tower"

    async def test_rejects_unknown_map(self):
        from main import submit_match

        result = await submit_match(
            map_name="Totally Fake Map",
            duration="10:00",
            mode="CONTROL",
            queue_type="COMPETITIVE",
            result="VICTORY",
            players=make_players(),
        )
        assert "error" in result
        assert "map name" in result["error"].lower()

    async def test_normalizes_hero_names(self):
        from main import get_match

        players = make_players(
            self_heroes=[
                {"hero_name": "ana", "started_at": [0], "stats": []},
            ],
        )
        players[1]["hero_name"] = "reinhardt"
        match_id = await create_test_match(players=players)
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert self_player["heroes"][0]["hero_name"] == "Ana"
        assert self_player["hero"] == "Ana"
        ally1 = next(p for p in match["player_stats"] if p["player_name"] == "Ally1")
        assert ally1["hero"] == "Reinhardt"

    async def test_fuzzy_matches_hero_name(self):
        from main import get_match

        players = make_players(
            self_heroes=[
                {"hero_name": "Brigite", "started_at": [0], "stats": []},  # typo
            ],
        )
        match_id = await create_test_match(players=players)
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert self_player["heroes"][0]["hero_name"] == "Brigitte"

    async def test_rejects_garbage_hero_name(self):
        from main import submit_match

        players = make_players()
        players[1]["hero_name"] = "Juno Does This Help? \\ Ukie (Anran"
        result = await submit_match(
            map_name="Lijiang Tower",
            duration="10:00",
            mode="CONTROL",
            queue_type="COMPETITIVE",
            result="VICTORY",
            players=players,
        )
        assert "error" in result
        assert "hero name" in result["error"].lower()

    async def test_rejects_garbage_hero_in_heroes_list(self):
        from main import submit_match

        players = make_players(
            self_heroes=[
                {"hero_name": "Not A Real Hero At All", "started_at": [0], "stats": []},
            ],
        )
        result = await submit_match(
            map_name="Lijiang Tower",
            duration="10:00",
            mode="CONTROL",
            queue_type="COMPETITIVE",
            result="VICTORY",
            players=players,
        )
        assert "error" in result
        assert "hero name" in result["error"].lower()


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
            "scoreboard_url",
            "hero_stats_url",
            "rank_min",
            "rank_max",
            "is_wide_match",
            "banned_heroes",
            "initial_team_side",
            "score_progression",
            "final_score",
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

    async def test_edit_player_name(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        ps = match["player_stats"][0]
        await edit_match(
            match_id,
            player_edits=[{"player_stat_id": ps["id"], "player_name": "NewName"}],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == ps["id"])
        assert updated["player_name"] == "NewName"

    async def test_edit_player_stats(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        ps = match["player_stats"][0]
        await edit_match(
            match_id,
            player_edits=[{
                "player_stat_id": ps["id"],
                "team": "enemy",
                "role": "tank",
                "eliminations": 99,
                "deaths": 1,
            }],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == ps["id"])
        assert updated["team"] == "ENEMY"
        assert updated["role"] == "TANK"
        assert updated["eliminations"] == 99
        assert updated["deaths"] == 1

    async def test_edit_player_replace_heroes_single(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        assert self_player["heroes"][0]["hero_name"] == "Ana"
        await edit_match(
            match_id,
            player_edits=[{
                "player_stat_id": self_player["id"],
                "heroes": [{"hero_name": "Mercy", "started_at": [0], "stats": []}],
            }],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == self_player["id"])
        assert len(updated["heroes"]) == 1
        assert updated["heroes"][0]["hero_name"] == "Mercy"

    async def test_edit_player_clear_heroes(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        await edit_match(
            match_id,
            player_edits=[{
                "player_stat_id": self_player["id"],
                "heroes": [],
            }],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == self_player["id"])
        assert updated["heroes"] == []

    async def test_edit_player_add_heroes_to_player_without(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        no_hero = next(
            p for p in match["player_stats"]
            if not p["is_self"] and p["heroes"] == []
        )
        await edit_match(
            match_id,
            player_edits=[{
                "player_stat_id": no_hero["id"],
                "heroes": [{"hero_name": "Genji", "started_at": [0], "stats": []}],
            }],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == no_hero["id"])
        assert len(updated["heroes"]) == 1
        assert updated["heroes"][0]["hero_name"] == "Genji"

    async def test_edit_player_title(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        ps = match["player_stats"][0]
        await edit_match(
            match_id,
            player_edits=[{"player_stat_id": ps["id"], "title": "Grandmaster"}],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == ps["id"])
        assert updated["title"] == "Grandmaster"

    async def test_edit_player_clear_title(self):
        from main import edit_match, get_match

        players = make_players()
        players[0]["title"] = "Champion"
        match_id = await create_test_match(players=players)
        match = await get_match(match_id)
        ps = next(p for p in match["player_stats"] if p["is_self"])
        assert ps["title"] == "Champion"
        await edit_match(
            match_id,
            player_edits=[{"player_stat_id": ps["id"], "title": ""}],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == ps["id"])
        assert updated["title"] is None

    async def test_edit_player_hero(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        ps = match["player_stats"][1]  # non-self player
        await edit_match(
            match_id,
            player_edits=[{"player_stat_id": ps["id"], "hero": "Mercy"}],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == ps["id"])
        assert updated["hero"] == "Mercy"

    async def test_edit_player_clear_hero_field(self):
        from main import edit_match, get_match

        players = make_players()
        players[1]["hero_name"] = "Genji"
        match_id = await create_test_match(players=players)
        match = await get_match(match_id)
        ps = next(p for p in match["player_stats"] if p["player_name"] == "Ally1")
        assert ps["hero"] == "Genji"
        await edit_match(
            match_id,
            player_edits=[{"player_stat_id": ps["id"], "hero": ""}],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == ps["id"])
        assert updated["hero"] is None

    async def test_edit_player_replace_heroes(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        await edit_match(
            match_id,
            player_edits=[{
                "player_stat_id": self_player["id"],
                "heroes": [
                    {"hero_name": "Mercy", "started_at": [0], "stats": []},
                    {"hero_name": "Lucio", "started_at": [200], "stats": [
                        {"label": "Sound Barriers", "value": "4", "is_featured": True},
                    ]},
                ],
            }],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == self_player["id"])
        assert len(updated["heroes"]) == 2
        hero_names = {h["hero_name"] for h in updated["heroes"]}
        assert hero_names == {"Mercy", "Lucio"}
        lucio = next(h for h in updated["heroes"] if h["hero_name"] == "Lucio")
        assert len(lucio["values"]) == 1

    async def test_edit_player_joined_at(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        ps = match["player_stats"][1]
        assert ps["joined_at"] == 0
        await edit_match(
            match_id,
            player_edits=[{"player_stat_id": ps["id"], "joined_at": 450}],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == ps["id"])
        assert updated["joined_at"] == 450

    async def test_edit_player_unknown_id_ignored(self):
        from main import edit_match

        match_id = await create_test_match()
        result = await edit_match(
            match_id,
            player_edits=[{
                "player_stat_id": "00000000-0000-0000-0000-000000000000",
                "player_name": "Ghost",
            }],
        )
        assert result == {"updated": True}

    async def test_edit_rank_fields(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        await edit_match(
            match_id, rank_min="Gold 3", rank_max="Diamond 1", is_wide_match=True
        )
        match = await get_match(match_id)
        assert match["rank_min"] == "Gold 3"
        assert match["rank_max"] == "Diamond 1"
        assert match["is_wide_match"] is True

    async def test_clear_rank_fields(self):
        from main import edit_match, get_match

        match_id = await create_test_match(
            rank_min="Gold 3", rank_max="Diamond 1", is_wide_match=True
        )
        await edit_match(match_id, rank_min="", rank_max="")
        match = await get_match(match_id)
        assert match["rank_min"] is None
        assert match["rank_max"] is None
        assert match["is_wide_match"] is True

    async def test_edit_rank_preserves_other_fields(self):
        from main import edit_match, get_match

        match_id = await create_test_match(
            map_name="Dorado", notes="Good game"
        )
        await edit_match(match_id, rank_min="Platinum 5")
        match = await get_match(match_id)
        assert match["rank_min"] == "Platinum 5"
        assert match["map_name"] == "Dorado"
        assert match["notes"] == "Good game"

    async def test_edit_normalizes_map_name(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        await edit_match(match_id, map_name="dorado")
        match = await get_match(match_id)
        assert match["map_name"] == "Dorado"

    async def test_edit_rejects_unknown_map(self):
        from main import edit_match

        match_id = await create_test_match()
        result = await edit_match(match_id, map_name="Totally Fake Map")
        assert "error" in result
        assert "map name" in result["error"].lower()

    async def test_edit_normalizes_hero_in_player_edits(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        ps = match["player_stats"][1]
        await edit_match(
            match_id,
            player_edits=[{"player_stat_id": ps["id"], "hero": "mercy"}],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == ps["id"])
        assert updated["hero"] == "Mercy"

    async def test_edit_rejects_unknown_hero_in_player_edits(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        ps = match["player_stats"][0]
        result = await edit_match(
            match_id,
            player_edits=[{"player_stat_id": ps["id"], "hero": "Not A Real Hero"}],
        )
        assert "error" in result
        assert "hero name" in result["error"].lower()

    async def test_edit_normalizes_hero_in_heroes_replacement(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        await edit_match(
            match_id,
            player_edits=[{
                "player_stat_id": self_player["id"],
                "heroes": [{"hero_name": "lucio", "started_at": [0], "stats": []}],
            }],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == self_player["id"])
        assert updated["heroes"][0]["hero_name"] == "Lucio"

    async def test_edit_in_party(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        ally1 = next(p for p in match["player_stats"] if p["player_name"] == "Ally1")
        assert ally1["in_party"] is False
        await edit_match(
            match_id,
            player_edits=[{"player_stat_id": ally1["id"], "in_party": True}],
        )
        match = await get_match(match_id)
        ally1 = next(p for p in match["player_stats"] if p["player_name"] == "Ally1")
        assert ally1["in_party"] is True

    async def test_edit_banned_heroes(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        await edit_match(match_id, banned_heroes=["Ana", "Genji"])
        match = await get_match(match_id)
        assert match["banned_heroes"] == ["Ana", "Genji"]

    async def test_edit_clear_banned_heroes(self):
        from main import edit_match, get_match

        match_id = await create_test_match(banned_heroes=["Ana"])
        await edit_match(match_id, banned_heroes=[])
        match = await get_match(match_id)
        assert match["banned_heroes"] is None

    async def test_edit_banned_heroes_rejects_unknown(self):
        from main import edit_match

        match_id = await create_test_match()
        result = await edit_match(match_id, banned_heroes=["FakeHero"])
        assert "error" in result
        assert "banned hero" in result["error"].lower()

    async def test_edit_initial_team_side(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        await edit_match(match_id, initial_team_side="DEFEND")
        match = await get_match(match_id)
        assert match["initial_team_side"] == "DEFEND"

    async def test_edit_clear_initial_team_side(self):
        from main import edit_match, get_match

        match_id = await create_test_match(initial_team_side="ATTACK")
        await edit_match(match_id, initial_team_side="")
        match = await get_match(match_id)
        assert match["initial_team_side"] is None

    async def test_edit_score_progression(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        await edit_match(match_id, score_progression=["0:1", "1:1"])
        match = await get_match(match_id)
        assert match["score_progression"] == ["0:1", "1:1"]
        assert match["final_score"] == "1:1"

    async def test_edit_clear_score_progression(self):
        from main import edit_match, get_match

        match_id = await create_test_match(score_progression=["1:0"])
        await edit_match(match_id, score_progression=[])
        match = await get_match(match_id)
        assert match["score_progression"] is None
        assert match["final_score"] is None

    async def test_edit_swap_snapshots(self):
        from main import edit_match, get_match

        match_id = await create_test_match()
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        snapshots = [
            {"time": 0, "eliminations": 0, "assists": 0, "deaths": 0, "damage": 0, "healing": 0, "mitigation": 0},
            {"time": 750, "eliminations": 15, "assists": 20, "deaths": 5, "damage": 5000, "healing": 12000, "mitigation": 0},
        ]
        await edit_match(
            match_id,
            player_edits=[{"player_stat_id": self_player["id"], "swap_snapshots": snapshots}],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == self_player["id"])
        assert updated["swap_snapshots"] == snapshots

    async def test_edit_clear_swap_snapshots(self):
        from main import edit_match, get_match

        players = make_players()
        players[0]["swap_snapshots"] = [
            {"time": 0, "eliminations": 0, "assists": 0, "deaths": 0, "damage": 0, "healing": 0, "mitigation": 0},
        ]
        match_id = await create_test_match(players=players)
        match = await get_match(match_id)
        self_player = next(p for p in match["player_stats"] if p["is_self"])
        await edit_match(
            match_id,
            player_edits=[{"player_stat_id": self_player["id"], "swap_snapshots": []}],
        )
        match = await get_match(match_id)
        updated = next(p for p in match["player_stats"] if p["id"] == self_player["id"])
        assert "swap_snapshots" not in updated


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
