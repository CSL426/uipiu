import asyncio

import pytest
from fastapi.testclient import TestClient

from app import tts
from app.main import app


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    target = tmp_path / "tts-cache"
    monkeypatch.setattr(tts, "CACHE_DIR", target)
    return target


@pytest.fixture
def client():
    return TestClient(app)


def test_cache_key_ignores_surrounding_whitespace(cache_dir):
    assert tts.cache_key("  已加入購物車  ", "v") == tts.cache_key("已加入購物車", "v")


def test_cache_key_differs_by_voice(cache_dir):
    assert tts.cache_key("已加入購物車", "a") != tts.cache_key("已加入購物車", "b")


async def test_synthesize_writes_then_reuses_cache(cache_dir, monkeypatch):
    calls = []

    async def fake_stream(text, voice):
        calls.append(text)
        return b"ID3-audio"

    monkeypatch.setattr(tts, "_synthesize_to_bytes", fake_stream)

    first = await tts.synthesize("已為您解析點單")
    second = await tts.synthesize("已為您解析點單")

    assert first == second == b"ID3-audio"
    # 第二次命中快取,不應再打 EdgeTTS
    assert len(calls) == 1
    assert tts.cache_path("已為您解析點單", tts.DEFAULT_VOICE).exists()


async def test_synthesize_rejects_empty_text(cache_dir):
    with pytest.raises(tts.TTSError):
        await tts.synthesize("   ")


async def test_synthesize_rejects_overlong_text(cache_dir):
    with pytest.raises(tts.TTSError):
        await tts.synthesize("茶" * (tts.MAX_TEXT_LENGTH + 1))


async def test_synthesize_raises_on_empty_audio(cache_dir, monkeypatch):
    async def empty_stream(text, voice):
        return b""

    monkeypatch.setattr(tts, "_synthesize_to_bytes", empty_stream)

    with pytest.raises(tts.TTSError):
        await tts.synthesize("珍珠奶茶")


async def test_synthesize_times_out(cache_dir, monkeypatch):
    async def hanging_stream(text, voice):
        await asyncio.sleep(10)
        return b"never"

    monkeypatch.setattr(tts, "_synthesize_to_bytes", hanging_stream)
    monkeypatch.setattr(tts, "TTS_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(tts.TTSError):
        await tts.synthesize("珍珠奶茶")


async def test_cache_write_failure_still_returns_audio(cache_dir, monkeypatch):
    async def fake_stream(text, voice):
        return b"ID3-audio"

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(tts, "_synthesize_to_bytes", fake_stream)
    monkeypatch.setattr(tts.Path, "mkdir", explode)

    # 快取壞掉不該讓使用者聽不到聲音
    assert await tts.synthesize("珍珠奶茶") == b"ID3-audio"


def test_endpoint_reports_failure_as_502(client, cache_dir, monkeypatch):
    async def failing_stream(text, voice):
        raise tts.TTSError("語音合成逾時。")
        yield b""  # pragma: no cover

    monkeypatch.setattr(tts, "stream", failing_stream)

    response = client.post("/tts", json={"text": "已為您解析點單"})

    # 前端收到 502 時應照常顯示文字,不要卡住對話
    assert response.status_code == 502
    assert "逾時" in response.json()["detail"]


def test_endpoint_passes_custom_voice(client, cache_dir, monkeypatch):
    seen = {}

    async def fake_stream(text, voice):
        seen["voice"] = voice
        yield b"ID3-audio"

    monkeypatch.setattr(tts, "stream", fake_stream)

    client.post("/tts", json={"text": "測試", "voice": "zh-TW-YunJheNeural"})
    assert seen["voice"] == "zh-TW-YunJheNeural"


def test_endpoint_defaults_voice_when_blank(client, cache_dir, monkeypatch):
    seen = {}

    async def fake_stream(text, voice):
        seen["voice"] = voice
        yield b"ID3-audio"

    monkeypatch.setattr(tts, "stream", fake_stream)

    client.post("/tts", json={"text": "測試", "voice": ""})
    assert seen["voice"] == tts.DEFAULT_VOICE


async def test_stream_yields_cached_file_in_one_chunk(cache_dir, monkeypatch):
    async def fake_stream(text, voice):
        return b"ID3-cached"

    monkeypatch.setattr(tts, "_synthesize_to_bytes", fake_stream)
    await tts.synthesize("已加入購物車")

    chunks = [c async for c in tts.stream("已加入購物車")]
    assert b"".join(chunks) == b"ID3-cached"


async def test_stream_forwards_chunks_and_fills_cache(cache_dir, monkeypatch):
    class FakeCommunicate:
        def __init__(self, text, voice):
            pass

        async def stream(self):
            for part in (b"aa", b"bb", b"cc"):
                yield {"type": "audio", "data": part}

    import sys
    import types

    fake_module = types.ModuleType("edge_tts")
    fake_module.Communicate = FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", fake_module)

    chunks = [c async for c in tts.stream("珍珠奶茶")]

    # 邊收邊吐，不是收完才給
    assert chunks == [b"aa", b"bb", b"cc"]
    # 收完才落快取
    assert tts.cache_path("珍珠奶茶", tts.DEFAULT_VOICE).read_bytes() == b"aabbcc"


async def test_stream_rejects_bad_input_before_yielding(cache_dir):
    with pytest.raises(tts.TTSError):
        [c async for c in tts.stream("   ")]


def test_endpoint_streams_audio(client, cache_dir, monkeypatch):
    async def fake_stream(text, voice):
        for part in (b"aa", b"bb"):
            yield part

    monkeypatch.setattr(tts, "stream", fake_stream)

    response = client.post("/tts", json={"text": "已為您解析點單"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"aabb"


def test_endpoint_reports_validation_failure_before_streaming(client, cache_dir, monkeypatch):
    async def failing_stream(text, voice):
        raise tts.TTSError("文字是空的。")
        yield b""  # pragma: no cover

    monkeypatch.setattr(tts, "stream", failing_stream)

    response = client.post("/tts", json={"text": "   "})

    assert response.status_code == 502
    assert "空" in response.json()["detail"]
