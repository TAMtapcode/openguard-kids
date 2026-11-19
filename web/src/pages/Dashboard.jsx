import { useCallback, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { dt, dur, LiveBadge, usePoll, useRealtime, useToast } from "../ui.jsx";

function LockControl({ child, dev, onDone }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [minutes, setMinutes] = useState(60);
  const [busy, setBusy] = useState(false);
  const refreshSoon = () => [700, 2200, 4500].forEach(ms => setTimeout(onDone, ms));
  const send = async (action, extra = {}) => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await api("POST", `/api/parent/children/${child.id}/command`, { action, device_id: dev.id, ...extra });
      const base = action === "LOCK_NOW" ? `Đã khóa, tự mở sau ${extra.minutes} phút.` : action === "ADD_TIME" ? "Đã cộng 15 phút." : "Đã mở khóa.";
      toast(r.delivered_now ? base : `${base} Máy đang ngoại tuyến, lệnh sẽ áp dụng ngay khi kết nối lại.`);
      setOpen(false);
      onDone();
      refreshSoon();
    } catch (e) { toast(e.message, true); }
    finally { setBusy(false); }
  };
  const locked = dev.state === "locked";
  return (
    <>
      <div className="actions">
        <button className={"btn " + (locked ? "primary" : "ghost")} type="button" disabled={busy} onClick={() => send("UNLOCK")}>🔓 Mở khóa</button>
        <button className="btn soft-danger" type="button" disabled={busy} onClick={() => setOpen(!open)}>🔒 Khóa máy…</button>
        <button className="btn primary" type="button" disabled={busy} onClick={() => send("ADD_TIME", { minutes: 15 })}>⏱️ +15 phút</button>
      </div>
      {open && (
        <div className="lockbox">
          <p>Máy của con hiện màn hình phủ tạm khóa (chống đổi Desktop). Con vẫn nhắn xin thêm giờ được và máy <b>tự mở</b> khi hết thời gian hoặc khi bạn bấm Mở khóa.</p>
          <div className="row wrap">
            <label>Khóa trong
              <select value={minutes} onChange={e => setMinutes(Number(e.target.value))}>
                {[15, 30, 60, 120, 240].map(m => <option key={m} value={m}>{m < 60 ? `${m} phút` : `${m / 60} giờ`}</option>)}
              </select></label>
            <button className="btn danger" type="button" disabled={busy} onClick={() => send("LOCK_NOW", { minutes })}>Khóa ngay</button>
            <button className="btn ghost" type="button" onClick={() => setOpen(false)}>Thôi</button>
          </div>
        </div>
      )}
    </>
  );
}

function Device({ child, d, onDone }) {
  const used = d.used_today_sec, rem = d.remaining_sec, total = used + Math.max(rem, 0);
  const pct = total > 0 ? Math.min(100, Math.round((100 * used) / total)) : 100;
  const cls = d.state === "locked" || rem <= 0 ? "out" : rem < 600 ? "low" : "";
  const badgeCls = !d.online ? "" : d.state === "locked" ? "lock" : d.state === "grace" ? "warn" : "on";
  const label = !d.online ? "Ngoại tuyến" : d.state === "locked" ? "Đang khóa" : d.state === "grace" ? "Sắp hết giờ" : "Đang bật";
  return (
    <div className="dev">
      <div className="dev-top">
        <div className="row">
          <b>{d.name}</b>
          <span className={"status-badge " + badgeCls}><span className={"dot " + badgeCls} />{label}</span>
        </div>
        {!d.policy_ok && d.online && <span className="muted" style={{ fontSize: ".82rem" }}>Đang cập nhật luật…</span>}
      </div>
      <div className="time-meta">
        <span>Đã dùng hôm nay: <b>{dur(used)}</b></span>
        <span>{d.online ? <>Còn lại: <strong>{dur(Math.max(0, rem))}</strong></> : "Cập nhật khi máy trực tuyến"}</span>
      </div>
      <div className={"timebar " + cls}><i style={{ width: `${pct}%` }} /></div>
      {!d.ui_ok && <p className="chip warn">Biểu tượng khay không hiển thị — agent đã tạm dừng chặn.</p>}
      <LockControl child={child} dev={d} onDone={onDone} />
    </div>
  );
}

export function ChildCard({ c, link, onDone }) {
  const initial = (c.name || "?").trim().charAt(0).toUpperCase();
  return (
    <article className="kid-card">
      <div className="kid-head">
        <div className="kid-title">
          <div className="kid-avatar" aria-hidden="true">{initial}</div>
          <div>
            <h2>{c.name} <small className="muted">· {c.age} tuổi</small></h2>
          </div>
          {c.pending_requests > 0 && <span className="chip warn">🔔 {c.pending_requests} yêu cầu chờ duyệt</span>}
        </div>
        {link && <Link className="kid-link" to={`/child/${c.id}`}>Chi tiết & cài đặt →</Link>}
      </div>
      {!c.devices.length && <p className="muted" style={{ marginTop: 12 }}>Chưa ghép đôi máy nào. Mở <b>Chi tiết & cài đặt → Thiết bị</b> để tạo mã cài đặt.</p>}
      {c.devices.map(d => <Device key={d.id} child={c} d={d} onDone={onDone} />)}
    </article>
  );
}

