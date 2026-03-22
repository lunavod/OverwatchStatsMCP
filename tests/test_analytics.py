"""Tests for analytics tools: stats summary, hero details, rankings, duration, teammates, player history."""

from tests.factories import create_test_match, make_players


# ---------------------------------------------------------------------------
# Shared dataset builder for analytics tests
# ---------------------------------------------------------------------------


async def _seed_analytics():
    """Create a dataset suitable for analytics queries. Returns match_ids."""
    ids = []

    # Match 1: Victory, CONTROL, Ana, Competitive
    ids.append(
        await create_test_match(
            map_name="Lijiang Tower",
            mode="CONTROL",
            queue_type="COMPETITIVE",
            result="VICTORY",
            duration="10:30",
            played_at="2026-01-10T18:00:00",
            players=make_players(
                self_role="SUPPORT",
                self_stats={
                    "eliminations": 12,
                    "assists": 22,
                    "deaths": 4,
                    "damage": 4000,
                    "healing": 14000,
                    "mitigation": 0,
                },
                ally_names=["BuddyA", "BuddyB", "AllyX", "AllyY"],
            ),
        )
    )

    # Match 2: Defeat, ESCORT, Soldier:76 DPS, Competitive
    ids.append(
        await create_test_match(
            map_name="Dorado",
            mode="ESCORT",
            queue_type="COMPETITIVE",
            result="DEFEAT",
            duration="15:00",
            played_at="2026-01-12T20:00:00",
            players=make_players(
                self_role="DPS",
                self_hero={
                    "hero_name": "Soldier: 76",
                    "stats": [
                        {"label": "Helix Kills", "value": "5", "is_featured": True},
                        {
                            "label": "Tactical Visor Kills",
                            "value": "3",
                            "is_featured": False,
                        },
                    ],
                },
                self_stats={
                    "eliminations": 28,
                    "assists": 10,
                    "deaths": 8,
                    "damage": 18000,
                    "healing": 500,
                    "mitigation": 0,
                },
                ally_names=["BuddyA", "BuddyB", "AllyZ", "AllyW"],
            ),
        )
    )

    # Match 3: Victory, HYBRID, Ana, Quickplay
    ids.append(
        await create_test_match(
            map_name="King's Row",
            mode="HYBRID",
            queue_type="QUICKPLAY",
            result="VICTORY",
            duration="08:45",
            played_at="2026-01-14T22:00:00",
            players=make_players(
                self_role="SUPPORT",
                self_stats={
                    "eliminations": 10,
                    "assists": 25,
                    "deaths": 3,
                    "damage": 3500,
                    "healing": 16000,
                    "mitigation": 0,
                },
                ally_names=["BuddyA", "SoloQ1", "SoloQ2", "SoloQ3"],
            ),
        )
    )

    # Match 4: Victory, CONTROL, Reinhardt Tank, Competitive
    ids.append(
        await create_test_match(
            map_name="Lijiang Tower",
            mode="CONTROL",
            queue_type="COMPETITIVE",
            result="VICTORY",
            duration="12:00",
            played_at="2026-01-16T19:00:00",
            players=make_players(
                self_role="TANK",
                self_hero={
                    "hero_name": "Reinhardt",
                    "stats": [
                        {"label": "Earthshatter Kills", "value": "4", "is_featured": True},
                        {"label": "Fire Strike Kills", "value": "7", "is_featured": False},
                        {"label": "Charge Kills", "value": "2", "is_featured": False},
                    ],
                },
                self_stats={
                    "eliminations": 20,
                    "assists": 15,
                    "deaths": 6,
                    "damage": 12000,
                    "healing": 0,
                    "mitigation": 22000,
                },
                ally_names=["BuddyA", "BuddyB", "AllyX", "NewAlly"],
            ),
        )
    )

    return ids


# ===========================================================================
# get_stats_summary
# ===========================================================================


