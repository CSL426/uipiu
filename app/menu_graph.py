from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agent_parser import AgentCartItem, AgentOrderResponse
from app.models import NormalizedMenu


# 常用同義詞與維度關鍵詞庫
DIMENSION_KEYWORDS = {
    "temperature": ["溫度", "冰塊", "冰量", "冷熱", "飲料溫度", "冰度"],
    "sweetness": ["甜度", "糖度", "甜度選擇", "糖量"],
    "size": ["尺寸", "大小", "份量", "杯型", "份量選擇", "容量"],
    "topping": ["加料", "配料", "+plus", "++plus", "配料專區", "加料專區", "配料要求"],
}

DIMENSION_SYNONYMS = {
    "temperature": {
        "完全去冰": ["完全去冰(無碎冰)", "全去冰"],
        "去冰": ["不加冰", "無冰", "去冰(小碎冰)"],
        "微冰": ["少碎冰", "微冷", "微冰塊"],
        "少冰": ["少冰塊"],
        "正常冰": ["全冰"],
        "常溫": ["室溫"],
        "熱": ["熱飲", "做熱的", "溫熱"],
        "溫": ["溫飲"],
        "固定冰量": ["固定", "冰量固定", "甜度冰塊固定"],
    },
    "sweetness": {
        "無糖": ["0糖", "不加糖", "無", "去糖", "0分糖", "零分糖"],
        "一分糖": ["1分糖", "一分", "微微糖", "10%"],
        "二分糖": ["2分糖", "二分", "20%"],
        "微糖": ["3分糖", "三分糖", "30%"],
        "半糖": ["5分糖", "五分糖", "半", "50%"],
        "少糖": ["7分糖", "七分糖", "70%", "8分糖", "八分糖"],
        "正常糖": ["正常甜", "全糖", "標準", "100%", "10分糖"],
        "正常甜": ["正常糖", "全糖", "標準", "100%", "10分糖"],
        "固定甜度": ["固定", "甜度固定", "甜度冰塊固定"],
    },
    "size": {
        "大杯": ["大", "l", "大份", "大碗"],
        "中杯": ["中", "m", "中份", "中碗"],
        "小杯": ["小", "s", "小份", "小碗"],
        "l": ["大杯", "大", "大份"],
        "m": ["中杯", "中", "中份"],
        "s": ["小杯", "小", "小份"],
    },
}

