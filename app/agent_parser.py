import asyncio
import json
import logging
import math
import os
import re
import shutil
import signal
from typing import Literal

from pydantic import BaseModel, Field

from app.models import NormalizedMenu

logger = logging.getLogger(__name__)


class AgentCartItem(BaseModel):
    customer_name: str = "訪客"
    item_id: str
    item_name: str
    quantity: int = 1
    selection_option_ids: list[str] = Field(default_factory=list)
    selection_option_names: list[str] = Field(default_factory=list)


class AgentOrderResponse(BaseModel):
    action: Literal["add_to_cart", "reply"]
    items: list[AgentCartItem] = Field(default_factory=list)
    reply_message: str = ""
    raw_output: str = ""


def filter_relevant_menu(user_text: str, menu: NormalizedMenu, max_candidates: int = 30) -> NormalizedMenu:
    """Slice menu candidates based on user text to prevent massive prompts on huge menus."""
    if len(menu.items) <= max_candidates:
        return menu

    tokens = [t.strip().lower() for t in user_text.replace("，", " ").replace("。", " ").split() if len(t.strip()) > 1]
    scored_items = []
    for item in menu.items:
        score = 0
        name_lower = item.name.lower()
        aliases_lower = [a.lower() for a in item.aliases]
        for token in tokens:
            if token in name_lower or any(token in a for a in aliases_lower):
                score += 10
            for char in token:
                if char in name_lower:
                    score += 1
        scored_items.append((score, item))

    scored_items.sort(key=lambda x: x[0], reverse=True)
    top_items = [item for score, item in scored_items if score > 0]
    if len(top_items) < 5:
        top_items = [item for _, item in scored_items[:max_candidates]]
    else:
        top_items = top_items[:max_candidates]

    needed_group_ids = set()
    for it in top_items:
        needed_group_ids.update(it.group_ids)

    filtered_groups = [g for g in menu.groups if g.id in needed_group_ids]
    return NormalizedMenu(
        store_id=menu.store_id,
        store_name=menu.store_name,
        items=top_items,
        groups=filtered_groups,
        categories=menu.categories,
    )


def format_menu_for_prompt(menu: NormalizedMenu) -> str:
    """Format normalized menu into a compact, hierarchical human- and LLM-friendly description."""
    lines: list[str] = [f"【店家名稱】: {menu.store_name or '手搖飲店'}"]
    group_map = {group.id: group for group in menu.groups}

    lines.append("\n【商品與菜單品項清單】:")
    for item in menu.items:
        cat_str = f"[{item.category_name}] " if item.category_name else ""
        lines.append(f"\n- 商品ID: {item.id} | {cat_str}{item.name} | 價格: ${item.price}")
        for gid in item.group_ids:
            g = group_map.get(gid)
            if not g:
                continue
            req = f"必選 {g.min_select} 項" if g.min_select > 0 else f"選填 (最多 {g.max_select} 項)"
            opts = ", ".join(
                f"{opt.name}(ID:{opt.id}{f', +${opt.price}' if opt.price > 0 else ''})"
                for opt in g.options
            )
            lines.append(f"  * 規格群組 [{g.name}] (規則: {req}): {opts}")

    return "\n".join(lines)


def build_order_prompt(user_text: str, menu: NormalizedMenu) -> str:
    try:
        from app.menu_graph import MenuGraph

        graph = MenuGraph.build_from_normalized_menu(menu)
        menu_text = graph.extract_narrow_subgraph_prompt(user_text, max_items=15)
    except Exception as exc:
        logger.warning(f"Error building narrow subgraph prompt: {exc}, using fallback")
        sliced_menu = filter_relevant_menu(user_text, menu)
        menu_text = format_menu_for_prompt(sliced_menu)

    return f"""你是一個精通台灣餐飲手搖點單的專業 AI Agent。
你的任務是根據這間店家的「真實菜單與規格清單」，解析使用者的自然語言點單，並決定呼叫工具。

{menu_text}

---
【工具決策說明】
你有兩個行動工具（Action）：
1. `add_to_cart`：當使用者點餐資訊足夠（必選規格皆已包含或可合理推斷時），請呼叫此工具將品項加入購物車。若使用者同時點了多份，請在 items 陣列中列出每一筆。
2. `reply`：若使用者缺少必選規格（例如強制要求選冷熱或甜度但使用者未指定）、或品項不存在、或使用者在詢問菜單問題/推薦，請直接友善親切地以繁體中文回覆追問或解答。

【輸出格式規範】
你必須「只輸出合法的純 JSON」，不要加上任何 Markdown 語法標籤（如 ```json），不要有額外說明文字。格式如下：

若加入購物車：
{{
  "action": "add_to_cart",
  "items": [
    {{
      "customer_name": "點單人姓名（若文字中未提及則填'訪客'）",
      "item_id": "商品ID（字串，必須精確匹配菜單中的ID）",
      "item_name": "商品名稱（必須完全符合菜單品名）",
      "quantity": 數量（整數，預設1）,
      "selection_option_ids": ["選項ID字串列表，必須完全符合該商品適用的選項ID"],
      "selection_option_names": ["選項名稱列表，如'半糖', '微冰'"]
    }}
  ],
  "reply_message": "加入購物車的簡短確認訊息（繁體中文）"
}}

若需要回覆或追問缺少的規格：
{{
  "action": "reply",
  "items": [],
  "reply_message": "親切向使用者說明的文字（繁體中文）"
}}

【使用者輸入文字】:
{user_text}
"""


