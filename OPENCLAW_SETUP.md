# OpenClaw Integration

This server can notify an [OpenClaw](https://docs.openclaw.ai) agent whenever a new match is submitted. The agent receives a rendered prompt with match details and can use the MCP tools to analyse the match.

Two notification modes are available:

| Mode | How it works | Session context |
|------|-------------|-----------------|
| **Agent CLI** | Invokes `openclaw agent` via CLI | Full — runs within the existing session with complete conversation history |
| **Webhook** | POSTs to the `/hooks/agent` HTTP endpoint | Isolated — runs an independent agent turn and posts a summary into the session |

Only one mode should be active at a time. If both are configured, the agent-CLI mode takes priority.

## Prerequisites

1. A running OpenClaw Gateway
2. The `openclaw` CLI on the server's `PATH` (agent-CLI mode) **or** a webhook token configured in your Gateway (webhook mode)

## Shared configuration

Both modes share these settings:

```bash
# Optional — only fire for matches with these sources (comma-separated)
# If empty or unset, fires for all matches regardless of source
OPENCLAW_WEBHOOK_SOURCE_FILTER=ocr,manual

# Optional — path to the Jinja2 template file (default: webhook_prompt.j2)
OPENCLAW_WEBHOOK_TEMPLATE=webhook_prompt.j2
```

### Prompt template

Copy the example template and customise it:

```bash
cp webhook_prompt.j2.example webhook_prompt.j2
```

The template uses [Jinja2](https://jinja.palletsprojects.com/) syntax. The `match` object is passed as the template context with these fields:

| Field        | Type        | Description                         |
|--------------|-------------|-------------------------------------|
| `match_id`   | string      | UUID of the created match           |
| `map_name`   | string      | Map name                            |
| `duration`   | string      | Match duration (MM:SS)              |
| `mode`       | string      | Game mode                           |
| `queue_type` | string      | COMPETITIVE or QUICKPLAY            |
| `result`     | string      | VICTORY, DEFEAT, or UNKNOWN         |
| `played_at`  | string/null | ISO 8601 timestamp (if provided)    |
| `notes`      | string/null | Free-text notes (if provided)       |
| `is_backfill`| bool        | Whether the match was backfilled    |
| `source`     | string      | Source identifier (default `""`)    |

---

## Agent-CLI mode (recommended)

Runs a turn within an existing OpenClaw session so the agent has full conversation history and context. Requires the `openclaw` CLI to be available on the server's `PATH`.

### Environment variables

```bash
# Required — the session key (or ID) to run the agent turn in
OPENCLAW_AGENT_SESSION_ID=agent:main:telegram:group:-5033067937

# Optional — deliver the response to a messaging channel
OPENCLAW_AGENT_CHANNEL=telegram

# Optional — recipient on the delivery channel (e.g. Telegram chat ID)
OPENCLAW_AGENT_REPLY_TO=-5033067937

# Optional — agent turn timeout in seconds (default: 120)
OPENCLAW_AGENT_TIMEOUT=120
```

### How it works

1. When `submit_match` is called, the match is saved to the database
2. The source filter is checked (if configured)
3. The Jinja2 template is rendered with the match data
4. `openclaw agent --session-id <id> --message <prompt>` is invoked
5. The agent processes the prompt with full access to the session history and MCP tools
6. If `OPENCLAW_AGENT_CHANNEL` is set, the response is delivered to that channel (e.g. a Telegram group)

---

## Webhook mode

Runs an isolated agent turn via the Gateway's `/hooks/agent` HTTP endpoint. The agent does **not** have access to the session's conversation history — it runs independently and posts a summary into the session.

### OpenClaw Gateway configuration

In your OpenClaw config (typically `~/.openclaw/openclaw.json`), enable webhooks:

```json
{
  "hooks": {
    "enabled": true,
    "token": "your-secret-token",
    "allowRequestSessionKey": true
  }
}
```

### Environment variables

```bash
# Required — the full URL to the OpenClaw /hooks/agent endpoint
OPENCLAW_WEBHOOK_URL=http://127.0.0.1:18789/hooks/agent

# Required — the webhook token from your OpenClaw config
OPENCLAW_WEBHOOK_TOKEN=your-secret-token

# Optional — session key for the webhook (enables session continuity)
OPENCLAW_WEBHOOK_SESSION_KEY=hook:overwatch

# Optional — delivery channel (default: "last")
OPENCLAW_WEBHOOK_CHANNEL=telegram

# Optional — delivery target (e.g. Telegram chat ID)
OPENCLAW_WEBHOOK_TO=-5033067937
```

### How it works

1. When `submit_match` is called, the match is saved to the database
2. The source filter is checked (if configured)
3. The Jinja2 template is rendered with the match data
4. The rendered prompt is POSTed to the OpenClaw `/hooks/agent` endpoint
5. OpenClaw runs an isolated agent turn with the prompt
6. The agent can use MCP tools to fetch match details and provide analysis
7. A summary is posted into the session specified by `sessionKey`

---

## Troubleshooting

- **Notification not firing**: Ensure at least one mode is configured (agent-CLI or webhook env vars)
- **Agent CLI: "openclaw binary not found"**: Ensure `openclaw` is on the `PATH` of the service user
- **Agent CLI: response goes to wrong chat**: Check `OPENCLAW_AGENT_CHANNEL` and `OPENCLAW_AGENT_REPLY_TO`
- **Webhook: 401 Unauthorized**: Verify the token matches your OpenClaw Gateway config
- **Webhook: response goes to DMs**: The webhook runs in isolation — set `OPENCLAW_WEBHOOK_CHANNEL` and `OPENCLAW_WEBHOOK_TO`, or switch to agent-CLI mode for proper session routing
- **Template not found**: Ensure `webhook_prompt.j2` exists (copy from `webhook_prompt.j2.example`)
- **Not firing for certain matches**: Check `OPENCLAW_WEBHOOK_SOURCE_FILTER` — if set, only matches whose `source` is in the list will trigger
