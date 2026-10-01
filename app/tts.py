import asyncio
import hashlib
import logging
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "zh-TW-HsiaoChenNeural"
DEFAULT_CACHE_DIR = Path("/app/app/data/tts-cache")
CACHE_DIR = Path(os.getenv("TTS_CACHE_DIR", str(DEFAULT_CACHE_DIR)))
TTS_TIMEOUT_SECONDS = float(os.getenv("TTS_TIMEOUT_SECONDS", "15"))
MAX_TEXT_LENGTH = 500

# 快取檔名只允許這些字元,避免 hash 以外的東西被當成路徑
_CACHE_NAME_RE = re.compile(r"^[0-9a-f]{32}\.mp3$")


class TTSError(RuntimeError):
    """語音合成失敗,呼叫端應退回純文字。"""


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _clean_and_validate(text: str) -> str:
    cleaned = _normalize_text(text)
    if not cleaned:
        raise TTSError("文字是空的。")
    if len(cleaned) > MAX_TEXT_LENGTH:
        raise TTSError(f"文字超過 {MAX_TEXT_LENGTH} 字上限。")
    return cleaned


def cache_key(text: str, voice: str) -> str:
    """同一句話配同一個聲音才算同一份快取。"""
    digest = hashlib.md5(f"{voice}\x00{_normalize_text(text)}".encode("utf-8"))
    return f"{digest.hexdigest()}.mp3"


def cache_path(text: str, voice: str) -> Path:
    name = cache_key(text, voice)
    if not _CACHE_NAME_RE.match(name):
        raise TTSError("快取檔名格式異常。")
    return CACHE_DIR / name


async def _synthesize_to_bytes(text: str, voice: str) -> bytes:
    try:
        import edge_tts
    except ImportError as exc:  # pragma: no cover - 相依套件缺失時才會走到
        raise TTSError("edge-tts 未安裝。") from exc

    communicate = edge_tts.Communicate(text, voice)
    chunks: list[bytes] = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])

    return b"".join(chunks)


async def stream(text: str, voice: str = DEFAULT_VOICE) -> AsyncIterator[bytes]:
    """邊合成邊吐音檔,讓瀏覽器可以先播前面幾個位元組。

    命中快取就直接吐整個檔;沒命中就一邊轉發 EdgeTTS 的 chunk 一邊累積,
    全部收完才寫入快取——中途斷線不會留下半截檔案。
    """
    cleaned = _clean_and_validate(text)

    path = cache_path(cleaned, voice)
    if path.exists():
        try:
            yield path.read_bytes()
            return
        except OSError:
            logger.warning("讀取 TTS 快取失敗,改為重新合成: %s", path.name)

    try:
        import edge_tts
    except ImportError as exc:  # pragma: no cover - 相依套件缺失時才會走到
        raise TTSError("edge-tts 未安裝。") from exc

    chunks: list[bytes] = []
    try:
        communicate = edge_tts.Communicate(cleaned, voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio" and chunk["data"]:
                chunks.append(chunk["data"])
                yield chunk["data"]
    except Exception as exc:
        raise TTSError(f"語音合成失敗:{exc}") from exc

    audio = b"".join(chunks)
    if not audio:
        raise TTSError("語音合成回傳空音檔。")

    _write_cache(path, audio)


def _write_cache(path: Path, audio: bytes) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # 先寫暫存檔再 rename,避免併發請求讀到寫到一半的檔案
        tmp = path.with_suffix(".mp3.tmp")
        tmp.write_bytes(audio)
        tmp.replace(path)
    except OSError:
        logger.warning("寫入 TTS 快取失敗,這次仍回傳音檔: %s", path.name)


async def synthesize(text: str, voice: str = DEFAULT_VOICE) -> bytes:
    """把文字轉成 mp3。命中快取就直接回,否則打 EdgeTTS 再寫入快取。

    比比拉布的固定句型(「已為您解析點單」等)會重複出現,快取能把這些變成零延遲,
    順便避開 EdgeTTS 的速率限制。
    """
    cleaned = _clean_and_validate(text)

    path = cache_path(cleaned, voice)
    if path.exists():
        try:
            return path.read_bytes()
        except OSError:
            logger.warning("讀取 TTS 快取失敗,改為重新合成: %s", path.name)

    try:
        audio = await asyncio.wait_for(
            _synthesize_to_bytes(cleaned, voice),
            timeout=TTS_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise TTSError(f"語音合成逾時({TTS_TIMEOUT_SECONDS} 秒)。") from exc
    except TTSError:
        raise
    except Exception as exc:
        raise TTSError(f"語音合成失敗:{exc}") from exc

    # 空音檔不寫進快取,否則之後每次都會命中一個播不出聲音的檔案
    if not audio:
        raise TTSError("語音合成回傳空音檔。")

    _write_cache(path, audio)
    return audio
