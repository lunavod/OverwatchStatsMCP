"""Tests for the OpenClaw webhook / agent-CLI integration."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import webhook
from webhook import (
    _fire_agent_cli,
    _render_prompt,
    _source_allowed,
    fire_webhook,
)


# ---------------------------------------------------------------------------
# Shared fixtures
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
def multiline_template(tmp_path):
    """Template that produces multi-line output."""
    tpl = tmp_path / "multi.j2"
    tpl.write_text("Line 1: {{ match.map_name }}\nLine 2: {{ match.result }}\nLine 3: done")
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
        "source": "",
    }


def _disable_both_modes():
    """Context manager that disables both notification modes."""
    return _patch_agent_cli_vars(session_id=None)


def _patch_agent_cli_vars(
    session_id="agent:main:telegram:group:-999",
    channel="telegram",
    reply_to="-999",
    timeout="60",
):
    """Patch all agent-CLI module vars at once."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with patch.object(webhook, "AGENT_SESSION_ID", session_id), \
             patch.object(webhook, "AGENT_CHANNEL", channel), \
             patch.object(webhook, "AGENT_REPLY_TO", reply_to), \
             patch.object(webhook, "AGENT_TIMEOUT", timeout):
            yield

    return _ctx()


def _patch_webhook_vars(
    url="http://localhost:18789/hooks/agent",
    token="tok",
    session_key=None,
    channel=None,
    to=None,
):
    """Patch all webhook-HTTP module vars at once."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with patch.object(webhook, "WEBHOOK_URL", url), \
             patch.object(webhook, "WEBHOOK_TOKEN", token), \
             patch.object(webhook, "WEBHOOK_SESSION_KEY", session_key), \
             patch.object(webhook, "WEBHOOK_CHANNEL", channel), \
             patch.object(webhook, "WEBHOOK_TO", to), \
             patch.object(webhook, "AGENT_SESSION_ID", None), \
             patch.object(webhook, "WEBHOOK_SOURCE_FILTER", ""):
            yield

    return _ctx()


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


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
# Source filter
# ---------------------------------------------------------------------------


def test_source_allowed_no_filter():
    with patch.object(webhook, "WEBHOOK_SOURCE_FILTER", ""):
        assert _source_allowed("") is True
        assert _source_allowed("ocr") is True
        assert _source_allowed("manual") is True


def test_source_allowed_single_value():
    with patch.object(webhook, "WEBHOOK_SOURCE_FILTER", "ocr"):
        assert _source_allowed("ocr") is True
        assert _source_allowed("manual") is False
        assert _source_allowed("") is False


def test_source_allowed_multiple_values():
    with patch.object(webhook, "WEBHOOK_SOURCE_FILTER", "ocr,manual"):
        assert _source_allowed("ocr") is True
        assert _source_allowed("manual") is True
        assert _source_allowed("other") is False
        assert _source_allowed("") is False


def test_source_allowed_whitespace_handling():
    with patch.object(webhook, "WEBHOOK_SOURCE_FILTER", " ocr , manual "):
        assert _source_allowed("ocr") is True
        assert _source_allowed("manual") is True


# ---------------------------------------------------------------------------
# fire_webhook — routing: agent-CLI takes priority over webhook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_cli_takes_priority_over_webhook(template_file, match_data):
    """When both modes are configured, agent-CLI is used."""
    with patch.object(webhook, "AGENT_SESSION_ID", "agent:main:test"), \
         patch.object(webhook, "AGENT_CHANNEL", None), \
         patch.object(webhook, "AGENT_REPLY_TO", None), \
         patch.object(webhook, "WEBHOOK_URL", "http://localhost/hooks/agent"), \
         patch.object(webhook, "WEBHOOK_TOKEN", "tok"), \
         patch.object(webhook, "WEBHOOK_SOURCE_FILTER", ""), \
         patch("webhook._fire_agent_cli", new_callable=AsyncMock) as mock_cli, \
         patch("webhook._fire_webhook_http", new_callable=AsyncMock) as mock_http:
        await fire_webhook(match_data)
        # create_task is used, so we need to let it run
        await asyncio.sleep(0)

    mock_cli.assert_called_once_with(match_data)
    mock_http.assert_not_called()


@pytest.mark.asyncio
async def test_falls_back_to_webhook_when_no_agent_session(template_file, match_data):
    """Webhook mode is used when AGENT_SESSION_ID is not set."""
    with patch.object(webhook, "AGENT_SESSION_ID", None), \
         patch.object(webhook, "WEBHOOK_URL", "http://localhost/hooks/agent"), \
         patch.object(webhook, "WEBHOOK_TOKEN", "tok"), \
         patch.object(webhook, "WEBHOOK_SOURCE_FILTER", ""), \
         patch("webhook._fire_agent_cli", new_callable=AsyncMock) as mock_cli, \
         patch("webhook._fire_webhook_http", new_callable=AsyncMock) as mock_http:
        await fire_webhook(match_data)

    mock_cli.assert_not_called()
    mock_http.assert_called_once_with(match_data)


@pytest.mark.asyncio
async def test_noop_when_neither_mode_configured(match_data):
    """No notification when both modes are unconfigured."""
    with patch.object(webhook, "AGENT_SESSION_ID", None), \
         patch.object(webhook, "WEBHOOK_URL", None), \
         patch.object(webhook, "WEBHOOK_TOKEN", None), \
         patch.object(webhook, "WEBHOOK_SOURCE_FILTER", ""), \
         patch("webhook._fire_agent_cli", new_callable=AsyncMock) as mock_cli, \
         patch("webhook._fire_webhook_http", new_callable=AsyncMock) as mock_http:
        await fire_webhook(match_data)

    mock_cli.assert_not_called()
    mock_http.assert_not_called()


# ---------------------------------------------------------------------------
# fire_webhook — source filter applies to both modes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_filter_blocks_agent_cli(template_file, match_data):
    match_data["source"] = "other"
    with patch.object(webhook, "WEBHOOK_SOURCE_FILTER", "ocr"), \
         patch.object(webhook, "AGENT_SESSION_ID", "agent:main:test"), \
         patch("webhook._fire_agent_cli", new_callable=AsyncMock) as mock_cli:
        await fire_webhook(match_data)

    mock_cli.assert_not_called()


@pytest.mark.asyncio
async def test_source_filter_blocks_webhook(template_file, match_data):
    match_data["source"] = "other"
    with _patch_webhook_vars(), \
         patch.object(webhook, "WEBHOOK_SOURCE_FILTER", "ocr"), \
         patch("webhook._fire_webhook_http", new_callable=AsyncMock) as mock_http:
        await fire_webhook(match_data)

    mock_http.assert_not_called()


# ---------------------------------------------------------------------------
# Agent-CLI mode: _fire_agent_cli
# ---------------------------------------------------------------------------


def _mock_subprocess(returncode=0, stdout=b"", stderr=b""):
    """Create a mock for asyncio.create_subprocess_exec."""
    proc = AsyncMock()
    proc.communicate.return_value = (stdout, stderr)
    proc.returncode = returncode
    return proc


@pytest.mark.asyncio
async def test_agent_cli_builds_correct_command(template_file, match_data):
    proc = _mock_subprocess()
    with _patch_agent_cli_vars(
        session_id="agent:main:telegram:group:-5033067937",
        channel="telegram",
        reply_to="-5033067937",
        timeout="120",
    ), \
         patch("shutil.which", return_value="/usr/bin/openclaw"), \
         patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec, \
         patch("uuid.uuid4", return_value="test-uuid-1234"):
        await _fire_agent_cli(match_data)

    mock_exec.assert_called_once()
    cmd = mock_exec.call_args[0]
    assert cmd[0] == "/usr/bin/openclaw"
    assert cmd[1:4] == ("gateway", "call", "agent")
    assert "--expect-final" in cmd
    assert "--timeout" in cmd
    assert cmd[cmd.index("--timeout") + 1] == "120000"  # seconds -> milliseconds
    assert "--params" in cmd
    params = json.loads(cmd[cmd.index("--params") + 1])
    assert params["sessionKey"] == "agent:main:telegram:group:-5033067937"
    assert "Lijiang Tower" in params["message"]
    assert "VICTORY" in params["message"]
    assert params["idempotencyKey"] == "test-uuid-1234"
    assert params["deliver"] is True
    assert params["channel"] == "telegram"
    assert params["to"] == "-5033067937"


@pytest.mark.asyncio
async def test_agent_cli_message_contains_rendered_prompt(template_file, match_data):
    proc = _mock_subprocess()
    with _patch_agent_cli_vars(), \
         patch("shutil.which", return_value="/usr/bin/openclaw"), \
         patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        await _fire_agent_cli(match_data)

    cmd = mock_exec.call_args[0]
    params = json.loads(cmd[cmd.index("--params") + 1])
    assert "Lijiang Tower" in params["message"]
    assert "VICTORY" in params["message"]
    assert "abc-123" in params["message"]


@pytest.mark.asyncio
async def test_agent_cli_multiline_prompt_in_json_params(multiline_template, match_data):
    """Newlines in the rendered prompt are preserved in the JSON --params value."""
    proc = _mock_subprocess()
    with _patch_agent_cli_vars(), \
         patch("shutil.which", return_value="/usr/bin/openclaw"), \
         patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        await _fire_agent_cli(match_data)

    cmd = mock_exec.call_args[0]
    params = json.loads(cmd[cmd.index("--params") + 1])
    message = params["message"]
    assert "\n" in message
    assert "Line 1: Lijiang Tower" in message
    assert "Line 2: VICTORY" in message
    assert "Line 3: done" in message


@pytest.mark.asyncio
async def test_agent_cli_no_deliver_without_channel(template_file, match_data):
    """deliver/channel/to are omitted from params when AGENT_CHANNEL is not set."""
    proc = _mock_subprocess()
    with _patch_agent_cli_vars(channel=None, reply_to=None), \
         patch("shutil.which", return_value="/usr/bin/openclaw"), \
         patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        await _fire_agent_cli(match_data)

    cmd = mock_exec.call_args[0]
    params = json.loads(cmd[cmd.index("--params") + 1])
    assert "deliver" not in params
    assert "channel" not in params
    assert "to" not in params
    # sessionKey and message should still be present
    assert "sessionKey" in params
    assert "message" in params


@pytest.mark.asyncio
async def test_agent_cli_channel_without_reply_to(template_file, match_data):
    """deliver and channel present but no 'to' when AGENT_REPLY_TO is unset."""
    proc = _mock_subprocess()
    with _patch_agent_cli_vars(channel="telegram", reply_to=None), \
         patch("shutil.which", return_value="/usr/bin/openclaw"), \
         patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        await _fire_agent_cli(match_data)

    cmd = mock_exec.call_args[0]
    params = json.loads(cmd[cmd.index("--params") + 1])
    assert params["deliver"] is True
    assert params["channel"] == "telegram"
    assert "to" not in params


@pytest.mark.asyncio
async def test_agent_cli_noop_when_binary_not_found(template_file, match_data):
    """No subprocess is spawned when openclaw is not on PATH."""
    with _patch_agent_cli_vars(), \
         patch("shutil.which", return_value=None), \
         patch("asyncio.create_subprocess_exec") as mock_exec:
        await _fire_agent_cli(match_data)

    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_agent_cli_skips_missing_template(match_data):
    with _patch_agent_cli_vars(), \
         patch.object(webhook, "WEBHOOK_TEMPLATE_PATH", Path("/nonexistent.j2")), \
         patch("shutil.which", return_value="/usr/bin/openclaw"), \
         patch("asyncio.create_subprocess_exec") as mock_exec:
        await _fire_agent_cli(match_data)

    mock_exec.assert_not_called()


@pytest.mark.asyncio
async def test_agent_cli_logs_nonzero_exit(template_file, match_data, caplog):
    proc = _mock_subprocess(returncode=1, stderr=b"session not found")
    with _patch_agent_cli_vars(), \
         patch("shutil.which", return_value="/usr/bin/openclaw"), \
         patch("asyncio.create_subprocess_exec", return_value=proc):
        await _fire_agent_cli(match_data)

    assert "Agent CLI failed (exit 1)" in caplog.text
    assert "session not found" in caplog.text


@pytest.mark.asyncio
async def test_agent_cli_handles_os_error(template_file, match_data, caplog):
    with _patch_agent_cli_vars(), \
         patch("shutil.which", return_value="/usr/bin/openclaw"), \
         patch("asyncio.create_subprocess_exec", side_effect=OSError("exec failed")):
        await _fire_agent_cli(match_data)

    assert "Agent CLI exec error" in caplog.text


# ---------------------------------------------------------------------------
# fire_webhook — agent-CLI runs as background task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_cli_runs_in_background(template_file, match_data):
    """fire_webhook returns immediately; _fire_agent_cli runs as a background task."""
    started = asyncio.Event()
    finished = asyncio.Event()

    async def slow_agent_cli(data):
        started.set()
        await asyncio.sleep(0.1)
        finished.set()

    with patch.object(webhook, "AGENT_SESSION_ID", "agent:main:test"), \
         patch.object(webhook, "WEBHOOK_SOURCE_FILTER", ""), \
         patch("webhook._fire_agent_cli", side_effect=slow_agent_cli):
        await fire_webhook(match_data)
        # fire_webhook should have returned already
        assert not finished.is_set()
        # But the task should have been scheduled
        await asyncio.sleep(0)
        assert started.is_set()
        # Wait for completion
        await asyncio.sleep(0.2)
        assert finished.is_set()


# ---------------------------------------------------------------------------
# Webhook-HTTP mode (existing tests updated to disable agent-CLI)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_webhook_noop_without_url(match_data):
    """No HTTP call when WEBHOOK_URL is not set."""
    with _patch_webhook_vars(url=None):
        with patch("webhook.httpx.AsyncClient") as mock_client:
            await fire_webhook(match_data)
            mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_fire_webhook_noop_without_token(match_data):
    """No HTTP call when WEBHOOK_TOKEN is not set."""
    with _patch_webhook_vars(token=None):
        with patch("webhook.httpx.AsyncClient") as mock_client:
            await fire_webhook(match_data)
            mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_fire_webhook_skips_filtered_source(match_data):
    """No HTTP call when source doesn't match filter."""
    match_data["source"] = "other"
    with _patch_webhook_vars(), \
         patch.object(webhook, "WEBHOOK_SOURCE_FILTER", "ocr,manual"):
        with patch("webhook.httpx.AsyncClient") as mock_client:
            await fire_webhook(match_data)
            mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_fire_webhook_fires_for_matching_source(template_file, match_data):
    """HTTP call is made when source matches filter."""
    match_data["source"] = "ocr"
    mock_response = AsyncMock()
    mock_response.status_code = 202
    mock_response.raise_for_status = lambda: None

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_response
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with _patch_webhook_vars(), \
         patch.object(webhook, "WEBHOOK_SOURCE_FILTER", "ocr,manual"), \
         patch("webhook.httpx.AsyncClient", return_value=mock_client_instance):
        await fire_webhook(match_data)

    mock_client_instance.post.assert_called_once()


