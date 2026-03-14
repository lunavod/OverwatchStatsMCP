"""OpenClaw integration — fires after match creation.

Two notification modes are available:

1. **Webhook** (`OPENCLAW_WEBHOOK_*`) — POSTs to the /hooks/agent endpoint.
   Runs an *isolated* agent turn and posts a summary into the session.

2. **Agent CLI** (`OPENCLAW_AGENT_*`) — Invokes ``openclaw agent`` via CLI.
   Runs a turn *within* an existing session, so the agent has full
   conversation history and context.

Only one mode should be active at a time.  The agent-CLI mode is checked
first; if its required env vars are not set the webhook mode is tried.
"""

import asyncio
import json
import logging
import os
import shutil
import uuid
from pathlib import Path

import httpx
from jinja2 import BaseLoader, Environment

logger = logging.getLogger(__name__)

# -- Shared ------------------------------------------------------------------

WEBHOOK_SOURCE_FILTER = os.getenv("OPENCLAW_WEBHOOK_SOURCE_FILTER", "")
WEBHOOK_TEMPLATE_PATH = Path(
    os.getenv("OPENCLAW_WEBHOOK_TEMPLATE", "webhook_prompt.j2")
)

# -- Webhook mode -------------------------------------------------------------

WEBHOOK_URL = os.getenv("OPENCLAW_WEBHOOK_URL")
WEBHOOK_TOKEN = os.getenv("OPENCLAW_WEBHOOK_TOKEN")
WEBHOOK_SESSION_KEY = os.getenv("OPENCLAW_WEBHOOK_SESSION_KEY")
WEBHOOK_CHANNEL = os.getenv("OPENCLAW_WEBHOOK_CHANNEL")
WEBHOOK_TO = os.getenv("OPENCLAW_WEBHOOK_TO")

# -- Agent-CLI mode -----------------------------------------------------------

AGENT_SESSION_ID = os.getenv("OPENCLAW_AGENT_SESSION_ID")
AGENT_CHANNEL = os.getenv("OPENCLAW_AGENT_CHANNEL")
AGENT_REPLY_TO = os.getenv("OPENCLAW_AGENT_REPLY_TO")
AGENT_TIMEOUT = os.getenv("OPENCLAW_AGENT_TIMEOUT", "120")


def _source_allowed(source: str) -> bool:
    """Check if the match source passes the filter."""
    if not WEBHOOK_SOURCE_FILTER:
        return True
    allowed = {s.strip() for s in WEBHOOK_SOURCE_FILTER.split(",") if s.strip()}
    return source in allowed


def _load_template() -> str | None:
    if WEBHOOK_TEMPLATE_PATH.exists():
        return WEBHOOK_TEMPLATE_PATH.read_text(encoding="utf-8")
    return None


def _render_prompt(match_data: dict) -> str:
    raw = _load_template()
    if raw is None:
        raise FileNotFoundError(
            f"Webhook template not found: {WEBHOOK_TEMPLATE_PATH}. "
            "Copy webhook_prompt.j2.example to webhook_prompt.j2 and customise it."
        )
    env = Environment(loader=BaseLoader(), autoescape=False)
    template = env.from_string(raw)
    return template.render(match=match_data)


async def _fire_webhook_http(match_data: dict) -> None:
    """POST to the OpenClaw /hooks/agent endpoint with rendered prompt."""
    try:
        prompt = _render_prompt(match_data)
    except FileNotFoundError as exc:
        logger.warning("Skipping webhook: %s", exc)
        return

    payload: dict = {
        "message": prompt,
        "name": "OverwatchMatchSubmit",
    }
    if WEBHOOK_SESSION_KEY:
        payload["sessionKey"] = WEBHOOK_SESSION_KEY
    if WEBHOOK_CHANNEL:
        payload["channel"] = WEBHOOK_CHANNEL
    if WEBHOOK_TO:
        payload["to"] = WEBHOOK_TO

    headers = {
        "Authorization": f"Bearer {WEBHOOK_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        assert WEBHOOK_URL is not None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(WEBHOOK_URL, json=payload, headers=headers)
            resp.raise_for_status()
            logger.info("Webhook fired (status %s)", resp.status_code)
    except httpx.HTTPError as exc:
        logger.error("Webhook request failed: %s", exc)


async def _fire_agent_cli(match_data: dict) -> None:
    """Invoke ``openclaw gateway call agent`` to run a turn within an existing session.

    Uses the gateway RPC method with ``sessionKey`` so the turn is both
    read from and persisted into the correct session.
    """
    openclaw = shutil.which("openclaw")
    if openclaw is None:
        logger.error("openclaw binary not found on PATH")
        return

    try:
        prompt = _render_prompt(match_data)
    except FileNotFoundError as exc:
        logger.warning("Skipping agent-cli: %s", exc)
        return

    params: dict = {
        "sessionKey": AGENT_SESSION_ID,
        "message": prompt,
        "idempotencyKey": str(uuid.uuid4()),
    }
    if AGENT_CHANNEL:
        params["deliver"] = True
        params["channel"] = AGENT_CHANNEL
        if AGENT_REPLY_TO:
            params["to"] = AGENT_REPLY_TO

    cmd = [
        openclaw, "gateway", "call", "agent",
        "--expect-final",
        "--timeout", str(int(AGENT_TIMEOUT) * 1000),
        "--params", json.dumps(params),
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            logger.info("Agent CLI turn completed (exit 0)")
        else:
            logger.error(
                "Agent CLI failed (exit %s): %s",
                proc.returncode,
                stderr.decode(errors="replace").strip(),
            )
    except OSError as exc:
        logger.error("Agent CLI exec error: %s", exc)


async def fire_webhook(match_data: dict) -> None:
    """Notify OpenClaw about a new match.

    Uses agent-CLI mode if configured, otherwise falls back to webhook mode.
    """
    if not _source_allowed(match_data.get("source", "")):
        return

    if AGENT_SESSION_ID:
        asyncio.create_task(_fire_agent_cli(match_data))
    elif WEBHOOK_URL and WEBHOOK_TOKEN:
        await _fire_webhook_http(match_data)

