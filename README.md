# Overwatch Stats MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server for tracking and analyzing Overwatch 2 match history. Submit match results with full player stats and hero data, then query for trends, rankings, teammate synergies, and recurring player histories — all accessible to any MCP-compatible AI client like Claude Desktop.

## Features

- **Match tracking** — Record complete 10-player lobbies with per-player stats, hero-specific breakdowns, notes, and screenshots
- **Flexible querying** — Filter and sort matches by map, mode, queue type, hero, date range, and stats
- **Match editing** — Update match metadata, player data (names, titles, stats, heroes), and manage screenshots after submission
- **Player notes** — Attach persistent notes to player usernames (globally, not per-match), surfaced in match details, teammate stats, and player history
- **Aggregated analytics** — Win rates, averages, and trends grouped by role, map, mode, hero, or time period (supports dual-axis grouping)
- **Hero detail stats** — Per-hero stat breakdowns with automatic parsing of percentages, durations (MM:SS), and comma-formatted numbers
- **Teammate tracking** — Win/loss rates with recurring teammates, with name normalization to handle rank suffixes like "(Bronze)"
- **Player history** — Look up any match and see which players you've encountered before, with their past performance and perspective-adjusted results
- **Lobby rankings** — See how you rank within each lobby across all stat categories, with percentile calculations
- **Duration analysis** — Win rates and performance bucketed by match length

## Requirements