@pytest.mark.asyncio
async def test_fire_webhook_skips_missing_template(match_data):
    with _patch_webhook_vars(), \
         patch.object(webhook, "WEBHOOK_TEMPLATE_PATH", Path("/nonexistent.j2")):
        with patch("webhook.httpx.AsyncClient") as mock_client:
            await fire_webhook(match_data)
            mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_fire_webhook_posts_correct_payload(template_file, match_data):
    mock_response = AsyncMock()
    mock_response.status_code = 202
    mock_response.raise_for_status = lambda: None

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_response
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with _patch_webhook_vars(token="secret-token"), \
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

    with _patch_webhook_vars(session_key="hook:overwatch"), \
         patch("webhook.httpx.AsyncClient", return_value=mock_client_instance):
        await fire_webhook(match_data)

    payload = mock_client_instance.post.call_args[1]["json"]
    assert payload["sessionKey"] == "hook:overwatch"


@pytest.mark.asyncio
async def test_fire_webhook_includes_channel_and_to(template_file, match_data):
    mock_response = AsyncMock()
    mock_response.status_code = 202
    mock_response.raise_for_status = lambda: None

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = mock_response
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with _patch_webhook_vars(channel="telegram", to="-5033067937"), \
         patch("webhook.httpx.AsyncClient", return_value=mock_client_instance):
        await fire_webhook(match_data)

    payload = mock_client_instance.post.call_args[1]["json"]
    assert payload["channel"] == "telegram"
    assert payload["to"] == "-5033067937"


