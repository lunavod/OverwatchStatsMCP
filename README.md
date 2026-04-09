# Overwatch Stats MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server for tracking and analyzing Overwatch 2 match history. Submit match results with full player stats and hero data, then query for trends, rankings, teammate synergies, and recurring player histories — all accessible to any MCP-compatible AI client like Claude Desktop.

## Features

- **Multi-user** — Google OAuth authentication with per-user data isolation; admin panel for user management
- **Match tracking** — Record complete 10-player lobbies with per-player stats, multi-hero timelines, swap performance snapshots, banned heroes, notes, screenshots, rank range metadata, and post-match rank updates
- **Scoreboard generation** — Automatically generates scoreboard PNG images on match submission
- **Flexible querying** — Filter and sort matches by map, mode, queue type, hero, date range, and stats
- **Match editing** — Update match metadata, player data (names, titles, stats, heroes), and manage screenshots after submission
- **Player notes** — Attach persistent notes to player usernames (per-user, not shared), surfaced in match details, teammate stats, and player history
- **Aggregated analytics** — Win rates, averages, and trends grouped by role, map, mode, hero, or time period (supports dual-axis grouping)
- **Hero detail stats** — Per-hero stat breakdowns with automatic parsing of percentages, durations (MM:SS), and comma-formatted numbers
- **Teammate tracking** — Win/loss rates with recurring teammates, with name normalization to handle rank suffixes like "(Bronze)"
- **Player history** — Look up any match and see which players you've encountered before, with their past performance and perspective-adjusted results
- **Lobby rankings** — See how you rank within each lobby across all stat categories, with percentile calculations
- **Duration analysis** — Win rates and performance bucketed by match length
- **File attachments** — Attach large files (recordings, metadata) to matches via [tus](https://tus.io/) resumable uploads, with automatic storage limit enforcement

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

### 4. Configure authentication

Set up Google OAuth for multi-user authentication. See [`docs/google-oauth-setup.md`](docs/google-oauth-setup.md) for the full guide.

Add to your `.env` file:

```bash
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
EXTERNAL_URL=http://localhost:8000
```

When `GOOGLE_CLIENT_ID` is not set, auth is disabled (useful for development with test scripts).

### 5. Start the server

```bash
uv run python src/main.py
```

The server starts on `http://0.0.0.0:8000` using the Streamable HTTP transport. The first user to authenticate is automatically promoted to admin.

**CLI options:**

| Flag     | Default   | Description          |
|----------|-----------|----------------------|
| `--host` | `0.0.0.0` | Bind address        |
| `--port` | `8000`    | Port to listen on    |

```bash
uv run python src/main.py --host 127.0.0.1 --port 9000
```

## Configuration

### Database

Set the `DATABASE_URL` environment variable to override the default connection string:

```bash
export DATABASE_URL="postgresql+asyncpg://user:password@host:5432/overwatch_stats"
```

Default: `postgresql+asyncpg://postgres:postgres@localhost:5432/overwatch_stats`

### Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `GOOGLE_CLIENT_ID` | *(empty)* | Google OAuth 2.0 client ID — auth disabled when empty |
| `GOOGLE_CLIENT_SECRET` | *(empty)* | Google OAuth 2.0 client secret |
| `EXTERNAL_URL` | `http://localhost:8000` | Base URL of the server (used for OAuth callbacks) |

See [`docs/google-oauth-setup.md`](docs/google-oauth-setup.md) for setup instructions.

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

### File Attachments (tusd)

Large files (recordings, metadata) can be attached to matches via [tus](https://tus.io/) resumable uploads. This requires a separate [tusd](https://github.com/tus/tusd) server instance. See [`docs/tusd-setup.md`](docs/tusd-setup.md) for server setup and [`docs/client-upload.md`](docs/client-upload.md) for client integration.

| Variable | Default | Description |
|----------|---------|-------------|
| `TUSD_AUTH_KEY` | *(empty)* | Bearer token clients must send for uploads |
| `TUSD_DATA_DIR` | `/srv/tusd-data` | Directory where tusd stores uploaded files |
| `MAX_STORED_MATCHES` | `0` (unlimited) | Max matches with files; oldest are purged when exceeded |

## Connecting to an MCP Client

When auth is enabled, Claude clients handle the OAuth flow automatically — you'll be redirected to sign in with Google on first connection.

### Claude Desktop / claude.ai

Add as a custom connector in **Settings > Connectors > Add custom connector** with your server's URL.

### Claude Code

Run `/mcp`, select **Remote (HTTP/SSE)**, and enter the server URL. Your browser opens for Google sign-in.

### Admin Panel

Accessible at `/admin/login`. Redirects to Google OAuth — only users with `is_admin=True` can access. The first registered user is auto-promoted to admin.

## Tools

### `ping`

Health check. Returns `"pong"`.

### `submit_match`

Record a completed match with all player stats.

**Parameters:**

| Name          | Type       | Required | Description                                              |
|---------------|------------|----------|----------------------------------------------------------|
| `map_name`    | string     | Yes      | Map name — fuzzy-matched to `maps.txt`; parenthetical suffixes like `(Lunar New Year)` are stripped |
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
| `screenshot_uploads` | array | No    | Base64 image uploads, each with `data` (base64 string) and optional `filename` |
| `rank_min`    | string     | No       | Minimum rank in the lobby (e.g. "Gold 3")                |
| `rank_max`    | string     | No       | Maximum rank in the lobby (e.g. "Diamond 1")             |
| `is_wide_match` | bool    | No       | Whether this is a wide skill-range match                 |
| `banned_heroes`  | string[] | No     | List of banned hero names — fuzzy-matched to `heroes.txt` |
| `initial_team_side` | string | No    | Initial side — `ATTACK` or `DEFEND`                      |
| `score_progression` | string[] | No  | Round scores as `"X:Y"` strings (e.g. `["1:0", "1:1", "2:1"]`) |
| `rank_update` | object | No | Rank update after the match (see below) |

**Rank update object:**

| Key                   | Type     | Required | Description                              |
|-----------------------|----------|----------|------------------------------------------|
| `rank`                | string   | Yes      | Rank tier (e.g. `"GOLD"`, `"PLATINUM"`)  |
| `division`            | int      | Yes      | Division within rank (1–5)               |
| `progress_pct`        | int      | Yes      | Progress within division (0–100)         |
| `delta_pct`           | int      | Yes      | Progress change (signed, e.g. +27 or -15)|
| `demotion_protection` | bool     | No       | Whether demotion protection is active (default false) |
| `modifiers`           | string[] | No       | Match modifiers (e.g. `["VICTORY", "UPHILL BATTLE"]`) |
| `hero_sr`             | object[] | No       | Per-hero SR updates: `[{hero, sr, delta}]` where `delta` is nullable |

**Player object:**

| Key            | Type    | Required | Description                                          |
|----------------|---------|----------|------------------------------------------------------|
| `team`         | string  | Yes      | `ALLY` or `ENEMY`                                    |
| `role`         | string  | Yes      | `TANK`, `DPS`, or `SUPPORT`                          |
| `player_name`  | string  | Yes      | Player's display name                                |
| `title`        | string  | No       | Player's title (e.g. competitive rank title)         |
| `hero_name`    | string  | No       | Hero played — fuzzy-matched to `heroes.txt` (auto-populated from `heroes` array if not set) |
| `eliminations` | int     | No       | Elimination count                                    |
| `assists`      | int     | No       | Assist count                                         |
| `deaths`       | int     | No       | Death count                                          |
| `damage`       | int     | No       | Damage dealt                                         |
| `healing`      | int     | No       | Healing done                                         |
| `mitigation`   | int     | No       | Damage mitigated                                     |
| `is_self`      | bool    | No       | Whether this is the recording player (default false) |
| `in_party`     | bool    | No       | Whether this player is in the recording player's group (default false) |
| `joined_at`    | int     | No       | Seconds from match start when this player joined (default 0) |
| `swap_snapshots` | array | No       | Cumulative stat snapshots at each hero swap — each with `time` (int seconds), `eliminations`, `assists`, `deaths`, `damage`, `healing`, `mitigation` (all int). Used to compute per-hero-segment performance deltas in `get_match` response. |
| `heroes`       | array   | No       | Array of hero dicts, each with `hero_name`, `started_at` (int array of seconds from match start), and `stats` (array of `{label, value, is_featured}`) |

**Name validation:** Map and hero names are fuzzy-matched against canonical lists (`maps.txt` and `heroes.txt`). Close typos from OCR are auto-corrected; completely unrecognizable names return an error and the match is rejected. Map names have parenthetical suffixes (e.g. `(Lunar New Year)`) stripped before matching. The same validation applies to `edit_match`.

### `get_match`

Retrieve full details for a match by UUID, including all player stats, hero stat values, multi-hero timelines (with computed `primary_hero`, `starting_hero`, `ending_hero`), banned heroes, rank range, rank update, notes, backfill flag, and screenshot URLs. When `swap_snapshots` are present on a player, computed `hero_segments` are included with pre-calculated per-segment stat deltas (eliminations, damage, etc. per hero play period).

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
| `screenshot_uploads`   | array    | No       | Base64 image uploads (same format as `submit_match`) |
| `screenshots_to_remove`| string[] | No       | Screenshot URLs to remove                      |
| `player_edits`         | array    | No       | List of player stat edits (see below)          |
| `rank_min`             | string   | No       | New minimum rank (empty string to clear)       |
| `rank_max`             | string   | No       | New maximum rank (empty string to clear)       |
| `is_wide_match`        | bool     | No       | New wide match flag                            |
| `banned_heroes`        | string[] | No       | New list of banned heroes (empty list to clear) |
| `initial_team_side`    | string   | No       | New initial side — `ATTACK` or `DEFEND` (empty string to clear) |
| `score_progression`    | string[] | No       | New score progression (empty list to clear)      |
| `rank_update`          | object   | No       | New rank update (same format as `submit_match`; pass `{}` to remove) |

**Player edit object:**

| Key              | Type   | Required | Description                                         |
|------------------|--------|----------|-----------------------------------------------------|
| `player_stat_id` | string | Yes      | UUID of the player stat (from `get_match` response)  |
| `player_name`    | string | No       | New player name                                      |
| `title`          | string | No       | New title (empty string to clear)                    |
| `hero`           | string | No       | Hero played (empty string to clear)                  |
| `team`           | string | No       | `ALLY` or `ENEMY`                                    |
| `role`           | string | No       | `TANK`, `DPS`, or `SUPPORT`                          |
| `eliminations`   | int    | No       | New elimination count                                |
| `assists`        | int    | No       | New assist count                                     |
| `deaths`         | int    | No       | New death count                                      |
| `damage`         | int    | No       | New damage dealt                                     |
| `healing`        | int    | No       | New healing done                                     |
| `mitigation`     | int    | No       | New damage mitigated                                 |
| `is_self`        | bool   | No       | New self flag                                        |
| `in_party`       | bool   | No       | New party member flag                                |
| `joined_at`      | int    | No       | Seconds from match start when player joined          |
| `swap_snapshots` | array  | No       | New swap snapshots (empty array to clear)             |
| `heroes`         | array  | No       | Replace all hero stats (same format as `submit_match` player `heroes`) |

### `list_matches`

List matches with filtering, sorting, and pagination.

**Filters:** `map_name`, `mode`, `queue_type`, `result`, `from_date`, `to_date`, `hero_name`, `player_name`, `rank`
**Sorting:** `sort_by` (any of the 6 stat columns), `sort_order` (`asc`/`desc`)
**Pagination:** `limit` (default 20, max 100), `offset`

### `get_stats_summary`

Aggregated stats for the self-player. Win rates, averages across all 6 stat categories.

**Grouping:** `group_by` and optional `group_by_2` — supports `role`, `map`, `mode`, `hero`, `week`, `day`, `hour`, `weekday`.
**Filters:** `queue_type`, `from_date`, `to_date`, `last_n`, `player_name`

### `get_hero_detail_stats`

Per-hero breakdowns from hero-specific stat values (the stats shown on the hero card in-game). Automatically parses percentages, `MM:SS` durations, and comma-formatted numbers into numeric aggregates (count/avg/min/max).

**Filters:** `hero_name`, `label`, `queue_type`, `from_date`, `to_date`

### `get_hero_stat_series`

Time-series of a single hero stat across all matches for the self-player. Returns individual data points per match ordered by `played_at`, along with overall count and average. Each point includes `match_id`, `played_at`, `value`, `map_name`, `result`, `duration`, and `queue_type`.

**Parameters:** `hero_name`, `label`
**Filters:** `queue_type`, `from_date`, `to_date`

### `get_teammate_stats`

Win/loss stats grouped by teammate (non-self allies). Normalizes player names by stripping rank suffixes like `" (Bronze)"`.

**Filters:** `queue_type`, `from_date`, `to_date`, `min_games`, `limit`

### `get_match_player_history`

Given a match ID, finds all non-self players who have appeared in other recorded matches and returns their recent match history with stats. Results are split into `players_with_history` and `players_without_history`. Match results are shown from each player's perspective (enemy victories become defeats and vice versa).

**Parameters:** `match_id`, `match_history` (number of recent matches per player, default 3)

### `get_player_history`

Like `get_match_player_history` but accepts a list of player usernames directly instead of a match ID. Looks up each player's recent match history with stats. Results are split into `players_with_history` and `players_without_history`. Name normalization strips title suffixes like `(Bronze)`.

**Parameters:** `player_names` (string[]), `match_history` (number of recent matches per player, default 3)

### `get_match_rankings`

Computes the self-player's average rank within each match lobby for all 6 stat categories, along with percentiles: `(lobby_size - rank) / (lobby_size - 1)`.

**Filters:** `queue_type`, `from_date`, `to_date`

### `get_duration_stats`

Win rates and average stats bucketed by match duration. Parses `MM:SS` duration strings into seconds and groups them into configurable buckets.

**Filters:** `queue_type`, `from_date`, `to_date`, `bucket_size` (seconds, default 120)

### `set_player_note`

Set or update a note for a player by username (per-user, not shared). Pass an empty string to delete the note.

**Parameters:** `player_name` (string), `note` (string)

### `get_player_note`

Get the note for a player by username. Returns `null` if no note exists.

**Parameters:** `player_name` (string)

### `list_player_notes`

List all player notes for the current user.

### `list_match_files`

List all files attached to a match.

**Parameters:** `match_id` (string)

### `delete_match_file`

Delete a file attached to a match (removes from DB and disk).

**Parameters:** `file_id` (string)

### `delete_match`

Delete a match and all associated data (player stats, hero stats, screenshots, files) by UUID. Cascading delete — also removes attached files from disk.

## Database Schema

Nine tables with cascading deletes rooted at users:

```
users
  ├── matches
  │     ├── player_stats
  │     │     └── hero_stats
  │     │           └── hero_stat_values
  │     ├── screenshots
  │     ├── match_files
  │     └── rank_updates (1:1)
  │           └── hero_sr_updates
  └── player_notes
```

- **users** — Google sub, email, display name, is_admin, is_disabled, max_stored_matches, timestamps
- **matches** — User-owned. Map, mode, queue type, result, duration, notes, is_backfill, rank_min, rank_max, is_wide_match, banned_heroes, initial_team_side, score_progression, has_attachments, scoreboard URLs, timestamps
- **player_stats** — Per-player per-match: team, role, name, title, hero, 6 stat columns, is_self, in_party, joined_at (seconds from match start), swap_snapshots (cumulative stats at hero swaps)
- **hero_stats** — Links a player_stat to a hero name (1:N for multi-hero support), with `started_at` timestamps
- **hero_stat_values** — Arbitrary key-value hero stats (label/value/is_featured)
- **screenshots** — Screenshot URLs attached to a match
- **match_files** — Files attached to a match via tus upload (filename, size, tus_id)
- **rank_updates** — Post-match rank update (1:1 with match): rank tier, division, progress %, delta %, demotion protection, modifiers
- **hero_sr_updates** — Per-hero SR values within a rank update: hero name, SR, delta (nullable)
- **player_notes** — Per-user notes attached to player usernames (unique per user+player_name)

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
├── conftest.py            # Testcontainers setup, DB override, auth context, per-test cleanup
├── factories.py           # Test data helpers (make_players, create_test_match)
├── test_match_crud.py     # Submit, get, edit, delete (108 tests)
├── test_list_matches.py   # Filtering, sorting, pagination (25 tests)
├── test_analytics.py      # Stats, heroes, teammates, rankings, duration, history (47 tests)
├── test_player_notes.py   # Player notes CRUD and integration (11 tests)
├── test_match_files.py    # File attachments, tusd hooks, storage limits (20 tests)
├── test_screenshots.py    # Screenshot upload and serving (11 tests)
├── test_user_isolation.py # Multi-user data isolation for every tool (18 tests)
├── test_auth.py           # OAuth provider, token management, user creation (19 tests)
└── test_admin.py          # Admin panel routes and access control (13 tests)
```

## Project Structure

```
.
├── src/
│   ├── main.py                # MCP server — all tools and helpers
│   ├── models.py              # SQLAlchemy ORM models (User, Match, PlayerStat, etc.)
│   ├── db.py                  # Database engine and session factory
│   ├── auth.py                # OAuth 2.1 provider with Google as IdP
│   ├── admin.py               # Admin panel routes (user management)
│   ├── scoreboard.py          # Scoreboard PNG image generation
│   ├── tusd_hooks.py          # tusd webhook handlers for file upload lifecycle
│   └── templates/admin/       # Jinja2 templates for admin panel
├── scripts/
│   ├── export_data.py         # Export matches and notes to JSON
│   ├── import_data.py         # Import JSON data with user ownership
│   └── test_auth.py           # Manual OAuth flow testing script
├── docs/                      # Setup guides (tusd, client upload, Google OAuth)
├── tests/                     # Test suite (284 tests, requires Docker)
├── alembic/
│   ├── env.py                 # Async migration environment
│   └── versions/              # Migration scripts (19 migrations)
├── heroes.txt                 # Canonical hero name list (used for fuzzy matching)
├── maps.txt                   # Canonical map name list (used for fuzzy matching)
├── alembic.ini                # Alembic configuration
├── docker-compose.yml         # PostgreSQL service
└── pyproject.toml             # Project metadata and dependencies
```

## Deployment

For production deployment on a VPS:

1. Set `DATABASE_URL` to your production Postgres instance
2. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `EXTERNAL_URL` for authentication
3. Run `uv run alembic upgrade head` to apply migrations
4. Start with `uv run python src/main.py`
5. Use systemd or similar to keep the process running
6. Put a reverse proxy (nginx/caddy) in front for TLS

**Reverse proxy body size:** Screenshot uploads send base64-encoded images in the request body, which can be 10-20MB+ for 4K screenshots. Most reverse proxies reject this by default (nginx allows only 1MB). Increase the limit to allow uploads:

- **Nginx:** `client_max_body_size 50M;` in the `server` or `location` block
- **Caddy:** `request_body { max_size 50MB }` in the site block
