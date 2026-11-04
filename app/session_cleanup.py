"""定期把閒置太久的團從本站移除。只刪本站紀錄，平台上的團和單不受影響。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import logging

from app import dependencies

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 3600.0


def purge_once() -> list[str]:
    removed = dependencies.get_cart_store().purge_idle_sessions()
    for name in removed:
        logger.info("閒置過久，已從本站移除團：%s", name)
    return removed


async def _loop() -> None:
    while True:
        try:
            await asyncio.to_thread(purge_once)
        except Exception:
            # 資料庫暫時被鎖之類的問題不該讓清除工作永遠停掉，下一輪再試
            logger.exception("清除閒置的團失敗")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


@asynccontextmanager
async def running() -> AsyncIterator[None]:
    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
