"""Optional Telegram bot integration — sends scoreboard images after match creation."""

import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def is_configured() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


async def send_scoreboard(image_paths: list[Path], caption: str | None = None) -> None:
    """Send scoreboard images to Telegram, each as a separate message.

    Only fires if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set.
    """
    if not is_configured():
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"

    async with httpx.AsyncClient(timeout=30) as client:
        for i, path in enumerate(image_paths):
            if not path.exists():
                logger.warning("Scoreboard image not found: %s", path)
                continue

            data = {"chat_id": TELEGRAM_CHAT_ID}
            # Caption only on the first image
            if i == 0 and caption:
                data["caption"] = caption

            with open(path, "rb") as f:
                files = {"photo": (path.name, f, "image/png")}
                try:
                    resp = await client.post(url, data=data, files=files)
                    resp.raise_for_status()
                    logger.info("Telegram photo sent: %s", path.name)
                except httpx.HTTPError as exc:
                    logger.error("Telegram send failed for %s: %s", path.name, exc)