class TestStatsSummary:
    async def test_overall(self):
        from main import get_stats_summary

        await _seed_analytics()
        result = await get_stats_summary()
        groups = result["groups"]
        assert len(groups) == 1
        g = groups[0]
        assert g["group_key"] == "overall"
        assert g["matches"] == 4
        assert g["wins"] == 3
        assert g["losses"] == 1

    async def test_group_by_role(self):
        from main import get_stats_summary

        await _seed_analytics()
        result = await get_stats_summary(group_by="role")
        groups = {g["group_key"]: g for g in result["groups"]}
        assert "SUPPORT" in groups
        assert "DPS" in groups
        assert "TANK" in groups
        assert groups["SUPPORT"]["matches"] == 2
        assert groups["DPS"]["matches"] == 1
        assert groups["TANK"]["matches"] == 1

    async def test_group_by_map(self):
        from main import get_stats_summary

        await _seed_analytics()
        result = await get_stats_summary(group_by="map")
        groups = {g["group_key"]: g for g in result["groups"]}
        assert groups["Lijiang Tower"]["matches"] == 2
        assert groups["Dorado"]["matches"] == 1
        assert groups["King's Row"]["matches"] == 1

    async def test_group_by_hero(self):
        from main import get_stats_summary

        await _seed_analytics()
        result = await get_stats_summary(group_by="hero")
        groups = {g["group_key"]: g for g in result["groups"]}
        assert "Ana" in groups
        assert "Soldier: 76" in groups
        assert "Reinhardt" in groups

    async def test_group_by_mode(self):
        from main import get_stats_summary

        await _seed_analytics()
        result = await get_stats_summary(group_by="mode")
        groups = {g["group_key"]: g for g in result["groups"]}
        assert groups["CONTROL"]["matches"] == 2
        assert groups["ESCORT"]["matches"] == 1
        assert groups["HYBRID"]["matches"] == 1

    async def test_two_dimensions(self):
        from main import get_stats_summary

        await _seed_analytics()
        result = await get_stats_summary(group_by="role", group_by_2="map")
        # Should have group_key_2 in each entry
        for g in result["groups"]:
            assert "group_key_2" in g

    async def test_two_dimensions_requires_group_by(self):
        from main import get_stats_summary

        result = await get_stats_summary(group_by_2="map")
        assert "error" in result

    async def test_queue_type_filter(self):
        from main import get_stats_summary

        await _seed_analytics()
        result = await get_stats_summary(queue_type="COMPETITIVE")
        g = result["groups"][0]
        assert g["matches"] == 3  # Matches 1, 2, 4

    async def test_date_range_filter(self):
        from main import get_stats_summary

        await _seed_analytics()
        result = await get_stats_summary(
            from_date="2026-01-13T00:00:00", to_date="2026-01-15T00:00:00"
        )
        g = result["groups"][0]
        assert g["matches"] == 1  # Only Match 3 (Jan 14)

    async def test_last_n(self):
        from main import get_stats_summary

        await _seed_analytics()
        result = await get_stats_summary(last_n=2)
        g = result["groups"][0]
        assert g["matches"] == 2

    async def test_win_rate_calculation(self):
        from main import get_stats_summary

        await _seed_analytics()
        result = await get_stats_summary()
        g = result["groups"][0]
        assert g["win_rate"] == round(3 / 4, 4)

    async def test_avg_stats_are_numeric(self):
        from main import get_stats_summary

        await _seed_analytics()
        result = await get_stats_summary()
        g = result["groups"][0]
        for key in [
            "avg_eliminations",
            "avg_assists",
            "avg_deaths",
            "avg_damage",
            "avg_healing",
            "avg_mitigation",
        ]:
            assert isinstance(g[key], float)

    async def test_group_by_day(self):
        from main import get_stats_summary

        await _seed_analytics()
        result = await get_stats_summary(group_by="day")
        # 4 matches on 4 different days
        assert len(result["groups"]) == 4

    async def test_player_name_filter(self):
        from main import get_stats_summary

        await _seed_analytics()
        # BuddyA appears in all 4 seeded matches
        all_result = await get_stats_summary()
        filtered = await get_stats_summary(player_name="BuddyA")
        assert filtered["groups"][0]["matches"] == all_result["groups"][0]["matches"]

    async def test_player_name_filter_reduces_matches(self):
        from main import get_stats_summary

        await _seed_analytics()
        # AllyX appears only in matches 1 and 4
        filtered = await get_stats_summary(player_name="AllyX")
        assert filtered["groups"][0]["matches"] == 2


# ===========================================================================
# get_hero_detail_stats
# ===========================================================================