# 中文數字對照
CHINESE_NUMBERS = {
    "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def _clean_item_name(name: str) -> str:
    return re.sub(r"[●/（）\(\)\+ＬＭＳ]", "", name).strip()


@dataclass
class OptionNode:
    id: str
    name: str
    price: int
    dimension_id: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class DimensionNode:
    id: str
    name: str
    min_select: int
    max_select: int
    dim_type: str  # temperature | sweetness | size | topping | other
    options: list[OptionNode] = field(default_factory=list)


@dataclass
class ItemNode:
    id: str
    name: str
    price: int
    category_name: str
    aliases: list[str] = field(default_factory=list)
    dimensions: list[DimensionNode] = field(default_factory=list)
    size_hint: str | None = None  # "大", "中", "小"


class MenuGraph:
    """Hierarchical Menu Knowledge Graph for zero-token fast matching and narrow-scope LLM routing."""

    def __init__(self, store_id: str, store_name: str = ""):
        self.store_id = store_id
        self.store_name = store_name
        self.items: list[ItemNode] = []
        self.item_by_id: dict[str, ItemNode] = {}
        self.name_index: dict[str, list[ItemNode]] = {}
        self.token_index: dict[str, set[str]] = {}  # token -> set of item_ids

    @classmethod
    def build_from_normalized_menu(cls, menu: NormalizedMenu) -> MenuGraph:
        graph = cls(store_id=menu.store_id, store_name=menu.store_name)
        group_map = {g.id: g for g in menu.groups}

        for item in menu.items:
            # 判斷尺寸前綴/後綴
            size_hint = None
            size_labels = {
                "大": "大", "中": "中", "小": "小",
                "L": "大", "M": "中", "S": "小",
                "大杯": "大", "中杯": "中",
            }
            for label, size in size_labels.items():
                if label in item.name or any(label in alias for alias in item.aliases):
                    size_hint = size
                    break

            dimensions: list[DimensionNode] = []
            for gid in item.group_ids:
                group = group_map.get(gid)
                if not group:
                    continue

                # 判斷維度類別
                dim_type = "other"
                lower_gname = group.name.lower()
                for dtype, keywords in DIMENSION_KEYWORDS.items():
                    if any(k in lower_gname for k in keywords):
                        dim_type = dtype
                        break

                option_nodes: list[OptionNode] = []
                for opt in group.options:
                    opt_aliases = list(opt.aliases)
                    # 去除括號註記（如 "溏心蛋(一顆)" -> "溏心蛋", "去冰(小碎冰)" -> "去冰"）
                    paren_stripped = re.sub(r"[\(（][^\)）]*[\)）]", "", opt.name).strip()
                    if paren_stripped and paren_stripped != opt.name:
                        opt_aliases.append(paren_stripped)

                    # 去除前綴 + 號（如 "+珍珠" -> "珍珠"）
                    plus_stripped = opt.name.lstrip("+＋").strip()
                    if plus_stripped and plus_stripped != opt.name:
                        opt_aliases.append(plus_stripped)

                    # 加入維度專屬同義詞（避免跨維度污染，如微冰被誤加微糖別名）
                    syn_map = DIMENSION_SYNONYMS.get(dim_type, {})
                    opt_lower = opt.name.lower().strip()
                    for std_name, syns in syn_map.items():
                        if std_name == opt_lower or std_name in opt.name or (paren_stripped and std_name in paren_stripped):
                            opt_aliases.extend(syns)
                        elif any(syn == opt_lower or (len(syn) > 1 and syn in opt.name) for syn in syns):
                            opt_aliases.append(std_name)

                    option_nodes.append(
                        OptionNode(
                            id=opt.id,
                            name=opt.name,
                            price=opt.price,
                            dimension_id=group.id,
                            aliases=list(set(opt_aliases)),
                        )
                    )

                # 核心手搖維度（溫度、甜度、尺寸）若有多個選項且非全部固定，在我們系統這層強制必選
                effective_min = group.min_select
                if dim_type in ("temperature", "sweetness", "size"):
                    if len(option_nodes) > 1 and not all("固定" in o.name for o in option_nodes):
                        effective_min = max(group.min_select, 1)

                dimensions.append(
                    DimensionNode(
                        id=group.id,
                        name=group.name,
                        min_select=effective_min,
                        max_select=group.max_select,
                        dim_type=dim_type,
                        options=option_nodes,
                    )
                )

            item_node = ItemNode(
                id=item.id,
                name=item.name,
                price=item.price,
                category_name=item.category_name or "",
                aliases=list(item.aliases),
                dimensions=dimensions,
                size_hint=size_hint,
            )

            graph.items.append(item_node)
            graph.item_by_id[item.id] = item_node

            # 建立名稱與別名索引
            names = [item.name, *item.aliases]
            # 建立簡化名稱（去掉點、斜線、特殊符號）
            clean_name = _clean_item_name(item.name)
            if clean_name and clean_name not in names:
                names.append(clean_name)

            for n in names:
                n_clean = n.lower().strip()
                graph.name_index.setdefault(n_clean, []).append(item_node)

            # 建立倒排字符索引
            for char in clean_name:
                if len(char.strip()) > 0:
                    graph.token_index.setdefault(char, set()).add(item.id)

        return graph

    def resolve_order_fast(self, user_text: str, default_contributor: str = "訪客") -> AgentOrderResponse | None:
        """Attempt to fast-resolve order directly from the graph without calling LLM.

        Supports single-line or multi-line batch orders. Returns AgentOrderResponse or None.
        """
        lines = [line.strip() for line in user_text.splitlines() if line.strip()]
        if len(lines) > 1:
            responses: list[AgentOrderResponse] = []
            for line in lines:
                sub_res = self._resolve_single_clause(line, default_contributor=default_contributor)
                if not sub_res:
                    return None  # 若有任何單項無法判斷，整體退回 LLM
                responses.append(sub_res)

            # 若有部分需要追問必選規格
            clarify_msgs = [r.reply_message for r in responses if r.action == "reply"]
            if clarify_msgs:
                return AgentOrderResponse(
                    action="reply",
                    items=[],
                    reply_message="\n".join(clarify_msgs),
                    raw_output="fast_graph_resolution_multi_clarify",
                )

            # 所有品項皆成功辨識
            all_items: list[AgentCartItem] = []
            for r in responses:
                all_items.extend(r.items)
            reply_lines = [r.reply_message for r in responses]
            return AgentOrderResponse(
                action="add_to_cart",
                items=all_items,
                reply_message="\n".join(reply_lines),
                raw_output="fast_graph_resolution_multi_success",
            )

        return self._resolve_single_clause(user_text.strip(), default_contributor=default_contributor)

    def _resolve_single_clause(self, user_text: str, default_contributor: str = "訪客") -> AgentOrderResponse | None:
        # 1. 抽取姓名
        contributor = default_contributor
        name_match = re.search(r"^(?P<name>[\u4e00-\u9fa5A-Za-z0-9_]{1,6})(?:\s*(?:要|點|幫我點|來|\+))", user_text)
        if name_match:
            candidate_name = name_match.group("name").strip()
            if candidate_name not in ["我", "請", "幫我", "麻煩", "直接"]:
                contributor = candidate_name

        # 2. 抽取數量
        quantity = 1
        qty_match = re.search(r"(?:\+|\b)(\d+)\s*(?:杯|份|個|瓶|碗)?", user_text)
        if qty_match:
            quantity = max(1, int(qty_match.group(1)))
        else:
            for ch, num in CHINESE_NUMBERS.items():
                if f"{ch}杯" in user_text or f"{ch}份" in user_text or f"{ch}個" in user_text or f"{ch}碗" in user_text:
                    quantity = num
                    break

        # 3. 抽取尺寸偏好
        size_req = None
        if any(w in user_text for w in ["大杯", "特大", "大份", "大碗", "大", "L", "l"]):
            size_req = "大"
        elif any(w in user_text for w in ["中杯", "中份", "中碗", "中", "M", "m"]):
            size_req = "中"
        elif any(w in user_text for w in ["小杯", "小份", "小碗", "小", "S", "s"]):
            size_req = "小"

        # 4. 商品定位（Graph 節點比對）
        matched_item = self._find_best_item(user_text, size_req=size_req)
        if not matched_item:
            return None  # 找不到精確商品，交給 LLM

        # 5. 規格選項對齊（Non-overlapping Span Resolution）
        candidate_terms: list[tuple[int, str, OptionNode, DimensionNode]] = []
        clean_text = user_text.lower()

        for dim in matched_item.dimensions:
            for opt in dim.options:
                opt_terms = [opt.name, *opt.aliases]
                for term in set(opt_terms):
                    t_lower = term.lower().strip()
                    if len(t_lower) >= 1:
                        candidate_terms.append((len(t_lower), t_lower, opt, dim))

        # 降序依長度排序（優先鎖定最長專有名詞，避免短詞誤傷）
        candidate_terms.sort(key=lambda x: x[0], reverse=True)

        selected_options: list[OptionNode] = []
        options_per_dim: dict[str, list[OptionNode]] = {}
        occupied_spans: set[int] = set()

        for _, term, opt, dim in candidate_terms:
            current_in_dim = options_per_dim.get(dim.id, [])
            if len(current_in_dim) >= dim.max_select:
                continue

            start_idx = 0
            while True:
                pos = clean_text.find(term, start_idx)
                if pos == -1:
                    break
                span = set(range(pos, pos + len(term)))
                if not (span & occupied_spans):
                    occupied_spans |= span
                    selected_options.append(opt)
                    options_per_dim.setdefault(dim.id, []).append(opt)
                    break
                start_idx = pos + 1

        # 5.5 單一固定選項自動帶入（如只有「固定冰量」或單一尺寸「M」時自動帶入，不需使用者贅述）
        for dim in matched_item.dimensions:
            if len(dim.options) == 1 and dim.id not in options_per_dim:
                single_opt = dim.options[0]
                selected_options.append(single_opt)
                options_per_dim.setdefault(dim.id, []).append(single_opt)

        # 6. 約束檢查：若缺少必選維度，產生友善追問
        missing_required_dims: list[DimensionNode] = []
        for dim in matched_item.dimensions:
            matched_count = len(options_per_dim.get(dim.id, []))
            if matched_count < dim.min_select:
                missing_required_dims.append(dim)

        if missing_required_dims:
            dim_names = "與".join(d.name for d in missing_required_dims)
            opt_examples = [", ".join(o.name for o in d.options[:4]) for d in missing_required_dims]
            reply_msg = (
                f"{contributor}您好，您點的「{matched_item.name}」需要選擇{dim_names}，"
                f"請問想選哪一種呢？（例如：{opt_examples[0]}）"
            )
            return AgentOrderResponse(
                action="reply",
                items=[],
                reply_message=reply_msg,
                raw_output="fast_graph_resolution_clarify",
            )

        # 7. 規格齊全，直接加入購物車
        reply_msg = f"{contributor}，您的 {matched_item.name}（{', '.join(o.name for o in selected_options) or '標準'}）已加入購物車！"
        return AgentOrderResponse(
            action="add_to_cart",
            items=[
                AgentCartItem(
                    customer_name=contributor,
                    item_id=matched_item.id,
                    item_name=matched_item.name,
                    quantity=quantity,
                    selection_option_ids=[o.id for o in selected_options],
                    selection_option_names=[o.name for o in selected_options],
                )
            ],
            reply_message=reply_msg,
            raw_output="fast_graph_resolution_success",
        )

    def _find_best_item(self, user_text: str, size_req: str | None = None) -> ItemNode | None:
        """Find the single best matching ItemNode in the graph."""
        text_lower = user_text.lower()
        candidates: list[tuple[int, ItemNode]] = []

        for item in self.items:
            clean_name = _clean_item_name(item.name).lower()
            aliases_lower = [a.lower() for a in item.aliases]

            score = 0
            # 精確名稱包含
            if clean_name in text_lower or any(a in text_lower for a in aliases_lower):
                score += len(clean_name) * 10
            elif any(token in text_lower for token in clean_name.split() if len(token) > 1):
                score += 15

            # 尺寸偏好加分
            if score > 0 and size_req and item.size_hint:
                if item.size_hint == size_req:
                    score += 20
                else:
                    score -= 10

            if score > 0:
                candidates.append((score, item))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        top_score, top_item = candidates[0]
        # 若最高分顯著高於次高分，或者只有一個高分匹配
        if len(candidates) > 1:
            second_score, second_item = candidates[1]
            if top_score - second_score < 5:
                # 檢查是否只是尺寸不同（如 大●四季春青茶 vs 中●四季春青茶）
                clean_top = _clean_item_name(top_item.name).lower()
                clean_second = _clean_item_name(second_item.name).lower()
                clean_top_base = re.sub(r"^[大中小]|(?:\([大中小]\))|[大中小]$", "", clean_top)
                clean_second_base = re.sub(r"^[大中小]|(?:\([大中小]\))|[大中小]$", "", clean_second)
                if clean_top_base == clean_second_base and top_item.size_hint and second_item.size_hint:
                    # 偏好大杯（台灣手搖飲常規預設）
                    if top_item.size_hint == "大":
                        return top_item
                    elif second_item.size_hint == "大":
                        return second_item
                return None  # 存在品項歧義，交給 LLM 或追問

        return top_item

    def extract_narrow_subgraph_prompt(self, user_text: str, max_items: int = 12) -> str:
        """Extract a highly compact, focused subgraph prompt with only the most relevant items."""
        # 依關鍵字關聯度挑選候選商品節點
        scored: list[tuple[int, ItemNode]] = []
        for item in self.items:
            score = 0
            clean_name = _clean_item_name(item.name).lower()
            for token in clean_name:
                if token in user_text:
                    score += 1
            for a in item.aliases:
                if a in user_text:
                    score += 10
            scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_items = [it for s, it in scored if s > 0][:max_items]
        if not top_items:
            top_items = [it for _, it in scored[:max_items]]

        lines = [f"【店家名稱】: {self.store_name or '餐飲手搖店'} (局部精選菜單圖譜)"]
        lines.append("\n【商品與可選規格清單】:")
        for item in top_items:
            cat = f"[{item.category_name}] " if item.category_name else ""
            lines.append(f"\n- 商品ID: {item.id} | {cat}{item.name} | 價格: ${item.price}")
            for dim in item.dimensions:
                req = f"必選{dim.min_select}項" if dim.min_select > 0 else f"選填(最多{dim.max_select}項)"
                opts = ", ".join(f"{o.name}(ID:{o.id}{f',+${o.price}' if o.price > 0 else ''})" for o in dim.options)
                lines.append(f"  * 規格 [{dim.name}] (規則: {req}): {opts}")

        return "\n".join(lines)
