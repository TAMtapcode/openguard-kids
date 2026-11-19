import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { wsEventsUrl } from "./api.js";

const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }) {
  const [t, setT] = useState({ msg: "", err: false, show: false });
  const h = useRef();
  const show = useCallback((msg, err = false) => {
    setT({ msg, err, show: true });
    clearTimeout(h.current);
    h.current = setTimeout(() => setT(x => ({ ...x, show: false })), 4200);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      <div id="toast" role="status" aria-live="polite" className={(t.show ? "show" : "") + (t.err ? " err" : "")}>{t.msg}</div>
    </ToastCtx.Provider>
  );
}

export const mins = s => Math.max(0, Math.round(s / 60));
export function dur(s) {
  const m = mins(s), h = Math.floor(m / 60);
  return h ? `${h} giờ ${String(m % 60).padStart(2, "0")} phút` : `${m} phút`;
}
export const dt = ts => (ts ? new Date(ts * 1000).toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" }) : "—");

export function usePoll(fn, ms, deps = []) {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    let alive = true;
    const run = () => { if (alive && document.visibilityState !== "hidden") fnRef.current(); };
    fnRef.current();
    const id = setInterval(run, ms);
    const onFocus = () => { if (alive) fnRef.current(); };
    const onVis = () => { if (alive && document.visibilityState === "visible") fnRef.current(); };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive = false;
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, ...deps]);
}

export function useRealtime(onEvent, enabled = true) {
  const [live, setLive] = useState(false);
  const cbRef = useRef(onEvent);
  cbRef.current = onEvent;

  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    let ws = null;
    let retryTimer = null;
    let pingTimer = null;
    let delay = 1500;

    const connect = () => {
      if (!alive) return;
      try {
        ws = new WebSocket(wsEventsUrl());
      } catch {
        setLive(false);
        retryTimer = setTimeout(connect, delay);
        delay = Math.min(delay * 1.5, 10000);
        return;
      }
      ws.onopen = () => {
        if (!alive) return;
        setLive(true);
        delay = 1500;
        clearInterval(pingTimer);
        pingTimer = setInterval(() => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            try { ws.send(JSON.stringify({ ping: Date.now() })); } catch { /* ignore */ }
          }
        }, 25000);
      };
      ws.onmessage = e => {
        if (!alive) return;
        try {
          const ev = JSON.parse(e.data);
          if (ev && ev.type && ev.type !== "pong" && ev.type !== "connected") {
            cbRef.current && cbRef.current(ev);
          }
        } catch { /* ignore */ }
      };
      ws.onclose = () => {
        if (!alive) return;
        setLive(false);
        clearInterval(pingTimer);
        retryTimer = setTimeout(connect, delay);
        delay = Math.min(delay * 1.5, 10000);
      };
      ws.onerror = () => {
        try { ws && ws.close(); } catch { /* ignore */ }
      };
    };

    connect();
    const onOnline = () => {
      if (alive && (!ws || ws.readyState !== WebSocket.OPEN)) {
        clearTimeout(retryTimer);
        connect();
      }
      cbRef.current && cbRef.current({ type: "reconnect" });
    };
    window.addEventListener("online", onOnline);
    return () => {
      alive = false;
      clearTimeout(retryTimer);
      clearInterval(pingTimer);
      window.removeEventListener("online", onOnline);
      try { ws && ws.close(); } catch { /* ignore */ }
    };
  }, [enabled]);

  return live;
}

export function LiveBadge({ live }) {
  return (
    <span className={"live-pill " + (live ? "on" : "off")} title={live ? "Đang đồng bộ trực tiếp, không cần bấm F5" : "Đang kết nối lại đồng bộ trực tiếp…"}>
      <i aria-hidden="true" />{live ? "Đồng bộ trực tiếp" : "Đang nối lại…"}
    </span>
  );
}

export function Loading({ text = "Đang tải…" }) {
  return <div className="center"><div className="spinner" aria-hidden="true" /><p className="muted">{text}</p></div>;
}

export function UsageChart({ points }) {
  const w = 560, H = 180, pb = 30, pt = 24, mx = Math.max(1800, ...points.map(p => p.seconds)), slot = w / points.length;
  return (
    <svg className="chart" viewBox={`0 0 ${w} ${H}`} role="img" aria-label="Thời lượng sử dụng máy theo ngày">
      <line className="grid-line" x1="8" y1={H - pb} x2={w - 8} y2={H - pb} />
      <line className="grid-line" x1="8" y1={pt + (H - pb - pt) / 2} x2={w - 8} y2={pt + (H - pb - pt) / 2} strokeDasharray="3 4" />
      {points.map((p, i) => {
        const h = ((H - pb - pt) * p.seconds) / mx, x = i * slot + slot * 0.22, y = H - pb - h;
        return (
          <g key={p.day}>
            <rect className={i === points.length - 1 ? "bar-today" : "bar"} x={x} y={y} width={slot * 0.56} height={Math.max(h, 3)} rx="6" />
            <text className="bar-val" x={x + slot * 0.28} y={y - 7} textAnchor="middle">{p.seconds ? `${mins(p.seconds)}′` : ""}</text>
            <text className="bar-lbl" x={x + slot * 0.28} y={H - 9} textAnchor="middle">{p.label}</text>
          </g>
        );
      })}
    </svg>
  );
}

export function Meters({ items }) {
  if (!items.length) return <p className="muted">Chưa có dữ liệu.</p>;
  const mx = Math.max(...items.map(a => a.seconds), 1);
  return items.map(a => (
    <div className="meter" key={a.subject}>
      <span title={a.subject}>{a.subject}</span>
      <div className="meter-track"><i style={{ width: `${Math.max(4, Math.round((100 * a.seconds) / mx))}%` }} /></div>
      <b>{dur(a.seconds)}</b>
    </div>
  ));
}

export function AuditTable({ rows, withIp }) {
  return (
    <div className="scroll"><table>
      <thead><tr><th>Lúc</th><th>Ai</th>{withIp && <th>IP</th>}<th>Việc</th><th>Trước</th><th>Sau</th></tr></thead>
      <tbody>
        {rows.length ? rows.map(a => (
          <tr key={a.id}><td>{dt(a.ts)}</td><td>{a.actor}</td>{withIp && <td>{a.ip}</td>}
            <td>{a.action}{a.field && ` · ${a.field}`}</td><td className="clip" title={a.old}>{a.old}</td><td className="clip" title={a.new}>{a.new}</td></tr>
        )) : <tr><td colSpan={withIp ? 6 : 5} className="muted">Chưa có thay đổi nào.</td></tr>}
      </tbody>
    </table></div>
  );
}
