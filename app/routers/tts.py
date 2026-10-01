from collections.abc import AsyncIterator
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import tts

logger = logging.getLogger(__name__)

router = APIRouter()


class TtsRequest(BaseModel):
    text: str
    voice: str = ""


@router.post("/tts")
async def synthesize_speech(request: TtsRequest) -> StreamingResponse:
    """把比比拉布的回覆轉成語音。

    與 agent-order 分開呼叫：解析結果（預覽卡）可以立刻顯示，語音晚幾百毫秒再開口，
    不讓 TTS 的延遲擋住畫面。合成失敗時前端應照常顯示文字。
    """
    voice = request.voice or tts.DEFAULT_VOICE

    # 先確保參數合法，讓格式錯誤仍以 502 回報；串流一旦開始就無法再改狀態碼
    try:
        stream = tts.stream(request.text, voice)
        first = await anext(stream)
    except tts.TTSError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except StopAsyncIteration:
        raise HTTPException(status_code=502, detail="語音合成回傳空音檔。") from None

    async def body() -> AsyncIterator[bytes]:
        yield first
        try:
            async for chunk in stream:
                yield chunk
        except tts.TTSError as exc:
            # 串流中途失敗只能中斷，前端會安靜退回純文字
            logger.warning("TTS 串流中斷: %s", exc)

    return StreamingResponse(
        body(),
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