export function useStatus(onRealtimeEvent) {
  const [kids, setKids] = useState(null);
  const seq = useRef(0);
  const toast = useToast();
  const load = useCallback(() => {
    const n = ++seq.current;
    return api("GET", "/api/parent/status").then(r => { if (n === seq.current) setKids(r.children); }).catch(() => {});
  }, []);
  const live = useRealtime(ev => {
    if (ev.type === "request_created") {
      const who = ev.child_name || "Con";
      toast(ev.kind === "add-time"
        ? `🔔 ${who} vừa xin thêm ${ev.minutes || 15} phút!`
        : `🔔 ${who} vừa báo chặn nhầm: ${ev.subject || ""}`);
    }
    load();
    onRealtimeEvent && onRealtimeEvent(ev);
  });
  usePoll(load, live ? 8000 : 4000);
  return [kids, load, live];
}

function PendingBanner({ kids, onDone }) {
  const toast = useToast();
  const [busyId, setBusyId] = useState(null);
  const all = (kids || []).flatMap(c => (c.pending_items || []).map(r => ({ ...r, childName: c.name, childId: c.id })));
  if (!all.length) return null;
  const decide = async (r, approve) => {
    setBusyId(r.id);
    try {
      await api("POST", `/api/parent/requests/${r.id}/decide`, { approve, minutes: approve ? r.minutes || null : null });
      toast(approve ? `Đã đồng ý yêu cầu của ${r.childName}.` : `Đã từ chối yêu cầu của ${r.childName}.`);
      onDone();
    } catch (e) { toast(e.message, true); }
    finally { setBusyId(null); }
  };
  return (
    <section className="panel highlight-inbox" aria-live="polite">
      <div className="panel-head">
        <h2>🔔 Yêu cầu đang chờ bố mẹ ({all.length})</h2>
        <span className="muted" style={{ fontSize: ".86rem" }}>Tự cập nhật ngay khi con bấm gửi</span>
      </div>
      {all.map(r => (
        <div className="req pending" key={r.id}>
          <div>
            <b>{r.childName}</b> · {r.kind === "add-time" ? `Xin thêm ${r.minutes} phút` : `Báo chặn nhầm: ${r.subject}`}
            <span className="muted"> · {dt(r.created_at)}</span>
            {r.message && <p>“{r.message}”</p>}
          </div>
          <div className="row">
            <button className="btn primary sm" type="button" disabled={busyId === r.id} onClick={() => decide(r, true)}>✓ Đồng ý</button>
            <button className="btn ghost sm" type="button" disabled={busyId === r.id} onClick={() => decide(r, false)}>Từ chối</button>
          </div>
        </div>
      ))}
    </section>
  );
}

export default function Dashboard() {
  const toast = useToast();
  const [kids, load, live] = useStatus();
  const [f, setF] = useState({ name: "", age: 9 });
  const add = async e => {
    e.preventDefault();
    try { await api("POST", "/api/parent/children", { name: f.name, age: Number(f.age) }); toast("Đã thêm hồ sơ cho con."); setF({ name: "", age: 9 }); load(); }
    catch (x) { toast(x.message, true); }
  };
  return (
    <>
      <header className="page-head">
        <div className="head-row">
          <div>
            <h1>Hôm nay con đang thế nào?</h1>
            <p className="lede">Mọi yêu cầu xin thêm giờ và trạng thái máy đều tự động cập nhật, không cần tải lại trang.</p>
          </div>
          <LiveBadge live={live} />
        </div>
      </header>
      <PendingBanner kids={kids} onDone={load} />
      <section className="children" aria-live="polite">
        {kids === null ? <p className="muted">Đang tải…</p> : kids.length ? kids.map(c => <ChildCard key={c.id} c={c} link onDone={load} />)
          : <div className="panel"><p className="muted" style={{ margin: 0 }}>Chưa có hồ sơ nào. Hãy thêm con ở khung bên dưới để bắt đầu nhé.</p></div>}
      </section>
      <section className="panel">
        <h2>Thêm hồ sơ cho con</h2>
        <form className="row wrap" onSubmit={add}>
          <label>Tên con<input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="Ví dụ: Bin" required maxLength={100} /></label>
          <label>Tuổi<input type="number" min="3" max="17" value={f.age} onChange={e => setF({ ...f, age: e.target.value })} required style={{ width: 92 }} /></label>
          <button className="btn primary">+ Thêm con</button>
        </form>
        <p className="muted" style={{ marginBottom: 0 }}>Trẻ 6–11 tuổi mặc định chặn mạng xã hội và nội dung không phù hợp, trò chơi tối đa 45 phút/ngày, giáo dục không giới hạn.</p>
      </section>
      {(!kids || kids.length === 0) && (
        <section className="panel"><h2>Bắt đầu chỉ với 3 bước</h2>
          <ol className="steps">
            <li><b>Thêm hồ sơ của con</b> ở khung phía trên.</li>
            <li>Mở hồ sơ → mục <b>Thiết bị</b> và bấm <b>Tạo mã cài đặt</b>.</li>
            <li>Trên máy của con, chạy <code>OGK-Setup.exe</code>, chọn “Máy của con” và dán mã. Xong!</li>
          </ol></section>
      )}
    </>
  );
}
