import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api, isRemote } from "../api.js";
import { dt, dur, LiveBadge, Loading, usePoll, useRealtime, useToast } from "../ui.jsx";

const LOCK_OPTIONS = [15, 30, 60, 120];

export function useStatus(onEvent) {
  const [kids, setKids] = useState(null);
  const load = useCallback(() => {
    api("GET", "/api/parent/status")
      .then(r => setKids(r.children || []))
      .catch(() => {});
  }, []);
  const live = useRealtime(ev => {
    load();
    if (onEvent) onEvent(ev);
  });
  usePoll(load, live ? 15000 : 6000, [load, live]);
  return [kids, load, live];
}

function DeviceRow({ childId, d, onDone }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [lockOpen, setLockOpen] = useState(false);
  const [lockMin, setLockMin] = useState(60);

  const used = Math.max(0, d.used_today_sec || 0);
  const rem = d.remaining_sec;
  const unlimited = rem == null || rem > 86400 * 30;
  const total = unlimited ? 0 : used + Math.max(0, rem);
  const pct = total > 0 ? Math.min(100, Math.round((100 * used) / total)) : 0;
  const barCls = !unlimited && rem <= 0 ? "out" : !unlimited && rem < 600 ? "low" : "";

  let badgeCls = "";
  let badgeText = "Ngoại tuyến";
  if (!d.online) {
    badgeCls = "";
    badgeText = "Ngoại tuyến";
  } else if (d.state === "locked") {
    badgeCls = "lock";
    badgeText = "Đang khóa";
  } else if (d.state === "suspended" || !d.ui_ok) {
    badgeCls = "warn";
    badgeText = "Biểu tượng khay tắt";
  } else if (d.state === "grace") {
    badgeCls = "warn";
    badgeText = "Sắp khóa (ân hạn)";
  } else {
    badgeCls = "on";
    badgeText = "Đang bật";
  }

  const sendCmd = async (action, minutes) => {
    setBusy(true);
    try {
      const payload = { action, device_id: d.id };
      if (minutes !== undefined) payload.minutes = minutes;
      const r = await api("POST", `/api/parent/children/${childId}/command`, payload);
      const delivered = r.delivered_now > 0;
      if (action === "LOCK_NOW") {
        toast(delivered ? `Đã khóa máy ${d.name} trong ${minutes} phút.` : "Đã gửi lệnh khóa (sẽ áp dụng ngay khi máy trực tuyến).");
        setLockOpen(false);
      } else if (action === "UNLOCK") {
        toast(delivered ? `Đã mở khóa máy ${d.name}.` : "Đã gửi lệnh mở khóa.");
      } else if (action === "ADD_TIME") {
        toast(delivered ? `Đã cộng thêm ${minutes} phút cho ${d.name}.` : `Đã lưu lệnh cộng +${minutes} phút.`);
      }
      if (onDone) onDone();
    } catch (e) {
      toast(e.message, true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dev">
      <div className="dev-top">
        <div>
          <b>{d.name}</b>{" "}
          <span className="muted">· cập nhật {dt(d.last_seen)}</span>
        </div>
        <div className="row wrap">
          {!d.policy_ok && d.online && <span className="chip warn">Đang đồng bộ luật…</span>}
          {!d.ui_ok && <span className="chip warn">Biểu tượng khay không hiển thị</span>}
          <span className={"status-badge " + badgeCls}>
            <i className={"dot " + badgeCls} aria-hidden="true" />
            {badgeText}
          </span>
        </div>
      </div>

      <div className="time-meta">
        <span>
          Đã dùng hôm nay: <strong>{dur(used)}</strong>
        </span>
        <span>
          {unlimited ? (
            <strong>Không giới hạn</strong>
          ) : rem > 0 ? (
            <>Còn lại: <strong>{dur(rem)}</strong></>
          ) : (
            <strong style={{ color: "var(--berry)" }}>Đã hết giờ hôm nay</strong>
          )}
        </span>
      </div>

      {!unlimited && (
        <div className={"timebar " + barCls} aria-hidden="true">
          <i style={{ width: `${Math.max(used > 0 ? 4 : 0, pct)}%` }} />
        </div>
      )}

      <div className="actions">
        <button className="btn primary sm" type="button" disabled={busy} onClick={() => sendCmd("ADD_TIME", 15)}>
          +15 phút
        </button>
        <button className="btn ghost sm" type="button" disabled={busy} onClick={() => sendCmd("ADD_TIME", 30)}>
          +30 phút
        </button>
        {d.state === "locked" ? (
          <button className="btn primary sm" type="button" disabled={busy} onClick={() => sendCmd("UNLOCK", 15)}>
            Mở khóa
          </button>
        ) : (
          <button className="btn soft-danger sm" type="button" disabled={busy} onClick={() => setLockOpen(v => !v)}>
            {lockOpen ? "Đóng bảng khóa" : "Khóa máy…"}
          </button>
        )}
      </div>

      {lockOpen && (
        <div className="lockbox">
          <p>
            <b>Khóa tạm thời máy {d.name}</b> — máy sẽ tự mở khi hết thời gian hoặc khi bạn bấm Mở khóa.
          </p>
          <div className="row wrap">
            <label>
              Thời gian khóa
              <select value={lockMin} onChange={e => setLockMin(Number(e.target.value))}>
                {LOCK_OPTIONS.map(m => (
                  <option key={m} value={m}>
                    {m >= 60 ? `${m / 60} giờ` : `${m} phút`}
                  </option>
                ))}
              </select>
            </label>
            <button className="btn danger sm" type="button" disabled={busy} onClick={() => sendCmd("LOCK_NOW", lockMin)}>
              Khóa ngay
            </button>
            <button className="btn ghost sm" type="button" disabled={busy} onClick={() => setLockOpen(false)}>
              Hủy
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function ChildCard({ c, onDone, showLink = false }) {
  const toast = useToast();
  const [busyReq, setBusyReq] = useState(null);
  const initial = (c.name || "?").trim().charAt(0).toUpperCase();

  const decide = async (r, approve) => {
    setBusyReq(r.id);
    try {
      await api("POST", `/api/parent/requests/${r.id}/decide`, {
        approve,
        minutes: approve ? r.minutes || null : null,
      });
      toast(approve ? "Đã đồng ý yêu cầu của con." : "Đã từ chối yêu cầu của con.");
      if (onDone) onDone();
    } catch (e) {
      toast(e.message, true);
    } finally {
      setBusyReq(null);
    }
  };

  return (
    <article className="kid-card">
      <div className="kid-head">
        <div className="kid-title">
          <div className="kid-avatar" aria-hidden="true">{initial}</div>
          <div>
            <h2>
              {c.name} <small className="muted">· {c.age} tuổi</small>
            </h2>
            {c.pending_requests > 0 && (
              <span className="chip warn">🔔 {c.pending_requests} yêu cầu đang chờ</span>
            )}
          </div>
        </div>
        {showLink && (
          <Link className="kid-link" to={`/child/${c.id}`}>
            Luật, báo cáo và yêu cầu →
          </Link>
        )}
      </div>

      {showLink && c.pending_items && c.pending_items.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {c.pending_items.map(r => (
            <div className="req pending" key={r.id}>
              <div>
                <b>{r.kind === "add-time" ? `⏰ Xin thêm ${r.minutes} phút` : `🔓 Báo chặn nhầm: ${r.subject}`}</b>
                <span className="muted"> · {dt(r.created_at)}</span>
                {r.message && <p>“{r.message}”</p>}
              </div>
              <div className="row">
                <button className="btn primary sm" type="button" disabled={busyReq === r.id} onClick={() => decide(r, true)}>
                  ✓ Đồng ý
                </button>
                <button className="btn ghost sm" type="button" disabled={busyReq === r.id} onClick={() => decide(r, false)}>
                  Từ chối
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {c.devices && c.devices.length > 0 ? (
        c.devices.map(d => <DeviceRow key={d.id} childId={c.id} d={d} onDone={onDone} />)
      ) : (
        <div className="dev">
          <p className="muted" style={{ margin: 0 }}>
            Chưa có máy tính nào được ghép đôi.{" "}
            {showLink && <Link to={`/child/${c.id}#devices`}>Thêm máy tính của con</Link>}
          </p>
        </div>
      )}
    </article>
  );
}

export default function Dashboard() {
  const toast = useToast();
  const [kids, load, live] = useStatus();
  const [name, setName] = useState("");
  const [age, setAge] = useState(9);
  const [adding, setAdding] = useState(false);
  const [stopping, setStopping] = useState(false);

  const isLocalHost = !isRemote && ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);

  const addChild = async e => {
    e.preventDefault();
    if (!name.trim()) return;
    setAdding(true);
    try {
      await api("POST", "/api/parent/children", { name: name.trim(), age: Number(age) || 9 });
      setName("");
      setAge(9);
      toast("Đã thêm hồ sơ cho con.");
      load();
    } catch (x) {
      toast(x.message, true);
    } finally {
      setAdding(false);
    }
  };

  const stopServer = async () => {
    if (!window.confirm("Tắt máy chủ bảng điều khiển trên máy bố mẹ? Máy của con vẫn tiếp tục áp dụng các luật đã lưu.")) return;
    setStopping(true);
    try {
      const r = await api("POST", "/api/parent/server/stop");
      toast(r.message || "Đang tắt máy chủ bảng điều khiển…");
    } catch (x) {
      toast(x.message, true);
      setStopping(false);
    }
  };

  if (kids === null) return <Loading />;

  return (
    <>
      <header className="page-head">
        <div className="head-row">
          <div>
            <h1>Bảng điều khiển gia đình</h1>
            <p className="lede">Theo dõi thời gian dùng máy, duyệt yêu cầu và điều chỉnh luật minh bạch cùng con.</p>
          </div>
          <LiveBadge live={live} />
        </div>
      </header>

      <section className="children">
        {kids.length ? (
          kids.map(c => <ChildCard key={c.id} c={c} onDone={load} showLink />)
        ) : (
          <section className="panel">
            <p className="muted" style={{ margin: 0 }}>
              Chưa có hồ sơ nào. Hãy thêm hồ sơ cho con ở khung bên dưới, sau đó tạo mã cài đặt để ghép đôi máy của con.
            </p>
          </section>
        )}
      </section>

      <section className="panel">
        <h2>Thêm hồ sơ cho con</h2>
        <form className="row wrap" onSubmit={addChild}>
          <label>
            Tên của con
            <input value={name} onChange={e => setName(e.target.value)} placeholder="Ví dụ: Bin" required maxLength={100} />
          </label>
          <label>
            Tuổi (3–17)
            <input type="number" min={3} max={17} value={age} onChange={e => setAge(e.target.value)} style={{ width: 110 }} required />
          </label>
          <button className="btn primary" disabled={adding}>
            {adding ? "Đang thêm…" : "Thêm con"}
          </button>
        </form>
      </section>

      <section className="panel">
        <div className="row between wrap">
          <span className="muted">
            Minh bạch tuyệt đối: chỉ ghi thời lượng, tên ứng dụng và tên miền cấp hai.{" "}
            <Link to="/privacy">Đọc cam kết quyền riêng tư</Link>
          </span>
          {isLocalHost && (
            <button className="btn ghost sm" type="button" disabled={stopping} onClick={stopServer}>
              {stopping ? "Đang tắt…" : "Tắt máy chủ bảng điều khiển"}
            </button>
          )}
        </div>
      </section>
    </>
  );
}
