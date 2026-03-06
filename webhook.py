"""OpenClaw webhook integration — fires after match creation."""

import logging
import os
from pathlib import Path

import httpx
from jinja2 import BaseLoader, Environment

logger = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv("OPENCLAW_WEBHOOK_URL")
WEBHOOK_TOKEN = os.getenv("OPENCLAW_WEBHOOK_TOKEN")
WEBHOOK_SESSION_KEY = os.getenv("OPENCLAW_WEBHOOK_SESSION_KEY")
WEBHOOK_TEMPLATE_PATH = Path(
    os.getenv("OPENCLAW_WEBHOOK_TEMPLATE", "webhook_prompt.j2")
)


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


async def fire_webhook(match_data: dict) -> None:
    """POST to the OpenClaw /hooks/agent endpoint with rendered prompt."""
    if not WEBHOOK_URL or not WEBHOOK_TOKEN:
        return

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

    headers = {
        "Authorization": f"Bearer {WEBHOOK_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(WEBHOOK_URL, json=payload, headers=headers)
            resp.raise_for_status()
            logger.info("Webhook fired (status %s)", resp.status_code)
    except httpx.HTTPError as exc:
        logger.error("Webhook request failed: %s", exc)
