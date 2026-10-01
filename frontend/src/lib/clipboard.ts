/**
 * 複製文字到剪貼簿，回傳是否成功。
 *
 * navigator.clipboard 只存在於 secure context（HTTPS 或 localhost）。
 * 用 http://區網IP:6464 開站時它是 undefined，直接呼叫會丟 TypeError、按鈕看起來沒反應，
 * 所以退回已棄用但仍廣泛支援的 execCommand("copy")；兩者都失敗時由呼叫端讓使用者手動複製。
 */
export async function copyText(text: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 權限被拒時仍可試舊方法
    }
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    textarea.remove();
  }
}
