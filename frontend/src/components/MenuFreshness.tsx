import type { NormalizedMenu } from "../types";

export function MenuFreshness({ menu }: { menu: NormalizedMenu | null }) {
  if (menu?.menu_source !== "cache") return null;

  const fetchedAt = menu.menu_fetched_at;
  const formattedTime = fetchedAt
    ? new Date(fetchedAt).toLocaleString("zh-TW", {
        timeZone: "Asia/Taipei",
        hour12: false,
      })
    : null;

  return (
    <p role="status" className="menu-cache-notice">
      使用上次菜單・{formattedTime ? (
        <>最後更新：<time dateTime={fetchedAt ?? undefined}>{formattedTime}</time>（臺北時間）</>
      ) : "更新時間未記錄"}
      。目前可整理點餐清單，送單前會重新核對菜單。
    </p>
  );
}
