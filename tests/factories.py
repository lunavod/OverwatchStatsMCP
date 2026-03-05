"""Shared test data factories for creating matches and players."""


def make_players(
    self_name="TestPlayer",
    self_role="SUPPORT",
    self_hero=None,
    self_stats=None,
    ally_names=None,
):
    """Build a 10-player roster with sensible defaults.

    Returns a list of player dicts suitable for submit_match().
    """
    if self_hero is None:
        self_hero = {
            "hero_name": "Ana",
            "stats": [
                {"label": "Nano Boost Assists", "value": "8", "is_featured": True},
                {"label": "Enemies Slept", "value": "12", "is_featured": False},
            ],
        }

    base_self = {
        "eliminations": 15,
        "assists": 20,
        "deaths": 5,
        "damage": 5000,
        "healing": 12000,
        "mitigation": 0,
    }
    if self_stats:
        base_self.update(self_stats)

    players = [
        {
            "team": "ALLY",
            "role": self_role,
            "player_name": self_name,
            **base_self,
            "is_self": True,
            "hero": self_hero,
        }
    ]

    _ally_names = ally_names or ["Ally1", "Ally2", "Ally3", "Ally4"]
    ally_templates = [
        ("TANK", 8000, 0, 15000),
        ("DPS", 10000, 0, 0),
        ("DPS", 9000, 0, 0),
        ("SUPPORT", 3000, 10000, 0),
    ]
    for name, (role, dmg, heal, mit) in zip(_ally_names, ally_templates):
        players.append(
            {
                "team": "ALLY",
                "role": role,
                "player_name": name,
                "eliminations": 10,
                "assists": 8,
                "deaths": 6,
                "damage": dmg,
                "healing": heal,
                "mitigation": mit,
                "is_self": False,
            }
        )

    enemy_templates = [
        ("TANK", 7000, 0, 12000),
        ("DPS", 9000, 0, 0),
        ("DPS", 8000, 0, 0),
        ("SUPPORT", 2500, 9000, 0),
        ("SUPPORT", 3000, 8000, 0),
    ]
    for i, (role, dmg, heal, mit) in enumerate(enemy_templates):
        players.append(
            {
                "team": "ENEMY",
                "role": role,
                "player_name": f"Enemy{i + 1}",
                "eliminations": 8,
                "assists": 5,
                "deaths": 7,
                "damage": dmg,
                "healing": heal,
                "mitigation": mit,
                "is_self": False,
            }
        )

    return players


async def create_test_match(**overrides):
    """Submit a match with defaults via the tool function. Returns match_id."""
    from main import submit_match

    defaults = {
        "map_name": "Lijiang Tower",
        "duration": "12:30",
        "mode": "CONTROL",
        "queue_type": "COMPETITIVE",
        "result": "VICTORY",
        "played_at": "2026-01-15T20:00:00",
        "players": make_players(),
    }
    defaults.update(overrides)
    result = await submit_match(**defaults)
    return result["match_id"]
