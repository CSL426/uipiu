import { LoaderCircle, Plus, Search, Star } from "lucide-react";
import { useState } from "react";

import type { CartSessionDetail, NidinGroupBuyImport, NidinStoreSearchResult, StoreSort } from "../types";
import { CustomSelect } from "./CustomSelect";

interface NidinStoreSearchProps {
  onOpened: (session: CartSessionDetail) => void;
}

async function detailOf(res: Response, fallback: string): Promise<string> {
  const body = (await res.json().catch(() => ({}))) as { detail?: unknown };
  return typeof body.detail === "string" ? body.detail : fallback;
}

/** 輸入品牌，找離主揪最近或評分相對高的 Nidin 分店，直接開新團並加進本站。 */
export function NidinStoreSearch({ onOpened }: NidinStoreSearchProps) {
  const [brand, setBrand] = useState("");
  const [sort, setSort] = useState<StoreSort>("distance");
  const [result, setResult] = useState<NidinStoreSearchResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  // 開團會真的在主揪的 Nidin 帳號建一個團：第一次按只進入確認
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [openingId, setOpeningId] = useState<string | null>(null);

  async function search(nextSort: StoreSort = sort) {
    if (!brand.trim()) {
      setError("先輸入品牌，例如：青山、得正。");
      return;
    }
    setSearching(true);
    setError("");
    setConfirmingId(null);
    try {
      const query = new URLSearchParams({ brand: brand.trim(), sort: nextSort });
      const res = await fetch(`/api/nidin/stores?${query}`);
      if (!res.ok) {
        setResult(null);
        setError(await detailOf(res, "找不到店家。"));
        return;
      }
      setResult((await res.json()) as NidinStoreSearchResult);
    } catch {
      setError("連不上本站伺服器。");
    } finally {
      setSearching(false);
    }
  }

  async function open(storeId: string) {
    if (confirmingId !== storeId) {
      setConfirmingId(storeId);
      return;
    }
    setOpeningId(storeId);
    setError("");
    try {
      const res = await fetch(`/api/nidin/stores/${storeId}/group-buy`, { method: "POST" });
      if (!res.ok) {
        setError(await detailOf(res, "開團失敗。"));
        return;
      }
      onOpened(((await res.json()) as NidinGroupBuyImport).session);
      setConfirmingId(null);
    } catch {
      setError("連不上本站伺服器，請到 Nidin 確認團有沒有開成功再重試。");
    } finally {
      setOpeningId(null);
    }
  }

  return (
    <div className="nidin-import">
      <div className="nidin-import-header">
        <strong>在 Nidin 開新團</strong>
      </div>
      <div className="agent-keys-form">
        <label className="field">
          <span>品牌</span>
          <input
            value={brand}
            maxLength={40}
            placeholder="例如：青山"
            onChange={(e) => setBrand(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") void search();
            }}
          />
        </label>
        <label className="field">
          <span>排序</span>
          <CustomSelect
            ariaLabel="排序"
            value={sort}
            onChange={(value) => {
              setSort(value as StoreSort);
              if (result) void search(value as StoreSort);
            }}
            options={[
              { value: "distance", label: "離我最近" },
              { value: "rating", label: "評分相對高" },
            ]}
          />
        </label>
        <button type="button" className="btn-secondary" onClick={() => void search()} disabled={searching}>
          {searching ? <LoaderCircle size={14} className="spin" /> : <Search size={14} />}
          找店
        </button>
      </div>

      {error ? <p role="alert" className="nidin-import-error">{error}</p> : null}
      {result ? (
        <p className="nidin-import-muted">
          {result.origin ? `距離從「${result.origin}」算起` : "讀不到你在 Nidin 的地址，無法算距離"}
          {sort === "rating" ? "；評價少的店會往 4 星拉，只看 8 公里內" : ""}
        </p>
      ) : null}
      {result?.stores.length === 0 ? <p className="nidin-import-muted">附近沒有這個品牌的店。</p> : null}
      <ul className="nidin-import-list">
        {(result?.stores ?? []).map((store) => (
          <li key={store.store_id} className={store.orderable ? "nidin-import-row" : "nidin-import-row agent-keys-revoked"}>
            <div className="nidin-import-info">
              <span className="nidin-import-title">{store.name}</span>
              <span className="nidin-import-muted">
                {store.distance_km !== null ? `${store.distance_km} km · ` : ""}
                <Star size={11} /> {store.rating_count > 0 ? `${store.rating}（${store.rating_count} 則）` : "還沒有評價"}
                {store.orderable ? "" : " · 目前不接單"}
              </span>
            </div>
            <button
              type="button"
              className="btn-secondary"
              disabled={!store.orderable || openingId !== null}
              onClick={() => void open(store.store_id)}
            >
              {openingId === store.store_id ? <LoaderCircle size={14} className="spin" /> : <Plus size={14} />}
              {confirmingId === store.store_id ? "確定開團？" : "開團"}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
