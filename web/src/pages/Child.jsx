import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api.js";
import { AuditTable, dt, Loading, Meters, UsageChart, useToast } from "../ui.jsx";
import { ChildCard, useStatus } from "./Dashboard.jsx";
import PolicyEditor from "./Policy.jsx";

const TABS = [["live", "Trực tiếp"], ["report", "Báo cáo"], ["inbox", "Yêu cầu"], ["policy", "Luật & lịch"], ["devices", "Thiết bị"], ["audit", "Nhật ký"], ["data", "Dữ liệu"]];

function Inbox({ items, reload }) {
  const toast = useToast();
  const decide = async (r, approve) => {
    try { await api("POST", `/api/parent/requests/${r.id}/decide`, { approve, minutes: approve ? r.minutes || null : null }); toast("Đã phản hồi con."); reload(); }
    catch (e) { toast(e.message, true); }
  };
  return (
    <section className="panel"><h2>Hộp thư yêu cầu của con</h2>
      {!items.length && <p className="muted">Con chưa gửi yêu cầu nào.</p>}
      {items.map(r => (
        <div className={"req " + r.status} key={r.id}>
          <div><b>{r.kind === "add-time" ? `Xin thêm ${r.minutes} phút` : `Báo chặn nhầm: ${r.subject}`}</b>
            <span className="muted"> · {dt(r.created_at)}</span>{r.message && <p>“{r.message}”</p>}</div>
          {r.status === "pending" ? (
            <div className="row">
              <button className="btn primary" type="button" onClick={() => decide(r, true)}>Đồng ý</button>
              <button className="btn ghost" type="button" onClick={() => decide(r, false)}>Từ chối</button>
            </div>
          ) : <span className={"chip " + r.status}>{r.status === "approved" ? "Đã đồng ý" : "Đã từ chối"}</span>}
        </div>
      ))}
    </section>
  );
}

function Devices({ o, reload }) {
  const toast = useToast();
  const [consent, setConsent] = useState(false);
  const [code, setCode] = useState("");
  const [viewer, setViewer] = useState({ username: "", password: "" });
  const cid = o.child.id;
  const revoke = async d => {
    if (!window.confirm("Gỡ thiết bị này khỏi quản lý? Agent sẽ ngừng áp dụng luật.")) return;
    try { await api("POST", `/api/parent/devices/${d.id}/revoke`); reload(); } catch (e) { toast(e.message, true); }
  };
  const makeCode = async () => {
    try { const r = await api("POST", `/api/parent/children/${cid}/enroll-code?consent=${consent}`); setCode(r.install_code || r.code); toast("Mã có hiệu lực 10 phút."); }
    catch (e) { toast(e.message, true); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(code); toast("Đã sao chép mã. Dán vào trình cài đặt trên máy của con."); }
    catch { toast("Hãy bôi đen mã và nhấn Ctrl+C.", true); }
  };
  const addViewer = async e => {
    e.preventDefault();
    try { await api("POST", `/api/parent/children/${cid}/viewer`, viewer); toast("Đã tạo tài khoản xem cho con."); setViewer({ username: "", password: "" }); }
    catch (x) { toast(x.message, true); }
  };
  const dl = `${o.lan_url}/download/OGK-Setup.exe`;
  return (
    <section className="panel"><h2>Thiết bị của con</h2>
      {!o.devices.length && <p className="muted">Chưa có thiết bị nào.</p>}
      {o.devices.map(d => (
        <div className="row between" key={d.id}>
          <span>{d.name} <span className="muted">· ghép đôi {dt(d.enrolled_at)} · {d.status === "active" ? "đang quản lý" : "đã gỡ"}</span></span>
          {d.status === "active" && <button className="btn ghost" type="button" onClick={() => revoke(d)}>Gỡ thiết bị</button>}
        </div>
      ))}
      <h3>Thêm máy tính của con</h3>
      <ol className="steps">
        <li><b>Tải bộ cài</b> về máy của con: <a href={dl}>{dl}</a> <span className="muted">(hoặc chép <code>OGK-Setup.exe</code> bằng USB).
          Trình duyệt có thể cảnh báo chứng chỉ: chọn “Nâng cao” rồi “Tiếp tục”.</span></li>
        <li><b>Tạo mã cài đặt</b> ở đây:
          <label className="check"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} /> Tôi là cha/mẹ hoặc người giám hộ hợp pháp
            của {o.child.name}, đã giải thích cho con và đồng ý cho ứng dụng ghi nhận thời lượng sử dụng như mô tả ở <Link to="/privacy" target="_blank">trang Quyền riêng tư</Link>.</label>
          <div className="row wrap"><button className="btn primary" type="button" onClick={makeCode}>Tạo mã cài đặt</button>
            {code && <><code className="code">{code}</code><button className="btn ghost" type="button" onClick={copy}>Sao chép</button></>}</div></li>
        <li><b>Mở OGK-Setup.exe</b> trên máy của con, chọn “Máy của con”, dán mã vào rồi bấm Cài đặt. Mã dùng một lần, hiệu lực 10 phút.</li>
      </ol>
      <h3>Tài khoản để con xem báo cáo</h3>
      <form className="row wrap" onSubmit={addViewer}>
        <label>Tên đăng nhập<input value={viewer.username} onChange={e => setViewer({ ...viewer, username: e.target.value })} required minLength={3} /></label>
        <label>Mật khẩu (≥8 ký tự)<input type="password" value={viewer.password} onChange={e => setViewer({ ...viewer, password: e.target.value })}
          required minLength={8} autoComplete="new-password" /></label>
        <button className="btn ghost">Tạo tài khoản xem</button>
      </form>
    </section>
  );
}


