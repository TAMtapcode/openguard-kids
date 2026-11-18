import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { normalizeServer, serverUrl, setServerUrl, setToken } from "../api.js";

export default function Connect() {
  const [q] = useSearchParams();
  const nav = useNavigate();
  const [addr, setAddr] = useState(q.get("server") || serverUrl() || "");
  const [msg, setMsg] = useState({ text: "", ok: false });
  let url = "";
  try { url = normalizeServer(addr); } catch { url = ""; }
  const test = async e => {
    e.preventDefault();
    if (!url) return setMsg({ text: "Địa chỉ không hợp lệ. Ví dụ: 192.168.1.10", ok: false });
    setMsg({ text: "Đang kiểm tra…", ok: true });
    try {
      const r = await fetch(url + "/healthz", { credentials: "omit" });
      if (!r.ok) throw new Error();
      if (url !== serverUrl()) setToken(null);
      setServerUrl(url);
      window.location.assign("/login");
    } catch {
      setMsg({ text: "Chưa kết nối được. Hãy làm bước 2 (chấp nhận chứng chỉ), kiểm tra hai máy cùng Wi-Fi rồi thử lại.", ok: false });
    }
  };
  return (
    <div className="auth"><form className="auth-card" onSubmit={test}>
      <span className="brand big"><span className="brand-mark" aria-hidden="true" />Open Guardian Kids</span>
      <h1>Kết nối máy của bố mẹ</h1>
      <p className="muted">Dữ liệu nằm trên máy tính của bố mẹ trong nhà. Trang này chỉ là giao diện, không lưu dữ liệu của con.</p>
      <ol className="steps">
        <li><label>Địa chỉ hiện ở trang cuối của bộ cài “Máy của bố mẹ”
          <input value={addr} onChange={e => setAddr(e.target.value)} placeholder="192.168.1.10" required autoFocus /></label></li>
        <li>Lần đầu: <a href={url ? url + "/healthz" : undefined} target="_blank" rel="noreferrer">mở địa chỉ này</a>, chọn “Nâng cao” → “Tiếp tục”
          để trình duyệt tin chứng chỉ của máy bố mẹ, rồi quay lại đây. Nếu trình duyệt hỏi quyền truy cập mạng cục bộ, chọn Cho phép.</li>
        <li><button className="btn primary">Kết nối</button></li>
      </ol>
      {msg.text && <p className={msg.ok ? "ok" : "error"} role="status">{msg.text}</p>}
      {serverUrl() && <p><button type="button" className="btn ghost" onClick={() => nav("/")}>Quay lại</button></p>}
    </form></div>
  );
}
