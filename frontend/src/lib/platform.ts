import type { CartSessionSummary } from "../types";

export type Platform = NonNullable<CartSessionSummary["provider"]>;

/** 畫面上稱呼這個團所在的平台；舊資料沒有 provider 的都是 Nidin */
export function platformLabel(provider?: Platform | null): string {
  return provider === "ocard" ? "Ocard" : "Nidin";
}

/** Ocard 跟團只要名字，沒有地方填電話 */
export function acceptsPhone(provider?: Platform | null): boolean {
  return provider !== "ocard";
}
