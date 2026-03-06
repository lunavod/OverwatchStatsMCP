"""Tests for the OpenClaw webhook integration."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import webhook
from webhook import _render_prompt, fire_webhook


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


@pytest.fixture
def template_file(tmp_path):
    """Create a temporary Jinja2 template and patch the module to use it."""
    tpl = tmp_path / "test_prompt.j2"
    tpl.write_text(
        "Map: {{ match.map_name }}, Result: {{ match.result }}, ID: {{ match.match_id }}"
    )
    with patch.object(webhook, "WEBHOOK_TEMPLATE_PATH", tpl):
        yield tpl


@pytest.fixture
def match_data():
    return {
        "match_id": "abc-123",
        "map_name": "Lijiang Tower",
        "duration": "12:30",
        "mode": "CONTROL",
        "queue_type": "COMPETITIVE",
        "result": "VICTORY",
        "played_at": "2026-01-15T20:00:00",
        "notes": None,
        "is_backfill": False,
    }


def test_render_prompt(template_file, match_data):
    result = _render_prompt(match_data)
    assert result == "Map: Lijiang Tower, Result: VICTORY, ID: abc-123"


def test_render_prompt_missing_template():
    with patch.object(webhook, "WEBHOOK_TEMPLATE_PATH", Path("/nonexistent.j2")):
        with pytest.raises(FileNotFoundError, match="Webhook template not found"):
            _render_prompt({"map_name": "test"})


def test_render_prompt_with_conditionals(tmp_path, match_data):
    tpl = tmp_path / "cond.j2"
    tpl.write_text(
        "{{ match.map_name }}{% if match.notes %} - {{ match.notes }}{% endif %}"
        "{% if match.is_backfill %} [BACKFILL]{% endif %}"
    )
    with patch.object(webhook, "WEBHOOK_TEMPLATE_PATH", tpl):
        assert _render_prompt(match_data) == "Lijiang Tower"

        match_data["notes"] = "Great game"
        match_data["is_backfill"] = True
        assert _render_prompt(match_data) == "Lijiang Tower - Great game [BACKFILL]"


# ---------------------------------------------------------------------------
# fire_webhook — skips when not configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_webhook_noop_without_url(match_data):
    """No HTTP call when WEBHOOK_URL is not set."""
    with patch.object(webhook, "WEBHOOK_URL", None), \
         patch.object(webhook, "WEBHOOK_TOKEN", "tok"):
        with patch("webhook.httpx.AsyncClient") as mock_client:
            await fire_webhook(match_data)
            mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_fire_webhook_noop_without_token(match_data):
    """No HTTP call when WEBHOOK_TOKEN is not set."""
    with patch.object(webhook, "WEBHOOK_URL", "http://localhost/hooks/agent"), \
         patch.object(webhook, "WEBHOOK_TOKEN", None):
        with patch("webhook.httpx.AsyncClient") as mock_client:
            await fire_webhook(match_data)
            mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# fire_webhook — skips gracefully when template is missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_webhook_skips_missing_template(match_data):
    with patch.object(webhook, "WEBHOOK_URL", "http://localhost/hooks/agent"), \
         patch.object(webhook, "WEBHOOK_TOKEN", "tok"), \
         patch.object(webhook, "WEBHOOK_TEMPLATE_PATH", Path("/nonexistent.j2")):
        with patch("webhook.httpx.AsyncClient") as mock_client:
            await fire_webhook(match_data)
            mock_client.assert_not_called()


# ---------------------------------------------------------------------------
# fire_webhook — HTTP calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_webhook_posts_correct_payload(template_file, match_data):
    mock_response = AsyncMock()
    mock_response.status_code = 202
    mock_response.raise_for_status = lambda: None

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_response
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch.object(webhook, "WEBHOOK_URL", "http://localhost:18789/hooks/agent"), \
         patch.object(webhook, "WEBHOOK_TOKEN", "secret-token"), \
         patch.object(webhook, "WEBHOOK_SESSION_KEY", None), \
         patch("webhook.httpx.AsyncClient", return_value=mock_client_instance):
        await fire_webhook(match_data)

    mock_client_instance.post.assert_called_once()
    call_args = mock_client_instance.post.call_args
    assert call_args[0][0] == "http://localhost:18789/hooks/agent"

    payload = call_args[1]["json"]
    assert payload["name"] == "OverwatchMatchSubmit"
    assert "Lijiang Tower" in payload["message"]
    assert "VICTORY" in payload["message"]
    assert "sessionKey" not in payload

    headers = call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_fire_webhook_includes_session_key(template_file, match_data):
    mock_response = AsyncMock()
    mock_response.status_code = 202
    mock_response.raise_for_status = lambda: None

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_response
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch.object(webhook, "WEBHOOK_URL", "http://localhost:18789/hooks/agent"), \
         patch.object(webhook, "WEBHOOK_TOKEN", "tok"), \
         patch.object(webhook, "WEBHOOK_SESSION_KEY", "hook:overwatch"), \
         patch("webhook.httpx.AsyncClient", return_value=mock_client_instance):
        await fire_webhook(match_data)

    payload = mock_client_instance.post.call_args[1]["json"]
    assert payload["sessionKey"] == "hook:overwatch"


@pytest.mark.asyncio
async def test_fire_webhook_handles_http_error(template_file, match_data):
    """HTTP errors are logged, not raised."""
    mock_client_instance = AsyncMock()
    mock_client_instance.post.side_effect = httpx.ConnectError("connection refused")
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch.object(webhook, "WEBHOOK_URL", "http://localhost:18789/hooks/agent"), \
         patch.object(webhook, "WEBHOOK_TOKEN", "tok"), \
         patch.object(webhook, "WEBHOOK_SESSION_KEY", None), \
         patch("webhook.httpx.AsyncClient", return_value=mock_client_instance):
        # Should not raise
        await fire_webhook(match_data)


# ---------------------------------------------------------------------------
# Integration: submit_match triggers webhook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_match_fires_webhook(template_file):
    """submit_match calls fire_webhook with correct match data."""
    from tests.factories import make_players

    captured = {}

    async def fake_fire(data):
        captured.update(data)

    with patch("main.fire_webhook", side_effect=fake_fire):
        from main import submit_match

        result = await submit_match(
            map_name="Hanamura",
            duration="08:45",
            mode="HYBRID",
            queue_type="COMPETITIVE",
            result="DEFEAT",
            players=make_players(),
            played_at="2026-02-10T19:00:00",
            notes="Close game",
        )

    assert captured["match_id"] == result["match_id"]
    assert captured["map_name"] == "Hanamura"
    assert captured["mode"] == "HYBRID"
    assert captured["result"] == "DEFEAT"
    assert captured["notes"] == "Close game"
    assert captured["played_at"] == "2026-02-10T19:00:00"
    assert captured["is_backfill"] is False


@pytest.mark.asyncio
async def test_submit_match_succeeds_when_webhook_fails(template_file):
    """Webhook failure must not prevent match creation."""
    from tests.factories import make_players

    mock_client_instance = AsyncMock()
    mock_client_instance.post.side_effect = httpx.ConnectError("refused")
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with patch.object(webhook, "WEBHOOK_URL", "http://localhost:18789/hooks/agent"), \
         patch.object(webhook, "WEBHOOK_TOKEN", "tok"), \
         patch.object(webhook, "WEBHOOK_SESSION_KEY", None), \
         patch("webhook.httpx.AsyncClient", return_value=mock_client_instance):
        from main import submit_match

        result = await submit_match(
            map_name="Dorado",
            duration="10:00",
            mode="ESCORT",
            queue_type="COMPETITIVE",
            result="VICTORY",
            players=make_players(),
        )

    assert "match_id" in result
