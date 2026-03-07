"""Tests for list_matches: filtering, sorting, and pagination."""

from tests.factories import create_test_match, make_players


async def _seed_matches():
    """Create a small dataset of varied matches. Returns list of match_ids."""
    ids = []

    ids.append(
        await create_test_match(
            map_name="Lijiang Tower",
            mode="CONTROL",
            queue_type="COMPETITIVE",
            result="VICTORY",
            duration="10:30",
            played_at="2026-01-10T18:00:00",
        )
    )
    ids.append(
        await create_test_match(
            map_name="Dorado",
            mode="ESCORT",
            queue_type="COMPETITIVE",
            result="DEFEAT",
            duration="15:00",
            played_at="2026-01-12T20:00:00",
            notes="Tough loss",
        )
    )
    ids.append(
        await create_test_match(
            map_name="King's Row",
            mode="HYBRID",
            queue_type="QUICKPLAY",
            result="VICTORY",
            duration="08:45",
            played_at="2026-01-14T22:00:00",
            is_backfill=True,
        )
    )
    ids.append(
        await create_test_match(
            map_name="Lijiang Tower",
            mode="CONTROL",
            queue_type="COMPETITIVE",
            result="VICTORY",
            duration="12:00",
            played_at="2026-01-16T19:00:00",
            players=make_players(
                self_role="DPS",
                self_hero={
                    "hero_name": "Soldier: 76",
                    "stats": [
                        {"label": "Helix Kills", "value": "5", "is_featured": True}
                    ],
                },
                self_stats={"eliminations": 30, "damage": 18000, "healing": 0},
            ),
        )
    )

    return ids


# ---------------------------------------------------------------------------
# Basic listing
# ---------------------------------------------------------------------------


class TestListMatchesBasic:
    async def test_returns_all(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches()
        assert result["total"] == 4
        assert len(result["matches"]) == 4

    async def test_default_order_is_newest_first(self):
        from main import list_matches

        ids = await _seed_matches()
        result = await list_matches()
        returned_ids = [m["id"] for m in result["matches"]]
        # ids[3] was created last (newest created_at)
        assert returned_ids[0] == ids[3]

    async def test_includes_notes_and_backfill(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches()
        for m in result["matches"]:
            assert "notes" in m
            assert "is_backfill" in m


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class TestListMatchesFilters:
    async def test_filter_by_map(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(map_name="Lijiang Tower")
        assert result["total"] == 2
        assert all(m["map_name"] == "Lijiang Tower" for m in result["matches"])

    async def test_filter_by_mode(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(mode="ESCORT")
        assert result["total"] == 1
        assert result["matches"][0]["mode"] == "ESCORT"

    async def test_filter_by_queue_type(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(queue_type="QUICKPLAY")
        assert result["total"] == 1
        assert result["matches"][0]["queue_type"] == "QUICKPLAY"

    async def test_filter_by_result(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(result="DEFEAT")
        assert result["total"] == 1
        assert result["matches"][0]["result"] == "DEFEAT"

    async def test_filter_by_date_range(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(
            from_date="2026-01-11T00:00:00", to_date="2026-01-15T00:00:00"
        )
        assert result["total"] == 2  # Dorado (Jan 12) and King's Row (Jan 14)

    async def test_filter_by_hero(self):
        from main import list_matches

        await _seed_matches()
        # Match 4 has Soldier: 76 as self hero
        result = await list_matches(hero_name="Soldier: 76")
        assert result["total"] == 1

    async def test_filter_by_hero_case_insensitive(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(hero_name="ana")
        # Matches 1, 2, 3 use default Ana hero
        assert result["total"] == 3

    async def test_combined_filters(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(
            queue_type="COMPETITIVE", result="VICTORY", map_name="Lijiang Tower"
        )
        assert result["total"] == 2

    async def test_filter_by_player_name(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(player_name="Ally1")
        # All seeded matches use make_players() which includes "Ally1"
        assert result["total"] > 0

    async def test_filter_by_player_name_case_insensitive(self):
        from main import list_matches

        await _seed_matches()
        lower = await list_matches(player_name="ally1")
        upper = await list_matches(player_name="ALLY1")
        assert lower["total"] == upper["total"]
        assert lower["total"] > 0

    async def test_filter_by_player_name_no_match(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(player_name="NonexistentPlayer99")
        assert result["total"] == 0

    async def test_filter_by_enemy_player_name(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(player_name="Enemy1")
        assert result["total"] > 0


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


class TestListMatchesSorting:
    async def test_sort_by_eliminations_desc(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(sort_by="eliminations", sort_order="desc")
        values = [m["sort_value"] for m in result["matches"]]
        assert values == sorted(values, reverse=True)

    async def test_sort_by_damage_asc(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(sort_by="damage", sort_order="asc")
        values = [m["sort_value"] for m in result["matches"]]
        non_null = [v for v in values if v is not None]
        assert non_null == sorted(non_null)

    async def test_sort_value_included_in_response(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(sort_by="eliminations")
        for m in result["matches"]:
            assert "sort_value" in m


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestListMatchesPagination:
    async def test_limit(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(limit=2)
        assert len(result["matches"]) == 2
        assert result["total"] == 4

    async def test_offset(self):
        from main import list_matches

        await _seed_matches()
        page1 = await list_matches(limit=2, offset=0)
        page2 = await list_matches(limit=2, offset=2)
        ids1 = {m["id"] for m in page1["matches"]}
        ids2 = {m["id"] for m in page2["matches"]}
        assert ids1.isdisjoint(ids2)

    async def test_limit_capped_at_100(self):
        from main import list_matches

        await _seed_matches()
        result = await list_matches(limit=999)
        # Should not error; internal limit is capped at 100
        assert result["total"] == 4
