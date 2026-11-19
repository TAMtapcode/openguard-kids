import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useToast } from "../ui.jsx";

const DAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "CN"];
const lines = t => t.split("\n").map(s => s.trim()).filter(Boolean);
const appsToText = a => a.map(x => [x.name || "", x.sha256 || "", x.group || ""].join(" | ").replace(/( \| )+$/, "")).join("\n");
const textToApps = t => lines(t).map(l => {
  const [name, sha256, group] = l.split("|").map(s => s.trim());
  return { name: name || "", sha256: sha256 || "", group: group || undefined };
});
const fromPolicy = P => ({
  wd: P.quota.weekday_min, we: P.quota.weekend_min, sched: P.schedule.map(r => r.slice()),
  groups: Object.fromEntries(Object.entries(P.group_budgets_min || {}).map(([k, v]) => [k, String(v)])),
  cats: P.domains.blocked_categories, domBlocked: P.domains.blocked.join("\n"), domAllowed: P.domains.allowed.join("\n"),
  safesearch: P.domains.safesearch, doh: P.domains.block_doh, mode: P.apps.mode,
  appsBlocked: appsToText(P.apps.blocked), appsAllowed: appsToText(P.apps.allowed),
  ltp: P.learn_to_play.enabled, ratio: P.learn_to_play.earn_ratio, cap: P.learn_to_play.daily_cap_min, stale: P.failsafe.stale_after_days,
});

function Schedule({ sched, setSched }) {
  const paint = useRef(null);
  useEffect(() => {
    const up = () => (paint.current = null);
    window.addEventListener("pointerup", up);
    return () => window.removeEventListener("pointerup", up);
  }, []);
  const setCell = (r, c, v) => setSched(s => s.map((row, i) => (i === r ? row.map((x, j) => (j === c ? v : x)) : row)));
  const toggleDay = r => setSched(s => s.map((row, i) => (i === r ? row.map(() => (row.every(x => x) ? 0 : 1)) : row)));
  return (
    <div className="scroll"><div className="sched" role="grid" aria-label="Lịch tuần 7 ngày × 48 khung giờ">
      <span />
      {Array.from({ length: 8 }, (_, i) => <span key={i} className="hh">{i * 3}h</span>)}
      {sched.map((row, r) => [
        <span key={`d${r}`} className="dn" title="Bấm để đảo cả ngày" onClick={() => toggleDay(r)}>{DAYS[r]}</span>,
        ...row.map((v, c) => (
          <div key={`${r}-${c}`} className={"c" + (v ? " on" : "")} role="gridcell" aria-selected={!!v}
            aria-label={`${DAYS[r]} ${String(Math.floor(c / 2)).padStart(2, "0")}:${c % 2 ? "30" : "00"}`}
            onPointerDown={() => { paint.current = v ? 0 : 1; setCell(r, c, paint.current); }}
            onPointerEnter={e => { if (paint.current !== null && e.buttons === 1) setCell(r, c, paint.current); }} />
        )),
      ])}
    </div></div>
  );
}