def _clean_json_markdown(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip ```json or ```
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    return cleaned.strip()


class AgentParser:
    def __init__(self, use_agy: bool = True, enable_fast_graph: bool = True):
        self.use_agy = use_agy
        self.enable_fast_graph = enable_fast_graph
        try:
            timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
        except ValueError:
            timeout = 30.0
        self.timeout_seconds = (
            min(timeout, 120.0) if math.isfinite(timeout) and timeout > 0 else 30.0
        )

    async def parse(
        self,
        user_text: str,
        menu: NormalizedMenu,
        default_contributor: str = "訪客",
    ) -> AgentOrderResponse:
        # Tier 1: MenuGraph 結構化圖搜尋與規格驗證 (0-token, 0ms 快速路徑)
        if self.enable_fast_graph:
            try:
                from app.menu_graph import MenuGraph

                graph = MenuGraph.build_from_normalized_menu(menu)
                fast_res = graph.resolve_order_fast(user_text, default_contributor=default_contributor)
                if fast_res is not None:
                    return fast_res
            except Exception as exc:
                logger.warning(f"Fast graph resolution encountered error, falling back to LLM: {exc}")

        # Tier 2: 圖譜精準剪枝後的 Narrow Subgraph 傳遞給 LLM
        prompt = build_order_prompt(user_text, menu)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                raw_output = await self._run_llm(prompt)
        except TimeoutError:
            logger.warning("LLM request timed out")
            return self._failure_reply("AI 點餐回應逾時")
        except Exception as exc:
            # Provider exceptions can include credentials and request contents.
            logger.warning("LLM request failed (%s)", type(exc).__name__)
            return self._failure_reply("AI 點餐目前暫時無法使用")

        try:
            data = json.loads(_clean_json_markdown(raw_output))
            result = AgentOrderResponse.model_validate(data)
            if (
                result.action == "add_to_cart" and not result.items
            ) or (result.action == "reply" and result.items):
                raise ValueError("Inconsistent order response")
            result.raw_output = raw_output
            return result
        except Exception as exc:
            logger.warning("Invalid LLM response (%s)", type(exc).__name__)
            return self._failure_reply("AI 暫時無法確認點餐內容")

    @staticmethod
    def _failure_reply(reason: str) -> AgentOrderResponse:
        return AgentOrderResponse(
            action="reply",
            reply_message=(
                f"抱歉，{reason}，這次沒有加入購物車。"
                "請稍後再試，或使用手動點餐選擇品項。"
            ),
        )

    async def _run_llm(self, prompt: str) -> str:
        # Check if agy is available
        agy_path = shutil.which("agy")
        if self.use_agy and agy_path:
            return await self._run_agy(agy_path, prompt)

        # Fallback to google-genai if GEMINI_API_KEY is present
        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            return await self._run_genai_sdk(api_key, prompt)

        raise RuntimeError("No LLM backend available. Neither `agy` in PATH nor GEMINI_API_KEY is set.")

    async def _run_agy(self, agy_path: str, prompt: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            agy_path,
            "-p",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # agy can spawn workers; cancellation must stop their process group.
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                stdout, _ = await proc.communicate()
        except (TimeoutError, asyncio.CancelledError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.communicate()
            raise
        if proc.returncode != 0:
            raise RuntimeError(f"agy exited with code {proc.returncode}")
        return stdout.decode(errors="replace").strip()

    async def _run_genai_sdk(self, api_key: str, prompt: str) -> str:
        from google import genai
        from google.genai import types

        with genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=max(1, math.ceil(self.timeout_seconds * 1000)),
            ),
        ) as client:
            async with client.aio as async_client:
                async with asyncio.timeout(self.timeout_seconds):
                    response = await async_client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=prompt,
                    )
                    return response.text or ""