@pytest.mark.asyncio
async def test_fire_webhook_handles_http_error(template_file, match_data):
    """HTTP errors are logged, not raised."""
    mock_client_instance = AsyncMock()
    mock_client_instance.post.side_effect = httpx.ConnectError("connection refused")
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with _patch_webhook_vars(), \
         patch("webhook.httpx.AsyncClient", return_value=mock_client_instance):
        # Should not raise
        await fire_webhook(match_data)


# ---------------------------------------------------------------------------
# Integration: submit_match triggers notification
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
            source="ocr",
        )

    assert captured["match_id"] == result["match_id"]
    assert captured["map_name"] == "Hanamura"
    assert captured["mode"] == "HYBRID"
    assert captured["result"] == "DEFEAT"
    assert captured["notes"] == "Close game"
    assert captured["played_at"] == "2026-02-10T19:00:00"
    assert captured["is_backfill"] is False
    assert captured["source"] == "ocr"


@pytest.mark.asyncio
async def test_submit_match_succeeds_when_webhook_fails(template_file):
    """Webhook failure must not prevent match creation."""
    from tests.factories import make_players

    mock_client_instance = AsyncMock()
    mock_client_instance.post.side_effect = httpx.ConnectError("refused")
    mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_client_instance.__aexit__ = AsyncMock(return_value=False)

    with _patch_webhook_vars(), \
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