- Python 3.12+
- PostgreSQL 15+
- [uv](https://docs.astral.sh/uv/) (recommended package manager)
- Docker (for running tests)

## Quick Start

### 1. Start PostgreSQL

Using Docker Compose (included):

```bash
docker compose up -d
```

This starts PostgreSQL on `localhost:5432` with database `overwatch_stats` and credentials `postgres:postgres`.

### 2. Install dependencies

```bash
uv sync
```

### 3. Run database migrations

```bash
uv run alembic upgrade head
```

### 4. Start the server

```bash
uv run python main.py
```

The server starts on `http://0.0.0.0:8000` using the Streamable HTTP transport.

**CLI options:**

| Flag     | Default   | Description          |
|----------|-----------|----------------------|
| `--host` | `0.0.0.0` | Bind address        |
| `--port` | `8000`    | Port to listen on    |

```bash
uv run python main.py --host 127.0.0.1 --port 9000
```

## Configuration

### Database

Set the `DATABASE_URL` environment variable to override the default connection string:

```bash
export DATABASE_URL="postgresql+asyncpg://user:password@host:5432/overwatch_stats"
```

Default: `postgresql+asyncpg://postgres:postgres@localhost:5432/overwatch_stats`

### Docker Compose

The included `docker-compose.yml` runs PostgreSQL only. Edit it to change the port, credentials, or Postgres version:

```yaml
services:
  postgres:
    image: postgres:17
    ports:
      - "5432:5432"
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: overwatch_stats
    volumes:
      - pgdata:/var/lib/postgresql/data
```

## Connecting to an MCP Client

### Claude Desktop

Add to your Claude Desktop MCP config:

```json
{
  "mcpServers": {
    "overwatch-stats": {
      "type": "streamable-http",
      "url": "http://localhost:8000/mcp/"
    }
  }
}
```

## Tools

### `ping`

Health check. Returns `"pong"`.

### `submit_match`

Record a completed match with all player stats.

**Parameters:**

| Name          | Type       | Required | Description                                              |
|---------------|------------|----------|----------------------------------------------------------|
| `map_name`    | string     | Yes      | Map name (e.g. "Lijiang Tower")                          |
| `duration`    | string     | Yes      | Match duration as `MM:SS`                                |
| `mode`        | string     | Yes      | `PUSH`, `CONTROL`, `ESCORT`, `HYBRID`, `CLASH`, `FLASHPOINT` |
| `queue_type`  | string     | Yes      | `COMPETITIVE` or `QUICKPLAY`                             |
| `result`      | string     | Yes      | `VICTORY`, `DEFEAT`, or `UNKNOWN`                        |
| `players`     | array      | Yes      | Array of 10 player objects (see below)                   |
| `played_at`   | string     | No       | ISO 8601 timestamp                                       |
| `notes`       | string     | No       | Free-text notes about the match                          |
| `is_backfill` | bool       | No       | Whether this match was backfilled from historical data (default false) |
| `source`      | string     | No       | Source identifier (e.g. "ocr", "manual"; default empty)  |
| `screenshots` | string[]   | No       | List of screenshot URLs (image download links)           |

**Player object:**

| Key            | Type    | Required | Description                                          |
|----------------|---------|----------|------------------------------------------------------|
| `team`         | string  | Yes      | `ALLY` or `ENEMY`                                    |
| `role`         | string  | Yes      | `TANK`, `DPS`, or `SUPPORT`                          |
| `player_name`  | string  | Yes      | Player's display name                                |
| `title`        | string  | No       | Player's title (e.g. competitive rank title)         |
| `eliminations` | int     | No       | Elimination count                                    |
| `assists`      | int     | No       | Assist count                                         |
| `deaths`       | int     | No       | Death count                                          |
| `damage`       | int     | No       | Damage dealt                                         |
| `healing`      | int     | No       | Healing done                                         |
| `mitigation`   | int     | No       | Damage mitigated                                     |
| `is_self`      | bool    | No       | Whether this is the recording player (default false) |
| `hero`         | object  | No       | Hero data with `hero_name` and `stats` array         |

### `get_match`

Retrieve full details for a match by UUID, including all player stats, hero stat values, notes, backfill flag, and screenshot URLs.

### `edit_match`

Edit an existing match's metadata. Only provided fields are updated.

**Parameters:**

| Name                   | Type     | Required | Description                                    |
|------------------------|----------|----------|------------------------------------------------|
| `match_id`             | string   | Yes      | UUID of the match to edit                      |
| `map_name`             | string   | No       | New map name                                   |
| `duration`             | string   | No       | New duration as `MM:SS`                        |
| `mode`                 | string   | No       | New game mode                                  |
| `queue_type`           | string   | No       | `COMPETITIVE` or `QUICKPLAY`                   |
| `result`               | string   | No       | `VICTORY`, `DEFEAT`, or `UNKNOWN`              |
| `played_at`            | string   | No       | ISO 8601 timestamp (empty string to clear)     |
| `notes`                | string   | No       | New notes text (empty string to clear)         |
| `is_backfill`          | bool     | No       | New backfill flag                              |
| `source`               | string   | No       | New source identifier                          |
| `screenshots_to_add`   | string[] | No       | Screenshot URLs to attach                      |
| `screenshots_to_remove`| string[] | No       | Screenshot URLs to remove                      |
| `player_edits`         | array    | No       | List of player stat edits (see below)          |

**Player edit object:**

| Key              | Type   | Required | Description                                         |
|------------------|--------|----------|-----------------------------------------------------|
| `player_stat_id` | string | Yes      | UUID of the player stat (from `get_match` response)  |
| `player_name`    | string | No       | New player name                                      |
| `title`          | string | No       | New title (empty string to clear)                    |
| `team`           | string | No       | `ALLY` or `ENEMY`                                    |
| `role`           | string | No       | `TANK`, `DPS`, or `SUPPORT`                          |
| `eliminations`   | int    | No       | New elimination count                                |
| `assists`        | int    | No       | New assist count                                     |
| `deaths`         | int    | No       | New death count                                      |
| `damage`         | int    | No       | New damage dealt                                     |
| `healing`        | int    | No       | New healing done                                     |
| `mitigation`     | int    | No       | New damage mitigated                                 |
| `is_self`        | bool   | No       | New self flag                                        |
| `hero_name`      | string | No       | Set/change hero name (empty string to clear hero)    |

### `list_matches`

List matches with filtering, sorting, and pagination.

**Filters:** `map_name`, `mode`, `queue_type`, `result`, `from_date`, `to_date`, `hero_name`
**Sorting:** `sort_by` (any of the 6 stat columns), `sort_order` (`asc`/`desc`)
**Pagination:** `limit` (default 20, max 100), `offset`

### `get_stats_summary`

Aggregated stats for the self-player. Win rates, averages across all 6 stat categories.

**Grouping:** `group_by` and optional `group_by_2` — supports `role`, `map`, `mode`, `hero`, `week`, `day`, `hour`, `weekday`.
**Filters:** `queue_type`, `from_date`, `to_date`, `last_n`

### `get_hero_detail_stats`

Per-hero breakdowns from hero-specific stat values (the stats shown on the hero card in-game). Automatically parses percentages, `MM:SS` durations, and comma-formatted numbers into numeric aggregates (count/avg/min/max).

**Filters:** `hero_name`, `label`, `queue_type`, `from_date`, `to_date`

### `get_teammate_stats`

Win/loss stats grouped by teammate (non-self allies). Normalizes player names by stripping rank suffixes like `" (Bronze)"`.

**Filters:** `queue_type`, `from_date`, `to_date`, `min_games`, `limit`

### `get_match_player_history`

Given a match ID, finds all non-self players who have appeared in other recorded matches and returns their recent match history with stats. Results are split into `players_with_history` and `players_without_history`. Match results are shown from each player's perspective (enemy victories become defeats and vice versa).

**Parameters:** `match_id`, `match_history` (number of recent matches per player, default 3)

### `get_match_rankings`

Computes the self-player's average rank within each match lobby for all 6 stat categories, along with percentiles: `(lobby_size - rank) / (lobby_size - 1)`.

**Filters:** `queue_type`, `from_date`, `to_date`

### `get_duration_stats`

Win rates and average stats bucketed by match duration. Parses `MM:SS` duration strings into seconds and groups them into configurable buckets.

**Filters:** `queue_type`, `from_date`, `to_date`, `bucket_size` (seconds, default 120)

### `set_player_note`

Set or update a global note for a player by username. Pass an empty string to delete the note.

**Parameters:** `player_name` (string), `note` (string)

### `get_player_note`

Get the note for a player by username. Returns `null` if no note exists.

**Parameters:** `player_name` (string)

### `list_player_notes`

List all player notes.

### `delete_match`

Delete a match and all associated data (player stats, hero stats, screenshots) by UUID. Cascading delete.

## OpenClaw Integration

The server can notify an [OpenClaw](https://docs.openclaw.ai) agent whenever a new match is submitted. Two modes are available:

- **Agent CLI** (recommended) — runs a turn within an existing session with full conversation history
- **Webhook** — POSTs to the `/hooks/agent` endpoint for an isolated agent turn

**Quick setup (agent-CLI mode):**

1. Set `OPENCLAW_AGENT_SESSION_ID` in `.env` to target an existing session
2. Copy `webhook_prompt.j2.example` to `webhook_prompt.j2` and customise
3. Optionally set `OPENCLAW_AGENT_CHANNEL` and `OPENCLAW_AGENT_REPLY_TO` for delivery

See [OPENCLAW_SETUP.md](OPENCLAW_SETUP.md) for full configuration details including webhook mode.

## Database Schema

Six tables (five with cascading deletes, one standalone):

```
matches
  ├── player_stats
  │     └── hero_stats
  │           └── hero_stat_values
  └── screenshots

player_notes (standalone, keyed by username)
```

- **matches** — Map, mode, queue type, result, duration, notes, is_backfill, timestamps
- **player_stats** — Per-player per-match: team, role, name, title, 6 stat columns, is_self flag
- **hero_stats** — Links a player_stat to a hero name (1:1)
- **hero_stat_values** — Arbitrary key-value hero stats (label/value/is_featured)
- **screenshots** — Screenshot URLs attached to a match
- **player_notes** — Global notes attached to player usernames

Migrations are managed with Alembic. To create a new migration after modifying models:

```bash
uv run alembic revision --autogenerate -m "description"
uv run alembic upgrade head
```

## Testing

Tests run against a disposable PostgreSQL container via [testcontainers](https://testcontainers.com/) — they never connect to any external or production database. Docker must be running.

### Install test dependencies

```bash
uv sync --extra test
```

### Run tests

```bash
uv run pytest
```

```bash
uv run pytest -v                           # verbose output
uv run pytest tests/test_match_crud.py     # single file
uv run pytest -k "test_filter"             # keyword match
```

### Test structure

```
tests/
├── conftest.py            # Testcontainers setup, DB override, per-test cleanup
├── factories.py           # Test data helpers (make_players, create_test_match)
├── test_match_crud.py     # Submit, get, edit, delete (39 tests)
├── test_list_matches.py   # Filtering, sorting, pagination (16 tests)
├── test_analytics.py      # Stats, heroes, teammates, rankings, duration, history (40 tests)
├── test_player_notes.py   # Player notes CRUD and integration (11 tests)
├── test_screenshots.py    # Screenshot upload and serving
└── test_webhook.py        # Webhook and agent-CLI notification (33 tests)
```

## Project Structure

```
.
├── main.py                    # MCP server — all tools and helpers
├── models.py                  # SQLAlchemy ORM models
├── db.py                      # Database engine and session factory
├── webhook.py                 # OpenClaw integration (agent-CLI and webhook modes)
├── webhook_prompt.j2.example  # Example Jinja2 template for notification prompt
├── OPENCLAW_SETUP.md          # OpenClaw integration setup guide
├── alembic.ini                # Alembic configuration
├── alembic/
│   ├── env.py                 # Async migration environment
│   └── versions/              # Migration scripts
├── tests/                     # Test suite (151 tests, requires Docker)
├── docker-compose.yml         # PostgreSQL service
└── pyproject.toml             # Project metadata and dependencies
```

## Deployment

For production deployment on a VPS:

1. Set `DATABASE_URL` to your production Postgres instance
2. Run `uv run alembic upgrade head` to apply migrations
3. Start with `uv run python main.py`
4. Use systemd or similar to keep the process running
5. Put a reverse proxy (nginx/caddy) in front for TLS

**Note:** The server currently has no authentication. Restrict access via firewall, VPN, or add an auth layer before exposing publicly.

**Reverse proxy body size:** Screenshot uploads send base64-encoded images in the request body, which can be 10-20MB+ for 4K screenshots. Most reverse proxies reject this by default (nginx allows only 1MB). Increase the limit to allow uploads:

- **Nginx:** `client_max_body_size 50M;` in the `server` or `location` block
- **Caddy:** `request_body { max_size 50MB }` in the site block
