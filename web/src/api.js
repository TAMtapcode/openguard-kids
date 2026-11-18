// Gọi API máy chủ OGK. Hai chế độ:
// - Cùng máy chủ (FastAPI phục vụ giao diện): cookie HttpOnly + X-CSRF-Token.
// - Netlify (VITE_REMOTE=1): địa chỉ máy chủ do bố mẹ nhập, phiên bằng Bearer token, không gửi cookie khác nguồn.
export const isRemote = import.meta.env.VITE_REMOTE === "1";
const LS = window.localStorage;
let csrf = "";

export const serverUrl = () => (isRemote ? LS.getItem("ogk_server") || "" : "");
export const setServerUrl = u => LS.setItem("ogk_server", u.trim().replace(/\/+$/, ""));
export const setCsrf = c => { csrf = c || ""; };
const token = () => (isRemote ? LS.getItem("ogk_token") : null);
export const setToken = t => (t ? LS.setItem("ogk_token", t) : LS.removeItem("ogk_token"));

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export function normalizeServer(input) {
  let u = input.trim();
  if (!u) return "";
  if (!/^https?:\/\//i.test(u)) u = "https://" + u;
  const url = new URL(u);
  const explicitPort = /^https?:\/\/(\[[^\]]+\]|[^/:]+):\d+/i.test(u);   // URL bỏ cổng mặc định (443) khỏi .port
  if (!explicitPort && url.protocol === "https:") url.port = "8443";
  return url.origin;
}

export async function api(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const t = token();
  if (t) headers.Authorization = `Bearer ${t}`;
  else if (!isRemote && csrf && method !== "GET") headers["X-CSRF-Token"] = csrf;   // CSRF chỉ cho phiên cookie
  let r;
  try {
    r = await fetch(serverUrl() + path, { method, headers, credentials: isRemote ? "omit" : "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, isRemote
      ? "Không kết nối được máy của bố mẹ. Kiểm tra máy đang bật, cùng mạng Wi-Fi và bạn đã chấp nhận chứng chỉ."
      : "Mất kết nối tới máy chủ.");
  }
  let data = null;
  try { data = await r.json(); } catch { /* không phải JSON */ }
  if (!r.ok) {
    const d = data && data.detail;
    const msg = Array.isArray(d) ? d.map(x => x.msg).join("; ") : d || "Có lỗi xảy ra.";
    if (r.status === 401 && path !== "/api/auth/login") window.dispatchEvent(new Event("ogk:401"));
    throw new ApiError(r.status, msg);
  }
  return data;
}
