import { useCallback, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { dur, usePoll, useToast } from "../ui.jsx";

function LockControl({ child, dev, onDone }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [minutes, setMinutes] = useState(60);
  const [busy, setBusy] = useState(false);
  // Trạng thái trên bảng điều khiển chỉ cập nhật khi máy con báo về; sau khi gửi lệnh ta tải lại nhiều lần để thấy kết quả sớm.
  const refreshSoon = () => [800, 2500, 5000].forEach(ms => setTimeout(onDone, ms));
  const send = async (action, extra = {}) => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await api("POST", `/api/parent/children/${child.id}/command`, { action, device_id: dev.id, ...extra });
      const base = action === "LOCK_NOW" ? `Đã khóa, tự mở sau ${extra.minutes} phút.` : action === "ADD_TIME" ? "Đã cộng 15 phút." : "Đã mở khóa.";
      toast(r.delivered_now ? base : `${base} Máy đang ngoại tuyến, lệnh sẽ áp dụng ngay khi máy kết nối lại.`);
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
        {/* Luôn hiện cả hai nút: trạng thái "đang khóa" có thể chưa kịp cập nhật, không để bố mẹ bị kẹt không bấm được Mở khóa */}
        <button className={"btn " + (locked ? "primary" : "ghost")} type="button" disabled={busy} onClick={() => send("UNLOCK")}>Mở khóa</button>
        <button className="btn danger" type="button" disabled={busy} onClick={() => setOpen(!open)}>Khóa máy…</button>
        <button className="btn primary" type="button" disabled={busy} onClick={() => send("ADD_TIME", { minutes: 15 })}>Cộng 15 phút</button>
      </div>
      {open && (
        <div className="lockbox">
          <p>Máy của con hiện màn hình khóa. Con vẫn bấm được <b>Đăng xuất</b> hoặc <b>Tắt máy</b> và nhắn xin thêm giờ.
            Máy <b>tự mở</b> khi hết thời gian khóa, hoặc khi bạn bấm Mở khóa.</p>
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
  const label = !d.online ? "Ngoại tuyến" : d.state === "locked" ? "Đang khóa" : d.state === "grace" ? "Sắp hết giờ" : "Đang bật";
  return (
    <div className="dev">
      <div className="dev-top">
        <span><span className={"dot " + (d.state === "locked" ? "lock" : d.online ? "on" : "")} /><b>{d.name}</b> <span className="muted">{label}</span></span>
        <span className="muted">Đã dùng {dur(used)}{d.online && ` · còn ${dur(rem)}`}</span>
      </div>
      <div className={"timebar " + cls}><i style={{ width: `${pct}%` }} /></div>
      {!d.ui_ok && <p className="chip warn">Biểu tượng khay không hiển thị — agent đã tạm dừng chặn.</p>}
      {!d.policy_ok && d.online && <p className="muted">Đang áp dụng luật mới…</p>}
      <LockControl child={child} dev={d} onDone={onDone} />
    </div>
  );
}

export function ChildCard({ c, link, onDone }) {
  return (
    <article className="kid-card">
      <div className="kid-head">
        <h2>{c.name}</h2><span className="muted">{c.age} tuổi</span>
        {c.pending_requests > 0 && <span className="chip warn">{c.pending_requests} yêu cầu mới</span>}
        {link && <Link to={`/child/${c.id}`}>Luật, báo cáo và yêu cầu</Link>}
      </div>
      {!c.devices.length && <p className="muted">Chưa ghép đôi máy nào. Mở “Luật, báo cáo và yêu cầu” → Thiết bị để tạo mã cài đặt.</p>}
      {c.devices.map(d => <Device key={d.id} child={c} d={d} onDone={onDone} />)}
    </article>
  );
}

export function useStatus() {
  const [kids, setKids] = useState(null);
  const seq = useRef(0);
  const load = useCallback(() => {
    const n = ++seq.current;   // chỉ nhận phản hồi mới nhất, phản hồi cũ đến muộn không được ghi đè trạng thái mới
    return api("GET", "/api/parent/status").then(r => { if (n === seq.current) setKids(r.children); }).catch(() => {});
  }, []);
  usePoll(load, 3000);
  return [kids, load];
}

export default function Dashboard() {
  const toast = useToast();
  const [kids, load] = useStatus();
  const [f, setF] = useState({ name: "", age: 9 });
  const add = async e => {
    e.preventDefault();
    try { await api("POST", "/api/parent/children", { name: f.name, age: Number(f.age) }); toast("Đã thêm con."); setF({ name: "", age: 9 }); load(); }
    catch (x) { toast(x.message, true); }
  };
  return (
    <>
      <header className="page-head"><h1>Hôm nay con đang thế nào?</h1>
        <p className="muted">Cập nhật mỗi 3 giây. Lệnh khóa và cộng giờ đến máy của con trong vài giây.</p></header>
      <section className="children" aria-live="polite">
        {kids === null ? <p className="muted">Đang tải…</p> : kids.length ? kids.map(c => <ChildCard key={c.id} c={c} link onDone={load} />)
          : <p className="muted">Chưa có hồ sơ nào. Hãy thêm con ở bên dưới.</p>}
      </section>
      <section className="panel"><h2>Bắt đầu chỉ với 3 bước</h2>
        <ol className="steps">
          <li><b>Thêm hồ sơ của con</b> ở khung bên dưới.</li>
          <li>Mở hồ sơ, mục <b>Thiết bị</b> và bấm <b>Tạo mã cài đặt</b>.</li>
          <li>Trên máy của con, chạy <code>OGK-Setup.exe</code>, chọn “Máy của con” và nhập mã. Xong!</li>
        </ol></section>
      <section className="panel"><h2>Thêm hồ sơ cho con</h2>
        <form className="row wrap" onSubmit={add}>
          <label>Tên con<input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} required maxLength={100} /></label>
          <label>Tuổi<input type="number" min="3" max="17" value={f.age} onChange={e => setF({ ...f, age: e.target.value })} required /></label>
          <button className="btn primary">Thêm con</button>
        </form>
        <p className="muted">Trẻ 6–11 tuổi được áp dụng chính sách mặc định: chặn mạng xã hội và nội dung không phù hợp, trò chơi tối đa 45 phút/ngày, giáo dục không giới hạn.</p>
      </section>
    </>
  );
}
