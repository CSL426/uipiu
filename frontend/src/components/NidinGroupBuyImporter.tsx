import { Download, LoaderCircle, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { formatMoney } from "../lib/menu";
import type { CartSessionDetail, NidinGroupBuy, NidinGroupBuyImport } from "../types";

interface NidinGroupBuyImporterProps {
  onImported: (session: CartSessionDetail, syncError: string | null) => void;
  onOpen: (sessionId: string) => void;
}

/** 列出主揪在 Nidin 當團長的團（還在揪的＋最近送出的），一鍵匯入本站並同步。 */
export function NidinGroupBuyImporter({ onImported, onOpen }: NidinGroupBuyImporterProps) {
  const [groupBuys, setGroupBuys] = useState<NidinGroupBuy[] | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/nidin/group-buys");
      const body = (await res.json().catch(() => ({}))) as NidinGroupBuy[] | { detail?: unknown };
      if (!res.ok || !Array.isArray(body)) {
        const detail = Array.isArray(body) ? undefined : body.detail;
        setError(typeof detail === "string" ? detail : "讀不到 Nidin 團購。");
        return;
      }
      setGroupBuys(body);
    } catch {
      setError("連不上本站伺服器。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function importOne(groupBuy: NidinGroupBuy) {
    setImporting(groupBuy.order_id);
    setError("");
    try {
      const res = await fetch(`/api/nidin/group-buys/${groupBuy.order_id}/import`, { method: "POST" });
      const body = (await res.json().catch(() => ({}))) as NidinGroupBuyImport & { detail?: unknown };
      if (!res.ok) {
        setError(typeof body.detail === "string" ? body.detail : "匯入失敗。");
        return;
      }
      setGroupBuys((current) =>
        (current ?? []).map((g) => (g.order_id === groupBuy.order_id ? { ...g, session_id: body.session.session_id } : g)),
      );
      onImported(body.session, body.sync_error);
    } catch {
      setError("連不上本站伺服器。");
    } finally {
      setImporting(null);
    }
  }

  return (
    <div className="nidin-import">
      <div className="nidin-import-header">
        <strong>我在 Nidin 開的團</strong>
        <button type="button" className="ghost-button ghost-button-text" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={14} className={loading ? "spin" : undefined} />
          重新整理
        </button>
      </div>
      {error ? <p role="alert" className="nidin-import-error">{error}</p> : null}
      {groupBuys === null && loading ? <p className="nidin-import-muted">讀取中…</p> : null}
      {groupBuys?.length === 0 ? <p className="nidin-import-muted">最近沒有你當團長的團。</p> : null}
      <ul className="nidin-import-list">
        {(groupBuys ?? []).map((g) => (
          <li key={g.order_id} className="nidin-import-row">
            <div className="nidin-import-info">
              <span className="nidin-import-title">{g.title}</span>
              <span className="nidin-import-muted">
                {g.created_at.slice(5, 16)} · {g.status_text}
                {g.submitted ? ` · ${g.follower_count} 人 · ${formatMoney(g.total_money)}` : ""}
              </span>
            </div>
            {g.session_id ? (
              <button type="button" className="btn-secondary" onClick={() => onOpen(g.session_id as string)}>
                查看
              </button>
            ) : (
              <button
                type="button"
                className="btn-secondary"
                disabled={importing !== null}
                onClick={() => void importOne(g)}
              >
                {importing === g.order_id ? <LoaderCircle size={14} className="spin" /> : <Download size={14} />}
                匯入
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
