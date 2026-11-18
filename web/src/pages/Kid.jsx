import { useEffect, useState } from "react";
import { api } from "../api.js";
import { AuditTable, dt, dur, Loading, Meters, UsageChart } from "../ui.jsx";

const ST = { pending: "đang chờ", approved: "bố mẹ đồng ý", denied: "bố mẹ chưa đồng ý" };

export default function Kid() {
  const [o, setO] = useState(null);
  useEffect(() => { api("GET", "/api/kid/overview").then(setO).catch(() => {}); }, []);
  if (!o) return <Loading />;
  const today = o.report.usage[o.report.usage.length - 1].seconds;
  return (
    <>
      <header className="page-head"><h1>Hôm nay em đã dùng gì, {o.child.name}?</h1>
        <p className="muted">Đây là đúng bộ báo cáo mà bố mẹ xem. Không có gì bị giấu.</p></header>
      <section className="panel hero-kid">
        <div className="garden" aria-hidden="true">{today >= 3600 ? "😴" : today >= 1800 ? "🌿" : "🌱"}</div>
        <div><p className="big-num">{dur(today)}</p>
          <p className="muted">{today >= 3600 ? "Em dùng khá lâu rồi. Nghỉ mắt một chút nhé!" : today >= 1800 ? "Cây của em đang lớn lên rất tốt." : "Hôm nay mới bắt đầu, cây vẫn còn nhỏ."}</p></div>
      </section>
      <section className="panel"><h2>7 ngày qua</h2><UsageChart points={o.report.usage} /></section>
      <section className="grid2">
        <div className="panel"><h2>Ứng dụng em hay dùng</h2><Meters items={o.report.apps} /></div>
        <div className="panel"><h2>Trang web em hay vào</h2><Meters items={o.report.domains} /></div>
      </section>
      <section className="panel"><h2>Ai đã đổi luật, lúc nào?</h2><AuditTable rows={o.audit} /></section>
      <section className="panel"><h2>Yêu cầu em đã gửi</h2>
        {o.requests.length ? o.requests.map(r => (
          <p key={r.id}>{dt(r.created_at)} · {r.kind === "add-time" ? `Xin thêm ${r.minutes} phút` : `Báo chặn nhầm: ${r.subject}`} → <b>{ST[r.status]}</b></p>
        )) : <p className="muted">Em chưa gửi yêu cầu nào.</p>}
      </section>
    </>
  );
}
