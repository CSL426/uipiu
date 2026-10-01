import asyncio
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_parser import AgentParser
from app.nidin import normalize_menu_from_api
from tests.fixtures import RAW_GROUP_MENU


@pytest.fixture
def menu():
    return normalize_menu_from_api(RAW_GROUP_MENU, "1717")


def assert_safe_reply(result):
    assert result.action == "reply"
    assert result.items == []
    assert result.raw_output == ""
    assert "手動點餐" in result.reply_message
    assert "沒有加入購物車" in result.reply_message
    assert "sensitive" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_no_backend_is_safe_reply(menu, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    parser = AgentParser(enable_fast_graph=False)
    with patch("app.agent_parser.shutil.which", return_value=None):
        result = await parser.parse("推薦飲料", menu)
    assert_safe_reply(result)


@pytest.mark.asyncio
async def test_provider_error_does_not_leak(menu, caplog):
    parser = AgentParser(enable_fast_graph=False)
    with patch.object(
        parser, "_run_llm", AsyncMock(side_effect=RuntimeError("sensitive-key"))
    ):
        result = await parser.parse("推薦飲料", menu)
    assert_safe_reply(result)
    assert "sensitive" not in caplog.text


@pytest.mark.asyncio
async def test_application_timeout_cancels_backend(menu, monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0.01")
    parser = AgentParser(enable_fast_graph=False)
    cancelled = asyncio.Event()

    async def stalled_backend(prompt):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with patch.object(parser, "_run_llm", stalled_backend):
        result = await asyncio.wait_for(parser.parse("推薦飲料", menu), 1)
    assert cancelled.is_set()
    assert_safe_reply(result)
    assert "逾時" in result.reply_message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    [
        "sensitive raw model output",
        "[]",
        "null",
        '{"action": "unexpected", "items": []}',
        '{"action": "add_to_cart", "items": []}',
        '{"action": "add_to_cart", "items": [{"quantity": "sensitive"}]}',
        '{"action": "reply", "items": [{"item_id": "1", "item_name": "茶"}]}',
    ],
)
async def test_invalid_output_never_returns_cart_items(menu, output, caplog):
    parser = AgentParser(enable_fast_graph=False)
    with patch.object(parser, "_run_llm", AsyncMock(return_value=output)):
        result = await parser.parse("推薦飲料", menu)
    assert_safe_reply(result)
    assert "sensitive" not in caplog.text


@pytest.mark.parametrize(
    ("configured", "expected"),
    [("5", 5), ("999", 120), ("nan", 30), ("inf", 30), ("-1", 30),
     ("0", 30), ("bad", 30)],
)
def test_timeout_has_finite_upper_bound(monkeypatch, configured, expected):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", configured)
    assert AgentParser().timeout_seconds == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_agy_timeout_and_cancel_reap_process_group(
    tmp_path, monkeypatch, cancel
):
    marker = tmp_path / "worker.pid"
    script = tmp_path / "fake-agy"
    # A real descendant checks cleanup beyond just killing the CLI process.
    script.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    script.chmod(0o700)
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "2" if cancel else "0.5")
    parser = AgentParser()
    real_spawn = asyncio.create_subprocess_exec
    spawned = []

    async def track_spawn(*args, **kwargs):
        proc = await real_spawn(*args, **kwargs)
        spawned.append(proc)
        return proc

    with patch("asyncio.create_subprocess_exec", track_spawn):
        task = asyncio.create_task(parser._run_agy(str(script), "ignored"))
        if cancel:
            async with asyncio.timeout(1):
                while not marker.exists():
                    await asyncio.sleep(0.01)
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await asyncio.wait_for(task, 3)
    assert spawned[0].returncode == -signal.SIGKILL
    with pytest.raises(ProcessLookupError):
        os.kill(spawned[0].pid, 0)
    child_pid = int(marker.read_text())
    # Container PID 1 may leave an orphan zombie, which is no longer running.
    child_stat = Path(f"/proc/{child_pid}/stat")
    async with asyncio.timeout(1):
        while child_stat.exists() and child_stat.read_text().split()[2] != "Z":
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_sdk_uses_async_transport_timeout_and_closes(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "1.25")
    parser = AgentParser()
    async_client = MagicMock()
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock()
    async_client.models.generate_content = AsyncMock(
        return_value=SimpleNamespace(text="response")
    )
    client = MagicMock()
    client.__enter__.return_value = client
    client.aio = async_client
    with patch("google.genai.Client", return_value=client) as factory:
        assert await parser._run_genai_sdk("fake-key", "prompt") == "response"
    assert factory.call_args.kwargs["http_options"].timeout == 1250
    async_client.models.generate_content.assert_awaited_once()
    async_client.__aexit__.assert_awaited_once()
    client.__exit__.assert_called_once()


@pytest.mark.asyncio
async def test_sdk_timeout_closes_async_client(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "0.01")
    parser = AgentParser()

    async def stalled_request(**kwargs):
        await asyncio.Event().wait()

    async_client = MagicMock()
    async_client.__aenter__ = AsyncMock(return_value=async_client)
    async_client.__aexit__ = AsyncMock(return_value=False)
    async_client.models.generate_content = stalled_request
    client = MagicMock()
    client.__enter__.return_value = client
    client.aio = async_client
    with patch("google.genai.Client", return_value=client):
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(parser._run_genai_sdk("fake", "prompt"), 1)
    async_client.__aexit__.assert_awaited_once()
    client.__exit__.assert_called_once()


@pytest.mark.asyncio
async def test_agy_provider_error_discards_stderr(menu, caplog):
    parser = AgentParser(enable_fast_graph=False)
    proc = SimpleNamespace(
        returncode=1,
        communicate=AsyncMock(return_value=(b"", b"sensitive-key")),
    )
    with (
        patch("app.agent_parser.shutil.which", return_value="/fake/agy"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        result = await parser.parse("推薦飲料", menu)
    assert_safe_reply(result)
    assert "sensitive" not in caplog.text


@pytest.mark.asyncio
async def test_fast_graph_still_works_without_backend(menu, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    parser = AgentParser()
    with patch.object(parser, "_run_llm", AsyncMock()) as backend:
        result = await parser.parse("小明要一杯中杯阿薩姆紅茶 半糖正常冰", menu)
    assert result.action == "add_to_cart"
    assert len(result.items) == 1
    backend.assert_not_awaited()
