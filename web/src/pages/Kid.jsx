import { useCallback, useState } from "react";
import { api } from "../api.js";
import { AuditTable, dt, dur, LiveBadge, Loading, Meters, UsageChart, usePoll, useRealtime } from "../ui.jsx";

const ST = { pending: "đang chờ bố mẹ", approved: "bố mẹ đồng ý 🎉", denied: "bố mẹ chưa đồng ý" };

export default function Kid() {
  const [o, setO] = useState(null);
  const load = useCallback(() => api("GET", "/api/kid/overview").then(setO).catch(() => {}), []);
  const live = useRealtime(() => load());
  usePoll(load, live ? 15000 : 8000, [load, live]);
  if (!o) return <Loading />;
  const today = o.report.usage[o.report.usage.length - 1].seconds;
  const edu = o.report.garden?.education_sec || 0;
  const play = o.report.garden?.play_sec || 0;
  const mood = today >= 3 * 3600 ? "tired" : edu > play && edu > 300 ? "lush" : "ok";
  const icon = mood === "tired" ? "😴" : mood === "lush" ? "🌳" : today >= 1800 ? "🌿" : "🌱";
  const msg = mood === "tired"
    ? "Bé dùng máy hơi lâu rồi. Đứng dậy vươn vai nghỉ mắt một chút nhé!"
    : mood === "lush"
      ? "Cây của bé xanh mướt vì hôm nay bé học tập chăm quá!"
      : today >= 1800 ? "Cây của bé đang lớn lên rất tốt nè." : "Hôm nay mới bắt đầu, cùng chăm cây xanh nhé!";
  return (
    <>
      <header className="page-head">
        <div className="head-row">
          <div>
            <h1>Chào {o.child.name}! Hôm nay bé dùng máy thế nào?</h1>
            <p className="lede">Đây là đúng bộ báo cáo mà bố mẹ xem — không có gì bị giấu đâu nhé.</p>
          </div>
          <LiveBadge live={live} />
        </div>
      </header>
      <section className={"panel garden " + mood}>
        <div className="garden-icon" aria-hidden="true">{icon}</div>
        <div className="garden-body">
          <p className="big-num">{dur(today)}</p>
          <p>{msg}</p>
          <div className="garden-stats">
            <span>📚 Học tập: <b>{Math.round(edu / 60)} phút</b></span>
            <span>🎮 Giải trí: <b>{Math.round(play / 60)} phút</b></span>
          </div>
        </div>
      </section>
      <section className="panel"><h2>7 ngày vừa qua</h2><UsageChart points={o.report.usage} /></section>
      <section className="grid2">
        <div className="panel"><h2>Ứng dụng bé hay dùng</h2><Meters items={o.report.apps} /></div>
        <div className="panel"><h2>Trang web bé hay vào</h2><Meters items={o.report.domains} /></div>
      </section>
      <section className="panel"><h2>Yêu cầu bé đã gửi cho bố mẹ</h2>
        {o.requests.length ? o.requests.map(r => (
          <div className="req" key={r.id}>
            <div><b>{r.kind === "add-time" ? `⏰ Xin thêm ${r.minutes} phút` : `🔓 Báo chặn nhầm: ${r.subject}`}</b>
              <span className="muted"> · {dt(r.created_at)}</span></div>
            <span className={"chip " + r.status}>{ST[r.status] || r.status}</span>
          </div>
        )) : <p className="muted">Bé chưa gửi yêu cầu nào.</p>}
      </section>
      <section className="panel"><h2>Ai đã đổi quy định, lúc nào?</h2><AuditTable rows={o.audit} /></section>
    </>
  );
}