class TestHeroDetailStats:
    async def test_returns_stats(self):
        from main import get_hero_detail_stats

        await _seed_analytics()
        result = await get_hero_detail_stats()
        assert len(result["stats"]) > 0

    async def test_filter_by_hero(self):
        from main import get_hero_detail_stats

        await _seed_analytics()
        result = await get_hero_detail_stats(hero_name="Ana")
        for s in result["stats"]:
            assert s["hero_name"] == "Ana"

    async def test_filter_by_label(self):
        from main import get_hero_detail_stats

        await _seed_analytics()
        result = await get_hero_detail_stats(label="Nano Boost Assists")
        assert len(result["stats"]) >= 1
        assert all(s["label"] == "Nano Boost Assists" for s in result["stats"])

    async def test_stat_shape(self):
        from main import get_hero_detail_stats

        await _seed_analytics()
        result = await get_hero_detail_stats(hero_name="Ana")
        for s in result["stats"]:
            assert "count" in s
            assert "avg" in s
            assert "min" in s
            assert "max" in s
            assert "unit" in s

    async def test_hero_filter_case_insensitive(self):
        from main import get_hero_detail_stats

        await _seed_analytics()
        result = await get_hero_detail_stats(hero_name="reinhardt")
        assert len(result["stats"]) > 0
        for s in result["stats"]:
            assert s["hero_name"] == "Reinhardt"

    async def test_queue_type_filter(self):
        from main import get_hero_detail_stats

        await _seed_analytics()
        # Ana is used in Match 1 (comp) and Match 3 (quickplay)
        comp = await get_hero_detail_stats(hero_name="Ana", queue_type="COMPETITIVE")
        qp = await get_hero_detail_stats(hero_name="Ana", queue_type="QUICKPLAY")
        # Both should return stats, but counts should differ
        comp_stat = next(s for s in comp["stats"] if s["label"] == "Nano Boost Assists")
        qp_stat = next(s for s in qp["stats"] if s["label"] == "Nano Boost Assists")
        assert comp_stat["count"] == 1
        assert qp_stat["count"] == 1


# ===========================================================================
# get_hero_stat_series
# ===========================================================================


class TestHeroStatSeries:
    async def test_returns_points(self):
        from main import get_hero_stat_series

        await _seed_analytics()
        result = await get_hero_stat_series(hero_name="Ana", label="Nano Boost Assists")
        assert result["hero_name"] == "Ana"
        assert result["label"] == "Nano Boost Assists"
        assert result["count"] >= 1
        assert len(result["points"]) >= 1

    async def test_point_shape(self):
        from main import get_hero_stat_series

        await _seed_analytics()
        result = await get_hero_stat_series(hero_name="Ana", label="Nano Boost Assists")
        point = result["points"][0]
        assert "match_id" in point
        assert "played_at" in point
        assert "value" in point
        assert "map_name" in point
        assert "result" in point
        assert "duration" in point
        assert "queue_type" in point

    async def test_has_avg(self):
        from main import get_hero_stat_series

        await _seed_analytics()
        result = await get_hero_stat_series(hero_name="Ana", label="Nano Boost Assists")
        assert "avg" in result
        assert result["avg"] == result["points"][0]["value"] or result["count"] > 1

    async def test_case_insensitive(self):
        from main import get_hero_stat_series

        await _seed_analytics()
        result = await get_hero_stat_series(hero_name="ana", label="nano boost assists")
        assert result["count"] >= 1

    async def test_queue_type_filter(self):
        from main import get_hero_stat_series

        await _seed_analytics()
        comp = await get_hero_stat_series(
            hero_name="Ana", label="Nano Boost Assists", queue_type="COMPETITIVE"
        )
        qp = await get_hero_stat_series(
            hero_name="Ana", label="Nano Boost Assists", queue_type="QUICKPLAY"
        )
        assert comp["count"] == 1
        assert qp["count"] == 1

    async def test_empty_result(self):
        from main import get_hero_stat_series

        await _seed_analytics()
        result = await get_hero_stat_series(hero_name="Ana", label="Nonexistent Stat")
        assert result["count"] == 0
        assert result["points"] == []

    async def test_ordered_by_played_at(self):
        from main import get_hero_stat_series

        await _seed_analytics()
        result = await get_hero_stat_series(hero_name="Ana", label="Nano Boost Assists")
        dates = [p["played_at"] for p in result["points"] if p["played_at"]]
        assert dates == sorted(dates)

    async def test_unit_detection(self):
        from main import get_hero_stat_series

        await _seed_analytics()
        result = await get_hero_stat_series(hero_name="Ana", label="Nano Boost Assists")
        assert result["unit"] in ("percent", "time", "number")

    async def test_date_filters(self):
        from main import get_hero_stat_series

        await _seed_analytics()
        # Ana appears in Match 1 (2026-01-10) and Match 3 (2026-01-15)
        result = await get_hero_stat_series(
            hero_name="Ana",
            label="Nano Boost Assists",
            from_date="2026-01-12",
            to_date="2026-01-16",
        )
        # Should only include Match 3
        assert result["count"] == 1
        assert result["points"][0]["map_name"] == "King's Row"


