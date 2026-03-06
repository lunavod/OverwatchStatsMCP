# OpenClaw Webhook Integration

This server can notify an [OpenClaw](https://docs.openclaw.ai) agent whenever a new match is submitted, using the `/hooks/agent` webhook endpoint. The agent receives a rendered prompt with match details and can use the MCP tools to analyse the match.

## Prerequisites

1. A running OpenClaw Gateway with webhooks enabled
2. A webhook token configured in your OpenClaw Gateway

### OpenClaw Gateway configuration

In your OpenClaw config (typically `~/.openclaw/config.json`), enable webhooks:

```json
{
  "hooks": {
    "enabled": true,
    "token": "your-secret-token",
    "allowRequestSessionKey": true,
    "allowedSessionKeyPrefixes": ["hook:"]
  }
}
```

If you don't need session key support, you can set `allowRequestSessionKey` to `false` and omit the `OPENCLAW_WEBHOOK_SESSION_KEY` environment variable.

## MCP Server Configuration

### 1. Set environment variables

Add these to your `.env` file:

```bash
# Required — the full URL to the OpenClaw /hooks/agent endpoint
OPENCLAW_WEBHOOK_URL=http://127.0.0.1:18789/hooks/agent

# Required — the webhook token from your OpenClaw config
OPENCLAW_WEBHOOK_TOKEN=your-secret-token

# Optional — session key for the webhook (enables session continuity)
# Must match an allowed prefix in your OpenClaw config
OPENCLAW_WEBHOOK_SESSION_KEY=hook:overwatch

# Optional — path to the Jinja2 template file (default: webhook_prompt.j2)
OPENCLAW_WEBHOOK_TEMPLATE=webhook_prompt.j2
```

### 2. Create the prompt template

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

## How it works

1. When `submit_match` is called, the match is saved to the database
2. If `OPENCLAW_WEBHOOK_URL` and `OPENCLAW_WEBHOOK_TOKEN` are set, the server renders the Jinja2 template with the match data
3. The rendered prompt is POSTed to the OpenClaw `/hooks/agent` endpoint
4. OpenClaw runs an agent turn with the prompt, which can use the MCP tools to fetch full match details and provide analysis
5. If a `sessionKey` is configured, all webhook-triggered agent turns share the same session context

## Troubleshooting

- **Webhook not firing**: Check that both `OPENCLAW_WEBHOOK_URL` and `OPENCLAW_WEBHOOK_TOKEN` are set in your environment
- **Template not found**: Ensure `webhook_prompt.j2` exists (copy from `webhook_prompt.j2.example`)
- **401 Unauthorized**: Verify the token matches your OpenClaw Gateway config
- **Session key rejected**: Ensure `allowRequestSessionKey: true` and the key prefix is in `allowedSessionKeyPrefixes`
