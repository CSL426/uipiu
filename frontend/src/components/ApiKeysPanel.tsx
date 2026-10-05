import { Check, Copy, KeyRound, LoaderCircle, Plus, RefreshCw, X } from "lucide-react";
import { useEffect, useState } from "react";

import { copyText } from "../lib/clipboard";
import type { ApiKeyInfo, ApiKeyScope, CreatedApiKey } from "../types";
import { CustomSelect } from "./CustomSelect";

const SCOPE_LABEL: Record<ApiKeyScope, string> = { order: "點單", leader: "主揪" };

function formatTime(seconds: number | null | undefined): string {
  if (!seconds) return "還沒用過";
  const date = new Date(seconds * 1000);
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

async function detailOf(res: Response, fallback: string): Promise<string> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  return typeof body.detail === "string" ? body.detail : fallback;
}

/** 發給外部 agent 的 API key：agent 帶著它連 /mcp 查團、點單；主揪 key 還能開團、刪單、標記收款。 */
export function ApiKeysPanel() {
  const [keys, setKeys] = useState<ApiKeyInfo[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [name, setName] = useState("");
  const [scope, setScope] = useState<ApiKeyScope>("order");
  const [creating, setCreating] = useState(false);
  // 新 key 只在建立當下拿得到；關掉後就再也看不到
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  const [copied, setCopied] = useState(false);
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [revokingId, setRevokingId] = useState<string | null>(null);
  const mcpUrl = `${window.location.origin}/mcp`;

  async function load() {
    setLoading(true);
    setError("");
    try {
      const res = await fetch("/api/api-keys");
      if (!res.ok) {
        setError(await detailOf(res, "讀不到 API key。"));
        return;
      }
      const body: unknown = await res.json().catch(() => null);
      // 回應不是清單時別讓整個管理頁跟著壞掉
      if (!Array.isArray(body)) {
        setError("讀不到 API key。");
        return;
      }
      setKeys(body as ApiKeyInfo[]);
    } catch {
      setError("連不上本站伺服器。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function create() {
    if (!name.trim()) {
      setError("先幫這把 key 取個名字，例如用的 agent 名稱。");
      return;
    }
    setCreating(true);
    setError("");
    try {
      const res = await fetch("/api/api-keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), scope }),
      });
      if (!res.ok) {
        setError(await detailOf(res, "建立失敗。"));
        return;
      }
      const key = (await res.json()) as CreatedApiKey;
      setCreated(key);
      setCopied(false);
      setName("");
      const { key: _secret, ...info } = key;
      setKeys((current) => [info, ...(current ?? [])]);
    } catch {
      setError("連不上本站伺服器。");
    } finally {
      setCreating(false);
    }
  }

  async function revoke(keyId: string) {
    setRevokingId(keyId);
    setError("");
    try {
      const res = await fetch(`/api/api-keys/${keyId}`, { method: "DELETE" });
      if (!res.ok) {
        setError(await detailOf(res, "撤銷失敗。"));
        return;
      }
      setKeys((current) => (current ?? []).map((k) => (k.id === keyId ? { ...k, revoked: true } : k)));
      if (created?.id === keyId) setCreated(null);
    } catch {
      setError("連不上本站伺服器。");
    } finally {
      setRevokingId(null);
      setConfirmingId(null);
    }
  }

  async function copyCreated() {
    if (!created) return;
    setCopied(await copyText(created.key));
  }

  return (
    <div className="nidin-import agent-keys">
      <div className="nidin-import-header">
        <strong>外部 agent 的 API key</strong>
        <button type="button" className="ghost-button ghost-button-text" onClick={() => void load()} disabled={loading}>
          <RefreshCw size={14} className={loading ? "spin" : undefined} />
          重新整理
        </button>
      </div>
      <p className="nidin-import-muted">
        agent 用 MCP 連 <code>{mcpUrl}</code>，帶 <code>Authorization: Bearer &lt;key&gt;</code>。點單 key 能查團、點單、撤回自己的單；主揪 key 還能開團、刪任何一筆、標記收款。
      </p>

      <div className="agent-keys-form">
        <label className="field">
          <span>名稱</span>
          <input value={name} maxLength={40} placeholder="例如：Claude 點餐助理" onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>權限</span>
          <CustomSelect
            ariaLabel="權限"
            value={scope}
            onChange={(value) => setScope(value as ApiKeyScope)}
            options={[
              { value: "order", label: "點單" },
              { value: "leader", label: "主揪" },
            ]}
          />
        </label>
        <button type="button" className="btn-secondary" onClick={() => void create()} disabled={creating}>
          {creating ? <LoaderCircle size={14} className="spin" /> : <Plus size={14} />}
          發一把新的
        </button>
      </div>

      {error ? <p role="alert" className="nidin-import-error">{error}</p> : null}

      {created ? (
        <div className="agent-keys-reveal" role="status">
          <p className="nidin-import-muted">這把 key 只會顯示這一次，請現在存好。</p>
          <code className="agent-keys-secret">{created.key}</code>
          <div className="inline-actions">
            <button type="button" className="btn-secondary" onClick={() => void copyCreated()}>
              {copied ? <Check size={14} /> : <Copy size={14} />}
              {copied ? "已複製" : "複製"}
            </button>
            <button type="button" className="ghost-button ghost-button-text" onClick={() => setCreated(null)}>
              我存好了
            </button>
          </div>
        </div>
      ) : null}

      {keys === null && loading ? <p className="nidin-import-muted">讀取中…</p> : null}
      {keys?.length === 0 ? <p className="nidin-import-muted">還沒有發過 key。</p> : null}
      <ul className="nidin-import-list">
        {(keys ?? []).map((k) => (
          <li key={k.id} className={k.revoked ? "nidin-import-row agent-keys-revoked" : "nidin-import-row"}>
            <div className="nidin-import-info">
              <span className="nidin-import-title">
                <KeyRound size={13} /> {k.name}
              </span>
              <span className="nidin-import-muted">
                {SCOPE_LABEL[k.scope]} · …{k.hint} · {k.revoked ? "已撤銷" : `最後使用 ${formatTime(k.last_used_at)}`}
              </span>
            </div>
            {k.revoked ? null : (
              <button
                type="button"
                className="btn-secondary"
                disabled={revokingId !== null}
                onClick={() => (confirmingId === k.id ? void revoke(k.id) : setConfirmingId(k.id))}
              >
                {revokingId === k.id ? <LoaderCircle size={14} className="spin" /> : <X size={14} />}
                {confirmingId === k.id ? "確定撤銷？" : "撤銷"}
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