export default function PolicyEditor({ cid, policy, version, categories, onSaved }) {
  const toast = useToast();
  const [s, setS] = useState(() => fromPolicy(policy));
  const [ver, setVer] = useState(version);
  useEffect(() => {
    setS(fromPolicy(policy));
    setVer(version);
  }, [cid, version]);
  const [hash, setHash] = useState("");
  const set = k => e => setS({ ...s, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const preset = p => setS({ ...s, sched: s.sched.map(() => Array.from({ length: 48 }, (_, i) => (p === "open" ? 1 : p === "closed" ? 0 : i >= 12 && i < 42 ? 1 : 0))) });
  const save = async () => {
    const gb = {};
    Object.entries(s.groups).forEach(([k, v]) => { if (v !== "" && v !== undefined) gb[k] = Number(v); });
    const body = {
      quota: { weekday_min: Number(s.wd), weekend_min: Number(s.we) }, schedule: s.sched, group_budgets_min: gb,
      domains: { blocked: lines(s.domBlocked), allowed: lines(s.domAllowed), safesearch: s.safesearch, block_doh: s.doh, blocked_categories: s.cats },
      apps: { mode: s.mode, blocked: textToApps(s.appsBlocked), allowed: textToApps(s.appsAllowed) },
      learn_to_play: { enabled: s.ltp, earn_ratio: Number(s.ratio), daily_cap_min: Number(s.cap) },
      failsafe: { stale_after_days: Number(s.stale) },
    };
    try {
      const r = await api("PUT", `/api/parent/children/${cid}/policy`, body);
      setVer(r.version);
      toast(`Đã lưu luật (phiên bản ${r.version}). Máy con áp dụng trong vòng 60 giây.`);
      onSaved && onSaved();
    } catch (e) { toast(e.message, true); }
  };
  const hashFile = async e => {
    const f = e.target.files[0];
    if (!f) return;
    if (!(window.crypto && crypto.subtle)) return setHash("Trình duyệt cần HTTPS để băm tệp. Hoặc chạy: python -m agent hash <tệp>");
    const h = await crypto.subtle.digest("SHA-256", await f.arrayBuffer());
    setHash([...new Uint8Array(h)].map(b => b.toString(16).padStart(2, "0")).join(""));
  };
  return (
    <section className="panel">
      <h2>Luật, lịch và danh sách chặn <small className="muted">phiên bản {ver}</small></h2>
      <h3>Hạn mức mỗi ngày</h3>
      <div className="row wrap">
        <label>Ngày thường (phút)<input type="number" min="0" max="1440" value={s.wd} onChange={set("wd")} /></label>
        <label>Cuối tuần (phút)<input type="number" min="0" max="1440" value={s.we} onChange={set("we")} /></label>
      </div>
      <h3>Lịch tuần <span className="muted">(mỗi ô 30 phút; xanh = được dùng máy; kéo chuột để tô)</span></h3>
      <div className="row wrap">
        <button className="btn ghost" type="button" onClick={() => preset("school")}>6h–21h mọi ngày</button>
        <button className="btn ghost" type="button" onClick={() => preset("open")}>Mở cả tuần</button>
        <button className="btn ghost" type="button" onClick={() => preset("closed")}>Khóa cả tuần</button>
      </div>
      <Schedule sched={s.sched} setSched={fn => setS(x => ({ ...x, sched: typeof fn === "function" ? fn(x.sched) : fn }))} />
      <h3>Ngân sách theo nhóm <span className="muted">(phút/ngày, để trống = không giới hạn)</span></h3>
      <div className="row wrap">
        {Object.entries(categories).map(([k, v]) => (
          <label key={k}>{v}<input type="number" min="0" max="1440" value={s.groups[k] ?? ""}
            onChange={e => setS({ ...s, groups: { ...s.groups, [k]: e.target.value } })} /></label>
        ))}
      </div>
      <h3>Nhóm tên miền bị chặn</h3>
      <div className="row wrap">
        {Object.entries(categories).map(([k, v]) => (
          <label key={k} className="check"><input type="checkbox" checked={s.cats.includes(k)}
            onChange={e => setS({ ...s, cats: e.target.checked ? [...s.cats, k] : s.cats.filter(x => x !== k) })} /> {v}</label>
        ))}
      </div>
      <div className="grid2">
        <label>Tên miền chặn thêm (mỗi dòng một tên, gồm cả tên miền con)<textarea rows="4" spellCheck="false" value={s.domBlocked} onChange={set("domBlocked")} /></label>
        <label>Tên miền luôn cho phép<textarea rows="4" spellCheck="false" value={s.domAllowed} onChange={set("domAllowed")} /></label>
      </div>
      <div className="row wrap">
        <label className="check"><input type="checkbox" checked={s.safesearch} onChange={set("safesearch")} /> Bật SafeSearch (Google, Bing, YouTube, DuckDuckGo)</label>
        <label className="check"><input type="checkbox" checked={s.doh} onChange={set("doh")} /> Chặn máy chủ DNS-over-HTTPS</label>
      </div>
      <h3>Ứng dụng</h3>
      <label>Chế độ<select value={s.mode} onChange={set("mode")}><option value="blocklist">Chặn theo danh sách</option><option value="allowlist">Chỉ cho phép danh sách</option></select></label>
      <div className="grid2">
        <label>Chặn: <code>tên.exe | sha256 | nhóm</code> (mỗi dòng một mục)<textarea rows="4" spellCheck="false" value={s.appsBlocked} onChange={set("appsBlocked")} /></label>
        <label>Cho phép (chế độ “chỉ cho phép”)<textarea rows="4" spellCheck="false" value={s.appsAllowed} onChange={set("appsAllowed")} /></label>
      </div>
      <div className="row wrap"><label>Lấy SHA-256 của một tệp .exe (tính ngay trên trình duyệt, không tải lên)<input type="file" onChange={hashFile} /></label>
        {hash && <code className="hash">{hash}</code>}</div>
      <h3>Đổi giờ học lấy giờ chơi</h3>
      <div className="row wrap">
        <label className="check"><input type="checkbox" checked={s.ltp} onChange={set("ltp")} /> Cho phép</label>
        <label>Phút thưởng cho mỗi phút học<input type="number" step="0.05" min="0" max="2" value={s.ratio} onChange={set("ratio")} /></label>
        <label>Tối đa mỗi ngày (phút)<input type="number" min="0" max="600" value={s.cap} onChange={set("cap")} /></label>
      </div>
      <h3>Khi máy mất kết nối lâu</h3>
      <label>Sau bao nhiêu ngày thì khóa các nhóm giải trí/game/mạng xã hội<input type="number" min="1" max="90" value={s.stale} onChange={set("stale")} /></label>
      <p className="muted">Nhóm giáo dục, cổng trường học và Tổng đài 111 luôn mở dù mất mạng bao lâu.</p>
      <button className="btn primary" type="button" onClick={save}>Lưu luật</button>
    </section>
  );
}