function DigitalGarden({ report }) {
  if (!report) return null;
  const eduTime = report.apps?.filter(a => a.cat === 'education').reduce((s, a) => s + a.sec, 0) || 0;
  const gameTime = report.apps?.filter(a => ['game', 'entertainment'].includes(a.cat)).reduce((s, a) => s + a.sec, 0) || 0;
  const totalTime = report.usage?.[0]?.seconds || 0;

  let icon = "🌱";
  let message = "Cây đang lớn lên cùng bé.";
  let colorClass = "bg-green-50 border-green-200 text-green-800";

  if (totalTime > 3 * 3600) {
    icon = "🥀";
    message = "Cây đang mệt úa. Bé dùng máy hơi lâu, nhắc bé nghỉ mắt nhé!";
    colorClass = "bg-yellow-50 border-yellow-200 text-yellow-800";
  } else if (eduTime > gameTime && eduTime > 300) {
    icon = "🌳";
    message = "Cây rất tươi tốt vì hôm nay bé học tập chăm chỉ!";
    colorClass = "bg-emerald-50 border-emerald-200 text-emerald-900";
  }

  return (
    <div className={`p-6 rounded-xl border flex items-center gap-6 mb-6 ${colorClass}`}>
      <div className="text-6xl drop-shadow-sm">{icon}</div>
      <div>
        <h3 className="text-lg font-bold mb-1">Khu vườn số</h3>
        <p className="text-sm font-medium">{message}</p>
        <div className="text-sm mt-2 opacity-80 flex gap-4">
          <span>📚 Học tập: {Math.round(eduTime / 60)} phút</span>
          <span>🎮 Giải trí: {Math.round(gameTime / 60)} phút</span>
        </div>
      </div>
    </div>
  );
}

export default function Child() {
  const { id } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const [o, setO] = useState(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState(() => window.location.hash.slice(1) || "live");
  const [kids, loadStatus] = useStatus();
  const load = useCallback(() => api("GET", `/api/parent/children/${id}/overview`).then(setO).catch(e => setErr(e.message)), [id]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { window.history.replaceState(null, "", `#${tab}`); }, [tab]);
  if (err) return <p className="error">{err}</p>;
  if (!o) return <Loading />;
  const live = kids && kids.find(c => c.id === o.child.id);
  const pending = o.requests.filter(r => r.status === "pending").length;
  const wipe = async () => {
    const typed = window.prompt(`Việc này xóa toàn bộ dữ liệu hoạt động của ${o.child.name} trên máy chủ và máy con, không thể khôi phục.\nGõ tên con để xác nhận:`);
    if (!typed || typed.trim() !== o.child.name) return;
    try { await api("DELETE", `/api/parent/children/${o.child.id}/data`); toast("Đã xóa dữ liệu."); load(); } catch (e) { toast(e.message, true); }
  };
  return (
    <>
      <header className="page-head">
        <a className="back" href="/" onClick={e => { e.preventDefault(); nav("/"); }}>← Tất cả các con</a>
        <h1>{o.child.name} <small className="muted">{o.child.age} tuổi</small></h1>
        <div className="tabs" role="tablist">
          {TABS.map(([k, v]) => (
            <button key={k} role="tab" type="button" aria-selected={tab === k} onClick={() => setTab(k)}>{v}{k === "inbox" && pending > 0 && ` (${pending})`}</button>
          ))}
        </div>
      </header>
      {tab === "live" && (live ? <ChildCard c={live} onDone={loadStatus} /> : <p className="muted">Đang tải…</p>)}
      {tab === "report" && (
        <section className="panel"><h2>7 ngày qua</h2><UsageChart points={o.report.usage} />
          <div className="grid2">
            <div><h3>10 ứng dụng dùng nhiều nhất</h3><Meters items={o.report.apps} /></div>
            <div><h3>10 tên miền truy cập nhiều nhất</h3><Meters items={o.report.domains} /></div>
          </div>
          <p className="muted">Lần bị chặn trong 7 ngày: ứng dụng <b>{o.report.blocked.blocked_app}</b>, trang web <b>{o.report.blocked.blocked_domain}</b>.
            Chỉ lưu tên ứng dụng và tên miền cấp hai, không lưu URL đầy đủ hay tiêu đề cửa sổ. Thời lượng tên miền là ước lượng.</p>
          <h3>Sự kiện gần đây</h3>
          {o.recent.length ? o.recent.map((e, i) => <p className="ev" key={i}><span className="muted">{dt(e.ts)}</span> · {e.text}</p>)
            : <p className="muted">Chưa có cảnh báo, khóa máy hay thay đổi giờ nào.</p>}
        </section>
      )}
      {tab === "inbox" && <Inbox items={o.requests} reload={load} />}
      {tab === "policy" && <PolicyEditor cid={o.child.id} policy={o.policy} version={o.version} categories={o.categories} onSaved={load} />}
      {tab === "devices" && <Devices o={o} reload={load} />}
      {tab === "audit" && <section className="panel"><h2>Nhật ký thay đổi <small className="muted">con cũng xem được</small></h2><AuditTable rows={o.audit} withIp /></section>}
      {tab === "data" && (
        <section className="panel danger-zone"><h2>Dữ liệu của con</h2>
          <p>Dữ liệu hoạt động tự động xóa sau 90 ngày. Bạn có thể xóa ngay toàn bộ dữ liệu trên máy chủ và trên bộ nhớ đệm của máy con.</p>
          <button className="btn danger" type="button" onClick={wipe}>Xóa toàn bộ dữ liệu của con tôi</button></section>
      )}
    </>
  );
}