# ===========================================================================
# get_teammate_stats
# ===========================================================================


class TestTeammateStats:
    async def test_returns_teammates(self):
        from main import get_teammate_stats

        await _seed_analytics()
        result = await get_teammate_stats()
        assert len(result["teammates"]) > 0

    async def test_counts_games_together(self):
        from main import get_teammate_stats

        await _seed_analytics()
        result = await get_teammate_stats()
        by_name = {t["player_name"]: t for t in result["teammates"]}
        # BuddyA appears in all 4 matches
        assert by_name["BuddyA"]["games"] == 4
        # BuddyB appears in matches 1, 2, 4
        assert by_name["BuddyB"]["games"] == 3

    async def test_min_games_filter(self):
        from main import get_teammate_stats

        await _seed_analytics()
        result = await get_teammate_stats(min_games=4)
        # Only BuddyA has 4 games
        assert len(result["teammates"]) == 1
        assert result["teammates"][0]["player_name"] == "BuddyA"

    async def test_win_rate(self):
        from main import get_teammate_stats

        await _seed_analytics()
        result = await get_teammate_stats()
        by_name = {t["player_name"]: t for t in result["teammates"]}
        buddy_a = by_name["BuddyA"]
        # BuddyA: 4 games, 3 wins (matches 1, 3, 4), 1 loss (match 2)
        assert buddy_a["wins"] == 3
        assert buddy_a["losses"] == 1
        assert buddy_a["win_rate"] == round(3 / 4, 4)

    async def test_queue_type_filter(self):
        from main import get_teammate_stats

        await _seed_analytics()
        result = await get_teammate_stats(queue_type="QUICKPLAY")
        # Only match 3 is quickplay — allies: BuddyA, SoloQ1, SoloQ2, SoloQ3
        names = {t["player_name"] for t in result["teammates"]}
        assert "BuddyA" in names
        assert "BuddyB" not in names

    async def test_name_normalization(self):
        """Player names with rank suffixes like '(Bronze)' should be normalized."""
        from main import get_teammate_stats

        # Create two matches: same player with and without suffix
        await create_test_match(
            played_at="2026-02-01T10:00:00",
            players=make_players(ally_names=["RankedPlayer", "A2", "A3", "A4"]),
        )
        await create_test_match(
            played_at="2026-02-02T10:00:00",
            players=make_players(
                ally_names=["RankedPlayer (Bronze)", "A2", "A3", "A4"]
            ),
        )
        result = await get_teammate_stats(min_games=2)
        by_name = {t["player_name"]: t for t in result["teammates"]}
        assert "RankedPlayer" in by_name
        assert by_name["RankedPlayer"]["games"] == 2

    async def test_ordered_by_games_desc(self):
        from main import get_teammate_stats

        await _seed_analytics()
        result = await get_teammate_stats()
        games = [t["games"] for t in result["teammates"]]
        assert games == sorted(games, reverse=True)


# ===========================================================================
# get_match_rankings
# ===========================================================================


class TestMatchRankings:
    async def test_returns_rankings(self):
        from main import get_match_rankings

        await _seed_analytics()
        result = await get_match_rankings()
        assert result["matches_analyzed"] == 4
        assert "rankings" in result

    async def test_all_stat_keys_present(self):
        from main import get_match_rankings

        await _seed_analytics()
        result = await get_match_rankings()
        expected = {
            "eliminations",
            "assists",
            "deaths",
            "damage",
            "healing",
            "mitigation",
        }
        assert set(result["rankings"].keys()) == expected

    async def test_ranking_shape(self):
        from main import get_match_rankings

        await _seed_analytics()
        result = await get_match_rankings()
        for stat_name, ranking in result["rankings"].items():
            assert "avg_rank" in ranking
            assert "avg_percentile" in ranking
            assert isinstance(ranking["avg_rank"], float)
            assert isinstance(ranking["avg_percentile"], float)

    async def test_queue_filter(self):
        from main import get_match_rankings

        await _seed_analytics()
        result = await get_match_rankings(queue_type="COMPETITIVE")
        assert result["matches_analyzed"] == 3


# ===========================================================================
# get_duration_stats
# ===========================================================================


