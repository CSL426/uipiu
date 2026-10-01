import jsQR from "jsqr";
import QRCode from "qrcode";

export async function generateQrDataUrl(text: string): Promise<string> {
  return QRCode.toDataURL(text, {
    width: 256,
    margin: 1,
    color: {
      dark: "#1e293b",
      light: "#ffffff",
    },
  });
}

export function parseQrCredentials(text: string): { token: string; userId: string } | null {
  const trimmed = text.trim();
  if (!trimmed) return null;

  // 1. Try parsing JSON format
  try {
    const data = JSON.parse(trimmed) as Record<string, unknown>;
    const token =
      (data.token as string) ||
      (data.Nidin_token as string) ||
      (data.mc_api_token as string) ||
      (data.mcApiToken as string) ||
      "";
    const userId =
      (data.userId as string) ||
      (data.user_id as string) ||
      (data.Nidin_userId as string) ||
      (data.mc_api_user as string) ||
      (data.mcApiUser as string) ||
      "";

    if (token && typeof token === "string") {
      return { token: token.trim(), userId: String(userId).trim() };
    }
  } catch {
    // Not valid JSON, proceed to other patterns
  }

  // 2. Query string format (nidin_token=...&nidin_user_id=...)
  if (trimmed.includes("=") && (trimmed.includes("token") || trimmed.includes("Token"))) {
    try {
      const params = new URLSearchParams(trimmed.startsWith("?") ? trimmed.slice(1) : trimmed);
      const token =
        params.get("token") ||
        params.get("Nidin_token") ||
        params.get("mc_api_token") ||
        "";
      const userId =
        params.get("userId") ||
        params.get("user_id") ||
        params.get("Nidin_userId") ||
        params.get("mc_api_user") ||
        "";
      if (token) {
        return { token: token.trim(), userId: userId.trim() };
      }
    } catch {
      // ignore
    }
  }

  // 3. Raw JWT token (starts with eyJ)
  if (trimmed.startsWith("eyJ") && trimmed.length > 30) {
    return { token: trimmed, userId: "" };
  }

  return null;
}

export async function parseQrFromImageFile(file: File): Promise<string | null> {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = (e) => {
      const dataUrl = e.target?.result;
      if (typeof dataUrl !== "string") {
        resolve(null);
        return;
      }
      const img = new Image();
      img.onload = () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = img.width;
          canvas.height = img.height;
          const ctx = canvas.getContext("2d", { willReadFrequently: true });
          if (!ctx) {
            resolve(null);
            return;
          }
          ctx.drawImage(img, 0, 0);
          const imageData = ctx.getImageData(0, 0, img.width, img.height);
          const code = jsQR(imageData.data, imageData.width, imageData.height);
          resolve(code ? code.data : null);
        } catch {
          resolve(null);
        }
      };
      img.onerror = () => resolve(null);
      img.src = dataUrl;
    };
    reader.onerror = () => resolve(null);
    reader.readAsDataURL(file);
  });
}

export function getNidinBookmarkletCode(): string {
  const code = `javascript:(function(){
var t=localStorage.getItem('Nidin_token')||'';
var u=localStorage.getItem('Nidin_userId')||'';
if(!t){alert('未在當前頁面找到 Nidin_token，請先登入你訂 (order.nidin.shop) 後再點擊此書籤！');return;}
var payload=JSON.stringify({token:t,userId:u});
var el=document.createElement('div');
el.style.cssText='position:fixed;inset:0;background:rgba(15,23,42,0.7);z-index:999999;display:flex;align-items:center;justify-content:center;font-family:system-ui,sans-serif;padding:16px;';
var box=document.createElement('div');
box.style.cssText='background:#fff;border-radius:12px;padding:24px;max-width:340px;width:100%;text-align:center;box-shadow:0 20px 25px -5px rgba(0,0,0,0.1);color:#0f172a;';
box.innerHTML='<h3 style="margin:0 0 8px;font-size:18px;">🎉 已提取你訂團長憑證</h3><p style="font-size:13px;color:#64748b;margin:0 0 16px;">使用手機相機或點單後台掃描下方 QR 碼快速匯入：</p><div id="nidin-qr" style="margin:0 auto 16px;display:flex;justify-content:center;">載入中...</div><div style="display:flex;gap:8px;justify-content:center;"><button id="c-btn" style="background:#0284c7;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:14px;font-weight:500;">複製憑證</button><button id="x-btn" style="background:#e2e8f0;color:#334155;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:14px;">關閉</button></div>';
el.appendChild(box);document.body.appendChild(el);
box.querySelector('#x-btn').onclick=function(){el.remove();};
box.querySelector('#c-btn').onclick=function(){navigator.clipboard.writeText(payload).then(function(){alert('憑證已複製！');});};
var s=document.createElement('script');s.src='https://cdn.jsdelivr.net/npm/qrcode@1.5.4/build/qrcode.min.js';
s.onload=function(){var cv=document.createElement('canvas');window.QRCode.toCanvas(cv,payload,{width:220,margin:1},function(e){if(!e){var q=box.querySelector('#nidin-qr');q.innerHTML='';q.appendChild(cv);}});};
document.body.appendChild(s);})();`;
  return code.replace(/\n\s*/g, "");
}
