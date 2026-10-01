import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_urlsafe
from typing import TYPE_CHECKING

from app.models import (
    CartLineSelection,
    CartSessionDetail,
    CartSessionLine,
    CartSessionMenu,
    CartSessionSummary,
    MenuGroup,
    MenuItem,
    MenuOption,
    NormalizedMenu,
    OfficialGroupBuySummary,
    StoreDeliverySummary,
    validate_menu_snapshot,
)

if TYPE_CHECKING:
    # order_hooks 會 import cart 模型，執行期用函式內 import 避開循環
    from app.order_hooks import HookContext

DB_PATH = Path(os.getenv("CART_DB_PATH", str(Path(__file__).resolve().parent / "data" / "cart.db")))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    group_buy_url TEXT NOT NULL,
    store_id TEXT NOT NULL,
    group_buy_code TEXT NOT NULL,
    session_name TEXT NOT NULL,
    store_name TEXT NOT NULL DEFAULT '',
    delivery_minimum INTEGER,
    delivery_description TEXT NOT NULL DEFAULT '',
    order_status_reason TEXT NOT NULL DEFAULT '',
    menu_json TEXT NOT NULL DEFAULT '{}',
    menu_fetched_at TEXT,
    owner_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(store_id, group_buy_code)
);

CREATE TABLE IF NOT EXISTS lines (
    line_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    contributor_name TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    category_name TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    selections_json TEXT NOT NULL DEFAULT '[]',
    line_total INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lines_session ON lines(session_id);

-- 每次「送進 Nidin」一筆：submission_id 由前端產生，同一個 id 重送不會在 Nidin 再下一次單
CREATE TABLE IF NOT EXISTS order_submissions (
    submission_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    contributor_name TEXT NOT NULL,
    status TEXT NOT NULL,
    official_order_id TEXT,
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_order ON order_submissions(session_id, official_order_id);

CREATE TABLE IF NOT EXISTS payments (
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    contributor_name TEXT NOT NULL,
    paid INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, contributor_name)
);

CREATE TABLE IF NOT EXISTS leader_auth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token TEXT NOT NULL,
    user_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
"""


def _resolve_official_selections(
    menu: NormalizedMenu, item_id: str, option_names: list[str], option_groups: list[str] | None = None
) -> list[dict[str, object]]:
    """Nidin 只給規格名稱；對回本站菜單的選項 id 與加價，「+1 跟著點」才複製得出完整規格。

    有群組名稱時先要求群組也對得上（不同群組可能都有「L」「少冰」），對不到才退回只比選項名稱。
    對不到（菜單改過、品項下架）的保留名稱、用 "opt" 佔位，只供顯示。
    """
    item = next((it for it in menu.items if it.id == item_id), None)
    groups = [group for group in menu.groups if item is not None and group.id in item.group_ids]
    group_names = option_groups or [""] * len(option_names)
    resolved: list[dict[str, object]] = []
    for name, group_name in zip(option_names, group_names):
        match = next(
            (
                (group, option)
                for group in groups
                if group_name and group.name == group_name
                for option in group.options
                if option.name == name
            ),
            None,
        ) or next(
            ((group, option) for group in groups for option in group.options if option.name == name),
            None,
        )
        if match is None:
            resolved.append({"group_id": "opt", "group_name": "選項", "option_id": "opt", "option_name": name, "price": 0})
        else:
            group, option = match
            resolved.append({
                "group_id": group.id, "group_name": group.name,
                "option_id": option.id, "option_name": option.name, "price": option.price,
            })
    return resolved


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _line_total(item_price: int, selections: list[CartLineSelection], quantity: int) -> int:
    return (item_price + sum(s.price for s in selections)) * quantity


class CartStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "menu_fetched_at" not in columns:
                # Legacy updated_at includes cart edits, so its fetch time is unknown.
                conn.execute("ALTER TABLE sessions ADD COLUMN menu_fetched_at TEXT")

            if "owner_id" not in columns:
                conn.execute("ALTER TABLE sessions ADD COLUMN owner_id TEXT")

            if "provider" not in columns:
                # 加這個欄位以前的團都是 Nidin
                conn.execute("ALTER TABLE sessions ADD COLUMN provider TEXT NOT NULL DEFAULT 'nidin'")

            if "nidin_order_id" not in columns:
                # 從「我的 Nidin 團購」匯入時記下 Nidin 團長單號，清單才知道哪些已經匯入過
                conn.execute("ALTER TABLE sessions ADD COLUMN nidin_order_id TEXT")

            submission_columns = {row["name"] for row in conn.execute("PRAGMA table_info(order_submissions)")}
            if "request_hash" not in submission_columns:
                conn.execute("ALTER TABLE order_submissions ADD COLUMN request_hash TEXT NOT NULL DEFAULT ''")
            if "pending_order_id" not in submission_columns:
                # 結果不明時先記下 Nidin 跟團購物車的單號，同步時拿來對帳
                conn.execute("ALTER TABLE order_submissions ADD COLUMN pending_order_id TEXT")
            if "cancel_ref" not in submission_columns:
                # 取消這張單要用的平台憑證（Ocard：這張跟團單專屬的裝置權杖）
                conn.execute("ALTER TABLE order_submissions ADD COLUMN cancel_ref TEXT")

            payment_columns = {row["name"] for row in conn.execute("PRAGMA table_info(payments)")}
            if "paid_amount" not in payment_columns:
                # 按「已收款」當下這個人的應付金額；之後應付變多（又點了）就自動不再算已收款
                conn.execute("ALTER TABLE payments ADD COLUMN paid_amount INTEGER")

            line_columns = {row["name"] for row in conn.execute("PRAGMA table_info(lines)")}
            if "official_order_id" not in line_columns:
                # 標記「從 Nidin 同步進來」的品項，重新同步時才能整批換掉而不是一直疊加
                conn.execute("ALTER TABLE lines ADD COLUMN official_order_id TEXT")

    def claim_legacy_sessions(self, owner_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET owner_id=? WHERE owner_id IS NULL",
                (owner_id,),
            )

    def session_owner(self, session_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT owner_id FROM sessions WHERE session_id=?", (session_id,),
            ).fetchone()
        if row is None:
            raise KeyError("找不到這個團購。")
        return row["owner_id"]

    # ---- 送單紀錄 --------------------------------------------------------------
    # status：pending 送出中或結果不明（逾時）、submitted 已成立、failed 確定沒送出、cancelled 已取消

    def begin_submission(
        self, submission_id: str, session_id: str, contributor_name: str, request_hash: str
    ) -> dict[str, str] | None:
        """登記一次送單。已登記過就回傳既有紀錄（不重送），否則回 None。

        failed 的紀錄確定沒送出，可以重試，連內容換了也可以（改成新的團或內容）。
        其他狀態若 request_hash 不同，代表同一個 id 被拿去送別的東西，回傳紀錄並標 conflict。
        """
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM order_submissions WHERE submission_id=?", (submission_id,)
            ).fetchone()
            if row is not None and row["status"] != "failed":
                existing = dict(row)
                existing["conflict"] = row["request_hash"] != request_hash
                return existing
            conn.execute(
                "INSERT INTO order_submissions "
                "(submission_id, session_id, contributor_name, status, official_order_id, message, created_at, updated_at, request_hash, pending_order_id) "
                "VALUES (?, ?, ?, 'pending', NULL, '', ?, ?, ?, NULL) "
                "ON CONFLICT(submission_id) DO UPDATE SET status='pending', message='', session_id=excluded.session_id, "
                "contributor_name=excluded.contributor_name, request_hash=excluded.request_hash, "
                "pending_order_id=NULL, updated_at=excluded.updated_at",
                (submission_id, session_id, contributor_name, now, now, request_hash),
            )
        return None

    def mark_submission_uncertain(
        self, submission_id: str, pending_order_id: str | None, cancel_ref: str | None = None
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE order_submissions SET status='pending', pending_order_id=?, cancel_ref=COALESCE(?, cancel_ref), "
                "message='送出後沒有收到平台的確定結果', updated_at=? WHERE submission_id=?",
                (pending_order_id, cancel_ref, _now_iso(), submission_id),
            )

    def reconcile_submissions(self, session_id: str, present_order_ids: set[str], *, grace_seconds: float) -> None:
        """用剛同步回來的 Nidin 跟團單對帳「結果不明」的送單。

        在 Nidin 上找到＝其實成立了（改成 submitted，點單人重送同一個 id 就能拿回取消憑證）；
        超過寬限時間還找不到＝沒成立（改成 failed，可以安全重試）。
        """
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT submission_id, pending_order_id, updated_at FROM order_submissions "
                "WHERE session_id=? AND status='pending'",
                (session_id,),
            ).fetchall()
            for row in rows:
                if row["pending_order_id"] and row["pending_order_id"] in present_order_ids:
                    conn.execute(
                        "UPDATE order_submissions SET status='submitted', official_order_id=?, message='', updated_at=? "
                        "WHERE submission_id=?",
                        (row["pending_order_id"], _now_iso(), row["submission_id"]),
                    )
                elif (now - datetime.fromisoformat(row["updated_at"])).total_seconds() > grace_seconds:
                    conn.execute(
                        "UPDATE order_submissions SET status='failed', message='對帳後平台上沒有這筆', updated_at=? "
                        "WHERE submission_id=?",
                        (_now_iso(), row["submission_id"]),
                    )

    def finish_submission(
        self,
        submission_id: str,
        *,
        status: str,
        official_order_id: str | None = None,
        message: str = "",
        cancel_ref: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE order_submissions SET status=?, official_order_id=COALESCE(?, official_order_id), "
                "cancel_ref=COALESCE(?, cancel_ref), message=?, updated_at=? WHERE submission_id=?",
                (status, official_order_id, cancel_ref, message, _now_iso(), submission_id),
            )

    def submission_for_order(self, session_id: str, official_order_id: str) -> dict[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM order_submissions WHERE session_id=? AND official_order_id=?",
                (session_id, official_order_id),
            ).fetchone()
        return dict(row) if row else None

    def remove_official_order(self, session_id: str, official_order_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM lines WHERE session_id=? AND official_order_id=?",
                (session_id, official_order_id),
            )
            conn.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (_now_iso(), session_id))

    def nidin_order_id(self, session_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT nidin_order_id FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown cart session: {session_id}")
        return row["nidin_order_id"]

    def link_nidin_order(self, session_id: str, nidin_order_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET nidin_order_id=? WHERE session_id=?",
                (nidin_order_id, session_id),
            )

    def sessions_by_nidin_order(self, owner_id: str) -> dict[str, str]:
        """{Nidin 團長單號: 本站 session_id}，只看這位主揪自己的團。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT nidin_order_id, session_id FROM sessions "
                "WHERE owner_id=? AND nidin_order_id IS NOT NULL",
                (owner_id,),
            ).fetchall()
        return {str(row["nidin_order_id"]): str(row["session_id"]) for row in rows}

    def list_owned_sessions(self, owner_id: str) -> list[CartSessionSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE owner_id=? ORDER BY updated_at DESC",
                (owner_id,),
            ).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def _owed_by_contributor(self, conn: sqlite3.Connection, session_id: str) -> dict[str, int]:
        rows = conn.execute(
            "SELECT contributor_name, SUM(line_total) AS owed FROM lines WHERE session_id=? GROUP BY contributor_name",
            (session_id,),
        ).fetchall()
        return {row["contributor_name"]: int(row["owed"] or 0) for row in rows}

    def get_payments(self, session_id: str) -> dict[str, bool]:
        """已收款＝標記時收到的金額涵蓋目前應付。

        不再靠「有新單就刪收款紀錄」：同步會一直重寫品項，而且任何人都能用別人的名字下單再取消，
        刪紀錄的做法會讓人無聲清掉別人的收款狀態。paid_amount 為 NULL 的是改版前的舊紀錄，照舊採信。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT contributor_name, paid, paid_amount FROM payments WHERE session_id=?",
                (session_id,),
            ).fetchall()
            owed = self._owed_by_contributor(conn, session_id)
        return {
            row["contributor_name"]: bool(row["paid"]) and (
                row["paid_amount"] is None or owed.get(row["contributor_name"], 0) <= row["paid_amount"]
            )
            for row in rows
        }

    def set_payment(self, session_id: str, name: str, paid: bool) -> dict[str, bool]:
        with self._connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM lines WHERE session_id=? AND contributor_name=?",
                (session_id, name),
            ).fetchone()
            if not exists:
                raise ValueError("此團購沒有這位點餐者。")
            owed = self._owed_by_contributor(conn, session_id).get(name, 0)
            conn.execute(
                "INSERT INTO payments (session_id, contributor_name, paid, paid_amount) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(session_id, contributor_name) DO UPDATE SET paid=excluded.paid, paid_amount=excluded.paid_amount",
                (session_id, name, int(paid), owed if paid else None),
            )
        return self.get_payments(session_id)

    def list_sessions(self) -> list[CartSessionSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_summary(row) for row in rows]

    def create_or_get_session(
        self,
        *,
        group_buy_url: str,
        store_id: str,
        group_buy_code: str,
        session_name: str | None,
        menu: NormalizedMenu,
        delivery_summary: StoreDeliverySummary | None,
        owner_id: str | None = None,
        provider: str = "nidin",
    ) -> CartSessionSummary:
        default_name = session_name.strip() if session_name and session_name.strip() else "未命名團購"
        validate_menu_snapshot(menu)
        menu_json = menu.model_dump_json()
        now = _now_iso()

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM sessions WHERE store_id=? AND group_buy_code=?",
                (store_id, group_buy_code),
            ).fetchone()

            if existing is not None:
                if owner_id is not None and existing["owner_id"] != owner_id:
                    raise PermissionError("這個團購由其他主揪管理。")
                update_name = session_name.strip() if session_name and session_name.strip() else existing["session_name"]
                store_name = menu.store_name or existing["store_name"]
                delivery_minimum = delivery_summary.delivery_minimum if delivery_summary else existing["delivery_minimum"]
                delivery_description = delivery_summary.delivery_description if delivery_summary else existing["delivery_description"]
                order_status_reason = delivery_summary.order_status_reason if delivery_summary else existing["order_status_reason"]
                conn.execute(
                    """UPDATE sessions SET
                        group_buy_url=?, session_name=?, store_name=?,
                        delivery_minimum=?, delivery_description=?, order_status_reason=?,
                        menu_json=?, menu_fetched_at=?, updated_at=?
                    WHERE session_id=?""",
                    (
                        group_buy_url, update_name, store_name,
                        delivery_minimum, delivery_description, order_status_reason,
                        menu_json, now, now, existing["session_id"],
                    ),
                )
                row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (existing["session_id"],)).fetchone()
                return self._row_to_summary(row)

            session_id = token_urlsafe(6)
            store_name = menu.store_name or (delivery_summary.store_name if delivery_summary else "")
            conn.execute(
                """INSERT INTO sessions
                    (session_id, group_buy_url, store_id, group_buy_code, session_name,
                     store_name, delivery_minimum, delivery_description, order_status_reason,
                     menu_json, menu_fetched_at, created_at, updated_at, owner_id, provider)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    session_id, group_buy_url, store_id, group_buy_code, default_name,
                    store_name,
                    delivery_summary.delivery_minimum if delivery_summary else None,
                    delivery_summary.delivery_description if delivery_summary else "",
                    delivery_summary.order_status_reason if delivery_summary else "",
                    menu_json, now, now, now, owner_id, provider,
                ),
            )
            row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            return self._row_to_summary(row)

    def get_session(self, session_id: str) -> CartSessionDetail:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown cart session: {session_id}")
            lines = self._fetch_lines(conn, session_id)
        return self._row_to_detail(row, lines)

    def get_menu(self, session_id: str) -> NormalizedMenu:
        with self._connect() as conn:
            row = conn.execute("SELECT menu_json, menu_fetched_at FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown cart session: {session_id}")
        menu = NormalizedMenu.model_validate_json(row["menu_json"])
        validate_menu_snapshot(menu)
        return CartSessionMenu(
            **menu.model_dump(), menu_source="cache",
            menu_fetched_at=row["menu_fetched_at"],
        )

    def replace_menu(self, session_id: str, menu: NormalizedMenu) -> NormalizedMenu:
        validate_menu_snapshot(menu)
        now = _now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT session_id FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown cart session: {session_id}")
            conn.execute(
                "UPDATE sessions SET menu_json=?, store_name=?, menu_fetched_at=?, updated_at=? WHERE session_id=?",
                (menu.model_dump_json(), menu.store_name, now, now, session_id),
            )
        return CartSessionMenu(
            **menu.model_dump(exclude={"menu_source", "menu_fetched_at"}),
            menu_source="live", menu_fetched_at=now,
        )

    def add_lines(
        self,
        session_id: str,
        *,
        contributor_name: str,
        lines: list[dict[str, object]],
    ) -> CartSessionDetail:
        """只寫本站、不送 Nidin；僅供主揪手動補登與既有測試使用。"""
        built = self.build_order_lines(session_id, contributor_name=contributor_name, lines=lines)
        return self.record_order_lines(session_id, built)

    def build_order_lines(
        self,
        session_id: str,
        *,
        contributor_name: str,
        lines: list[dict[str, object]],
        menu: NormalizedMenu | None = None,
    ) -> list[tuple[CartSessionLine, "HookContext"]]:
        """跑完點單規則（必選規格、固定選項自動帶入、數量上限）組出品項，但不寫入。

        送進 Nidin 之前必須先跑：規則自動補上的選項也要送到 Nidin，兩邊金額才一致。
        """
        from app.order_hooks import HookContext, default_order_pipeline

        if menu is None:
            with self._connect() as conn:
                session_row = conn.execute(
                    "SELECT menu_json FROM sessions WHERE session_id=?", (session_id,)
                ).fetchone()
            if session_row is None:
                raise KeyError(f"Unknown cart session: {session_id}")
            menu = NormalizedMenu.model_validate_json(session_row["menu_json"])
        item_map = {item.id: item for item in menu.items}
        group_map = {group.id: group for group in menu.groups}

        built_lines: list[tuple[CartSessionLine, HookContext]] = []
        for line in lines:
            item_id = str(line["item_id"])
            quantity = int(line.get("quantity", 1))
            selection_option_ids = [str(oid) for oid in line.get("selection_option_ids", [])]
            item = item_map.get(item_id)
            if item is None:
                raise ValueError("找不到這個品項")

            hook_ctx = HookContext(
                item=item,
                quantity=quantity,
                selection_option_ids=selection_option_ids,
                contributor_name=contributor_name,
                group_map=group_map,
                menu=menu,
            )
            hook_res = default_order_pipeline.run_pre_validation(hook_ctx)
            if not hook_res.is_valid:
                raise ValueError(hook_res.message)

            final_option_ids = hook_res.modified_option_ids if hook_res.modified_option_ids is not None else selection_option_ids
            final_quantity = hook_res.modified_quantity if hook_res.modified_quantity is not None else quantity
            built_lines.append((
                self._build_line(
                    contributor_name=contributor_name,
                    item=item,
                    quantity=final_quantity,
                    selection_option_ids=final_option_ids,
                    group_map=group_map,
                ),
                hook_ctx,
            ))
        return built_lines

    def record_order_lines(
        self,
        session_id: str,
        built_lines: list[tuple[CartSessionLine, "HookContext"]],
        *,
        official_order_id: str | None = None,
    ) -> CartSessionDetail:
        """寫入已組好的品項。official_order_id 標記它對應哪張 Nidin 跟團單，同步時才會被取代而不重複。"""
        from app.order_hooks import default_order_pipeline

        with self._connect() as conn:
            session_row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if session_row is None:
                raise KeyError(f"Unknown cart session: {session_id}")

            # 不刪 payments：又點了一杯時應付變多，get_payments 會自動判定為未收清
            for built, hook_ctx in built_lines:
                conn.execute(
                    """INSERT INTO lines
                        (line_id, session_id, contributor_name, item_id, item_name,
                         category_name, quantity, selections_json, line_total, created_at,
                         official_order_id)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        built.line_id, session_id, built.contributor_name,
                        built.item_id, built.item_name, built.category_name,
                        built.quantity,
                        json.dumps([s.model_dump() for s in built.selections], ensure_ascii=False),
                        built.line_total, built.created_at,
                        official_order_id,
                    ),
                )
                default_order_pipeline.run_post_hooks(hook_ctx, built)

            now = _now_iso()
            conn.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now, session_id))
            session_row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            all_lines = self._fetch_lines(conn, session_id)

        return self._row_to_detail(session_row, all_lines)

    def clear_lines(self, session_id: str) -> CartSessionDetail:
        with self._connect() as conn:
            session_row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if session_row is None:
                raise KeyError(f"Unknown cart session: {session_id}")
            conn.execute("DELETE FROM lines WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM payments WHERE session_id=?", (session_id,))
            now = _now_iso()
            conn.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now, session_id))
            session_row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        return self._row_to_detail(session_row, [])

    def clear_submitted_lines(
        self, session_id: str, line_ids: list[str],
    ) -> CartSessionDetail:
        with self._connect() as conn:
            session_row = conn.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,),
            ).fetchone()
            if session_row is None:
                raise KeyError(f"Unknown cart session: {session_id}")
            # Other people can add lines while the upstream submission is pending.
            conn.executemany(
                "DELETE FROM lines WHERE session_id=? AND line_id=?",
                [(session_id, line_id) for line_id in line_ids],
            )
            conn.execute(
                "DELETE FROM payments WHERE session_id=? AND NOT EXISTS "
                "(SELECT 1 FROM lines WHERE lines.session_id=payments.session_id "
                "AND lines.contributor_name=payments.contributor_name)",
                (session_id,),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE session_id=?",
                (_now_iso(), session_id),
            )
            session_row = conn.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,),
            ).fetchone()
            remaining_lines = self._fetch_lines(conn, session_id)
        return self._row_to_detail(session_row, remaining_lines)

    def _fetch_lines(self, conn: sqlite3.Connection, session_id: str) -> list[CartSessionLine]:
        rows = conn.execute(
            "SELECT * FROM lines WHERE session_id=? ORDER BY created_at", (session_id,)
        ).fetchall()
        return [self._line_row_to_model(r) for r in rows]

    def _line_row_to_model(self, row: sqlite3.Row) -> CartSessionLine:
        selections = [CartLineSelection(**s) for s in json.loads(row["selections_json"])]
        return CartSessionLine(
            line_id=row["line_id"],
            contributor_name=row["contributor_name"],
            item_id=row["item_id"],
            item_name=row["item_name"],
            category_name=row["category_name"],
            quantity=row["quantity"],
            selections=selections,
            line_total=row["line_total"],
            created_at=row["created_at"],
            official_order_id=row["official_order_id"],
        )

    def _build_line(
        self,
        *,
        contributor_name: str,
        item: MenuItem | None,
        quantity: int,
        selection_option_ids: list[str],
        group_map: dict[str, MenuGroup],
    ) -> CartSessionLine:
        if item is None:
            raise ValueError("找不到這個品項")
        if quantity < 1:
            raise ValueError("數量至少要 1 杯")

        selections: list[CartLineSelection] = []
        counts_by_group: dict[str, int] = {gid: 0 for gid in item.group_ids}
        for option_id in selection_option_ids:
            match: tuple[MenuGroup, MenuOption] | None = None
            for group_id in item.group_ids:
                group = group_map.get(group_id)
                if group is None:
                    continue
                option = next((candidate for candidate in group.options if candidate.id == option_id), None)
                if option is not None:
                    match = (group, option)
                    break
            if match is None:
                raise ValueError("選項不存在")
            group, option = match
            counts_by_group[group.id] = counts_by_group.get(group.id, 0) + 1
            if counts_by_group[group.id] > group.max_select:
                raise ValueError(f"{group.name} 超過可選上限")
            selections.append(
                CartLineSelection(
                    group_id=group.id,
                    group_name=group.name,
                    option_id=option.id,
                    option_name=option.name,
                    price=option.price,
                )
            )

        for group_id in item.group_ids:
            group = group_map.get(group_id)
            if group is None:
                continue
            if counts_by_group.get(group.id, 0) < group.min_select:
                raise ValueError(f"{group.name} 尚未選完")

        return CartSessionLine(
            line_id=token_urlsafe(6),
            contributor_name=contributor_name.strip() or "未命名",
            item_id=item.id,
            item_name=item.name,
            category_name=item.category_name,
            quantity=quantity,
            selections=selections,
            line_total=_line_total(item.price, selections, quantity),
            created_at=_now_iso(),
        )

    def _row_to_summary(self, row: sqlite3.Row) -> CartSessionSummary:
        with self._connect() as conn:
            stats = conn.execute(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(line_total),0) as total FROM lines WHERE session_id=?",
                (row["session_id"],),
            ).fetchone()
        return CartSessionSummary(
            session_id=row["session_id"],
            provider=row["provider"],
            group_buy_url=row["group_buy_url"],
            store_id=row["store_id"],
            group_buy_code=row["group_buy_code"],
            session_name=row["session_name"],
            store_name=row["store_name"],
            line_count=stats["cnt"],
            total_money=stats["total"],
            delivery_minimum=row["delivery_minimum"],
            delivery_description=row["delivery_description"],
            order_status_reason=row["order_status_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_detail(self, row: sqlite3.Row, lines: list[CartSessionLine]) -> CartSessionDetail:
        return CartSessionDetail(
            session_id=row["session_id"],
            provider=row["provider"],
            group_buy_url=row["group_buy_url"],
            store_id=row["store_id"],
            group_buy_code=row["group_buy_code"],
            session_name=row["session_name"],
            store_name=row["store_name"],
            line_count=len(lines),
            total_money=sum(l.line_total for l in lines),
            delivery_minimum=row["delivery_minimum"],
            delivery_description=row["delivery_description"],
            order_status_reason=row["order_status_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            lines=lines,
        )

    def get_leader_auth(self) -> tuple[str, str] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT token, user_id FROM leader_auth WHERE id=1").fetchone()
            if row is None:
                return None
            return str(row["token"]), str(row["user_id"])

    def set_leader_auth(self, token: str, user_id: str = "") -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO leader_auth (id, token, user_id, updated_at)
                VALUES (1, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET token=excluded.token, user_id=excluded.user_id, updated_at=excluded.updated_at""",
                (token.strip(), user_id.strip(), now),
            )

    def clear_leader_auth(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM leader_auth WHERE id=1")

    def merge_official_orders_to_lines(
        self,
        session_id: str,
        official_summary: OfficialGroupBuySummary,
    ) -> CartSessionDetail:
        """Merge items from official Nidin group buy into this session's lines."""
        with self._connect() as conn:
            session_row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if session_row is None:
                raise KeyError(f"Unknown cart session: {session_id}")

            now = _now_iso()
            # 同步 = 以 Nidin 目前的狀態為準：先清掉上次同步進來的，手動加的品項（無 official_order_id）不動
            conn.execute(
                "DELETE FROM lines WHERE session_id=? AND official_order_id IS NOT NULL",
                (session_id,),
            )
            # 不動 payments：同步只是把 Nidin 目前的內容照抄回來，不代表誰又多欠了錢。
            # 以前每次同步都清掉收款標記，改成頻繁同步後主揪勾的「已收款」會一直消失
            menu = NormalizedMenu.model_validate_json(session_row["menu_json"])
            for order in official_summary.follower_orders:
                follower_name = order.follower_name or "官方跟團者"
                for index, item in enumerate(order.items):
                    # 固定的 line_id：每 20 秒同步一次也不會讓畫面上的每一杯都換身分
                    line_id = "line_" + hashlib.sha256(
                        f"{session_id}:{order.order_id}:{index}".encode()
                    ).hexdigest()[:16]
                    selections = _resolve_official_selections(
                        menu, str(item.product_id), item.options, item.option_groups
                    )
                    conn.execute(
                        """INSERT INTO lines
                            (line_id, session_id, contributor_name, item_id, item_name,
                             category_name, quantity, selections_json, line_total, created_at,
                             official_order_id)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            line_id,
                            session_id,
                            follower_name,
                            str(item.product_id),
                            item.name,
                            None,
                            item.amount,
                            json.dumps(selections, ensure_ascii=False),
                            item.subtotal,
                            order.created_at or now,
                            str(order.order_id),
                        ),
                    )

            conn.execute("UPDATE sessions SET updated_at=? WHERE session_id=?", (now, session_id))
            session_row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            lines = self._fetch_lines(conn, session_id)
        return self._row_to_detail(session_row, lines)