class TestDurationStats:
    async def test_returns_buckets(self):
        from main import get_duration_stats

        await _seed_analytics()
        result = await get_duration_stats()
        assert "buckets" in result
        assert "bucket_size_seconds" in result
        assert len(result["buckets"]) > 0

    async def test_bucket_shape(self):
        from main import get_duration_stats

        await _seed_analytics()
        result = await get_duration_stats()
        for b in result["buckets"]:
            assert "range" in b
            assert "matches" in b
            assert "wins" in b
            assert "losses" in b
            assert "win_rate" in b

    async def test_custom_bucket_size(self):
        from main import get_duration_stats

        await _seed_analytics()
        result_small = await get_duration_stats(bucket_size=60)
        result_large = await get_duration_stats(bucket_size=600)
        # Smaller buckets → more granularity → at least as many buckets
        assert len(result_small["buckets"]) >= len(result_large["buckets"])

    async def test_queue_filter(self):
        from main import get_duration_stats

        await _seed_analytics()
        result = await get_duration_stats(queue_type="QUICKPLAY")
        total_matches = sum(b["matches"] for b in result["buckets"])
        assert total_matches == 1  # Only match 3 is quickplay


# ===========================================================================
# get_match_player_history
# ===========================================================================


class TestMatchPlayerHistory:
    async def test_not_found(self):
        from main import get_match_player_history

        result = await get_match_player_history("00000000-0000-0000-0000-000000000000")
        assert "error" in result

    async def test_returns_structure(self):
        from main import get_match_player_history

        ids = await _seed_analytics()
        result = await get_match_player_history(ids[-1])
        assert "match_id" in result
        assert "match_info" in result
        assert "players_with_history" in result
        assert "players_without_history" in result

    async def test_finds_recurring_players(self):
        from main import get_match_player_history

        ids = await _seed_analytics()
        # Check match 4 — enemies Enemy1-5 appear in all matches
        result = await get_match_player_history(ids[3])
        recurring_names = {
            p["normalized_name"] for p in result["players_with_history"]
        }
        # Enemy players appear in matches 1-3 as well
        for i in range(1, 6):
            assert f"Enemy{i}" in recurring_names

    async def test_history_entries_have_stats(self):
        from main import get_match_player_history

        ids = await _seed_analytics()
        result = await get_match_player_history(ids[3])
        if result["players_with_history"]:
            player = result["players_with_history"][0]
            assert "history" in player
            assert "total_appearances" in player
            for h in player["history"]:
                assert "stats" in h
                assert "map_name" in h
                assert "played_at" in h

    async def test_match_history_limit(self):
        from main import get_match_player_history

        ids = await _seed_analytics()
        result = await get_match_player_history(ids[3], match_history=1)
        for player in result["players_with_history"]:
            assert len(player["history"]) <= 1

    async def test_self_player_excluded(self):
        from main import get_match_player_history

        ids = await _seed_analytics()
        result = await get_match_player_history(ids[0])
        all_names = [p["player_name"] for p in result["players_with_history"]] + [
            p["player_name"] for p in result["players_without_history"]
        ]
        assert "TestPlayer" not in all_names


# ===========================================================================
# get_player_history
# ===========================================================================


class TestPlayerHistory:
    async def test_empty_list(self):
        from main import get_player_history

        result = await get_player_history([])
        assert result == {"players_with_history": [], "players_without_history": []}

    async def test_finds_known_players(self):
        from main import get_player_history

        await _seed_analytics()
        # Enemy1-5 appear in all seeded matches
        result = await get_player_history(["Enemy1", "Enemy2"])
        names_with = {p["player_name"] for p in result["players_with_history"]}
        assert "Enemy1" in names_with
        assert "Enemy2" in names_with

    async def test_unknown_player_in_without_history(self):
        from main import get_player_history

        await _seed_analytics()
        result = await get_player_history(["NeverSeenBefore"])
        assert len(result["players_with_history"]) == 0
        assert len(result["players_without_history"]) == 1
        assert result["players_without_history"][0]["player_name"] == "NeverSeenBefore"

    async def test_match_history_limit(self):
        from main import get_player_history

        await _seed_analytics()
        result = await get_player_history(["Enemy1"], match_history=1)
        for player in result["players_with_history"]:
            assert len(player["history"]) <= 1

    async def test_history_entries_have_stats(self):
        from main import get_player_history

        await _seed_analytics()
        result = await get_player_history(["Enemy1"])
        assert result["players_with_history"]
        player = result["players_with_history"][0]
        assert "total_appearances" in player
        entry = player["history"][0]
        assert "stats" in entry
        assert "eliminations" in entry["stats"]
        assert "match_id" in entry
