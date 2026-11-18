import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

const ToastCtx = createContext(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }) {
  const [t, setT] = useState({ msg: "", err: false, show: false });
  const h = useRef();
  const show = useCallback((msg, err = false) => {
    setT({ msg, err, show: true });
    clearTimeout(h.current);
    h.current = setTimeout(() => setT(x => ({ ...x, show: false })), 3500);
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
  useEffect(() => {
    let alive = true;
    const run = () => alive && fn();
    run();
    const id = setInterval(run, ms);
    return () => { alive = false; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

export function Loading({ text = "Đang tải…" }) {
  return <div className="center"><p className="muted">{text}</p></div>;
}

export function UsageChart({ points }) {
  const w = 560, H = 170, pb = 28, pt = 22, mx = Math.max(1800, ...points.map(p => p.seconds)), slot = w / points.length;
  return (
    <svg className="chart" viewBox={`0 0 ${w} ${H}`} role="img" aria-label="Thời lượng sử dụng máy theo ngày">
      {points.map((p, i) => {
        const h = ((H - pb - pt) * p.seconds) / mx, x = i * slot + slot * 0.2, y = H - pb - h;
        return (
          <g key={p.day}>
            <rect className={i === points.length - 1 ? "bar-today" : "bar"} x={x} y={y} width={slot * 0.6} height={Math.max(h, 1)} rx="4" />
            <text className="bar-val" x={x + slot * 0.3} y={y - 6} textAnchor="middle">{p.seconds ? `${mins(p.seconds)}′` : ""}</text>
            <text className="bar-lbl" x={x + slot * 0.3} y={H - 8} textAnchor="middle">{p.label}</text>
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
      <span title={a.subject}>{a.subject}</span><i style={{ width: `${Math.round((100 * a.seconds) / mx)}%` }} /><b>{dur(a.seconds)}</b>
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
