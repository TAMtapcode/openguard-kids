(() => {
  "use strict";
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const csrf = () => document.querySelector('meta[name="csrf"]').content;
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") n.className = v; else if (k.startsWith("data-")) n.setAttribute(k, v); else n[k] = v;
    }
    kids.flat().forEach(c => n.append(c));
    return n;
  };
  function toast(msg, err = false) {
    const t = $("#toast"); t.textContent = msg; t.className = "show" + (err ? " err" : "");
    clearTimeout(toast.h); toast.h = setTimeout(() => (t.className = ""), 3500);
  }
  async function api(method, url, body) {
    const r = await fetch(url, { method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() }, body: body === undefined ? undefined : JSON.stringify(body) });
    let data = null; try { data = await r.json(); } catch (_) {}
    if (r.status === 401) { location.href = "/login"; throw new Error("auth"); }
    if (!r.ok) { const d = data && data.detail; const m = Array.isArray(d) ? d.map(x => x.msg).join("; ") : (d || "Có lỗi xảy ra."); toast(m, true); throw new Error(m); }
    return data;
  }
  const mins = s => Math.max(0, Math.round(s / 60));
  const dur = s => { const m = mins(s), h = Math.floor(m / 60); return h ? `${h} giờ ${String(m % 60).padStart(2, "0")} phút` : `${m} phút`; };

  /* ---------- Trạng thái trực tiếp ---------- */
  function deviceNode(c, d) {
    const used = d.used_today_sec, rem = d.remaining_sec, total = used + Math.max(rem, 0);
    const pct = total > 0 ? Math.min(100, Math.round(100 * used / total)) : 100;
    const cls = d.state === "locked" || rem <= 0 ? "out" : rem < 600 ? "low" : "";
    const label = !d.online ? "Ngoại tuyến" : d.state === "locked" ? "Đang khóa" : d.state === "grace" ? "Sắp hết giờ" : "Đang bật";
    const bar = el("div", { class: "timebar " + cls }, el("i", { style: `width:${pct}%` }));
    const node = el("div", { class: "dev" },
      el("div", { class: "dev-top" },
        el("span", {}, el("span", { class: "dot " + (d.state === "locked" ? "lock" : d.online ? "on" : "") }), el("b", {}, d.name), " ", el("span", { class: "muted" }, label)),
        el("span", { class: "muted" }, `Đã dùng ${dur(used)}` + (d.online ? ` · còn ${dur(rem)}` : ""))),
      bar);
    if (!d.ui_ok) node.append(el("p", { class: "chip warn" }, "Biểu tượng khay không hiển thị — agent đã tạm dừng chặn."));
    if (!d.policy_ok && d.online) node.append(el("p", { class: "muted" }, "Đang áp dụng luật mới…"));
    node.append(el("div", { class: "actions" },
      el("button", { class: "btn danger", type: "button", "data-cmd": "LOCK_NOW", "data-child": c.id, "data-device": d.id }, "Khóa máy ngay"),
      d.state === "locked" ? el("button", { class: "btn ghost", type: "button", "data-cmd": "UNLOCK", "data-child": c.id, "data-device": d.id }, "Mở khóa") : "",
      el("button", { class: "btn primary", type: "button", "data-cmd": "ADD_TIME", "data-child": c.id, "data-device": d.id }, "Cộng 15 phút")));
    return node;
  }
  function childNode(c, link) {
    const card = el("article", { class: "kid-card" },
      el("div", { class: "kid-head" }, el("h2", {}, c.name), el("span", { class: "muted" }, `${c.age} tuổi`),
        c.pending_requests ? el("span", { class: "chip warn" }, `${c.pending_requests} yêu cầu mới`) : "",
        link ? el("a", { href: `/child/${c.id}` }, "Luật, báo cáo và yêu cầu") : ""));
    if (!c.devices.length) card.append(el("p", { class: "muted" }, "Chưa ghép đôi máy nào. Mở “Luật, báo cáo và yêu cầu” để tạo mã ghép đôi."));
    c.devices.forEach(d => card.append(deviceNode(c, d)));
    return card;
  }
  async function refreshStatus() {
    const box = $("[data-role=dashboard]") || $("[data-role=child-live]");
    if (!box) return;
    const only = box.dataset.child ? Number(box.dataset.child) : null;
    try {
      const data = await api("GET", "/api/parent/status");
      const kids = data.children.filter(c => only === null || c.id === only);
      box.replaceChildren(...(kids.length ? kids.map(c => childNode(c, only === null)) : [el("p", { class: "muted" }, "Chưa có hồ sơ nào. Hãy thêm con ở bên dưới.")]));
    } catch (_) {}
  }
  if ($("[data-role=dashboard]") || $("[data-role=child-live]")) { refreshStatus(); setInterval(refreshStatus, 5000); }

  /* ---------- Hành động ---------- */
  document.addEventListener("click", async ev => {
    const b = ev.target.closest("[data-action],[data-cmd]"); if (!b) return;
    try {
      if (b.dataset.cmd) {
        const act = b.dataset.cmd;
        if (act === "LOCK_NOW" && !confirm("Khóa máy của con ngay bây giờ?")) return;
        const r = await api("POST", `/api/parent/children/${b.dataset.child}/command`, { action: act, minutes: 15, device_id: b.dataset.device });
        toast(act === "LOCK_NOW" ? "Đã gửi lệnh khóa máy." : act === "ADD_TIME" ? "Đã cộng 15 phút." : "Đã mở khóa.");
        refreshStatus(); return;
      }
      switch (b.dataset.action) {
        case "logout": await api("POST", "/logout"); location.href = "/login"; break;
        case "decide": await api("POST", `/api/parent/requests/${b.dataset.id}/decide`, { approve: b.dataset.approve === "1", minutes: Number(b.dataset.minutes) || null }); toast("Đã phản hồi con."); location.reload(); break;
        case "revoke": if (confirm("Gỡ thiết bị này khỏi quản lý? Agent sẽ ngừng áp dụng luật.")) { await api("POST", `/api/parent/devices/${b.dataset.id}/revoke`); location.reload(); } break;
        case "wipe": { const typed = prompt(`Việc này xóa toàn bộ dữ liệu hoạt động của ${b.dataset.name} trên máy chủ và máy con, không thể khôi phục.\nGõ tên con để xác nhận:`);
          if (typed && typed.trim() === b.dataset.name) { await api("DELETE", `/api/parent/children/${b.dataset.child}/data`); toast("Đã xóa dữ liệu."); setTimeout(() => location.reload(), 800); } break; }
        case "toggle-user": await api("POST", `/api/admin/users/${b.dataset.id}/toggle`); location.reload(); break;
      }
    } catch (_) {}
  });

  document.addEventListener("submit", async ev => {
    const f = ev.target.closest("form[data-form]"); if (!f) return;
    ev.preventDefault(); const fd = Object.fromEntries(new FormData(f));
    try {
      if (f.dataset.form === "add-child") { await api("POST", "/api/parent/children", { name: fd.name, age: Number(fd.age) }); toast("Đã thêm con."); f.reset(); refreshStatus(); }
      if (f.dataset.form === "viewer") { await api("POST", `/api/parent/children/${f.dataset.child}/viewer`, fd); toast("Đã tạo tài khoản xem cho con."); f.reset(); }
    } catch (_) {}
  });

  const mk = $("#make-code");
  if (mk) mk.addEventListener("click", async () => {
    try {
      const r = await api("POST", `/api/parent/children/${mk.dataset.child}/enroll-code?consent=${$("#consent").checked}`);
      $("#code-out").textContent = r.install_code || r.code; $("#copy-code").hidden = false; toast("Mã có hiệu lực 10 phút.");
    } catch (_) {}
  });

  const cc = $("#copy-code");
  if (cc) cc.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#code-out").textContent); toast("Đã sao chép mã. Dán vào trình cài đặt trên máy của con."); }
    catch (_) { toast("Hãy bôi đen mã và nhấn Ctrl+C.", true); }
  });

  /* ---------- Trình soạn chính sách ---------- */
  const pd = $("#policy-data");
  if (pd) {
    const P = JSON.parse(pd.textContent);
    const grid = $("#sched"); let sched = P.schedule.map(r => r.slice()); let paint = null;
    const DAYS = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "CN"];
    function drawGrid() {
      grid.replaceChildren(el("span"));
      for (let h = 0; h < 24; h += 3) grid.append(el("span", { class: "hh" }, `${h}h`));
      sched.forEach((row, r) => {
        grid.append(el("span", { class: "dn", title: "Bấm để đảo cả ngày", "data-day": r }, DAYS[r]));
        row.forEach((v, c) => grid.append(el("div", { class: "c" + (v ? " on" : ""), "data-r": r, "data-c": c, role: "gridcell", "aria-label": `${DAYS[r]} ${String(Math.floor(c / 2)).padStart(2, "0")}:${c % 2 ? "30" : "00"}`, "aria-selected": !!v })));
      });
    }
    const setCell = (n, v) => { const r = +n.dataset.r, c = +n.dataset.c; sched[r][c] = v; n.classList.toggle("on", !!v); };
    grid.addEventListener("pointerdown", e => { const n = e.target.closest(".c"); if (!n) { const d = e.target.closest(".dn"); if (d) { const r = +d.dataset.day, v = sched[r].every(x => x) ? 0 : 1; sched[r] = sched[r].map(() => v); drawGrid(); } return; }
      paint = sched[+n.dataset.r][+n.dataset.c] ? 0 : 1; setCell(n, paint); });
    grid.addEventListener("pointerover", e => { if (paint === null || e.buttons !== 1) return; const n = e.target.closest(".c"); if (n) setCell(n, paint); });
    addEventListener("pointerup", () => (paint = null));
    $$("[data-preset]").forEach(b => b.addEventListener("click", () => {
      const p = b.dataset.preset; sched = sched.map(() => Array.from({ length: 48 }, (_, i) => p === "open" ? 1 : p === "closed" ? 0 : (i >= 12 && i < 42 ? 1 : 0))); drawGrid(); }));
    const lines = t => t.split("\n").map(s => s.trim()).filter(Boolean);
    const appsToText = a => a.map(x => [x.name || "", x.sha256 || "", x.group || ""].join(" | ").replace(/( \| )+$/, "")).join("\n");
    const textToApps = t => lines(t).map(l => { const [name, sha256, group] = l.split("|").map(s => s.trim()); return { name: name || "", sha256: sha256 || "", group: group || undefined }; });
    function fill() {
      $("#q-wd").value = P.quota.weekday_min; $("#q-we").value = P.quota.weekend_min;
      $$("[data-group]").forEach(i => (i.value = P.group_budgets_min[i.dataset.group] ?? ""));
      $$("[data-blockcat]").forEach(i => (i.checked = P.domains.blocked_categories.includes(i.dataset.blockcat)));
      $("#dom-blocked").value = P.domains.blocked.join("\n"); $("#dom-allowed").value = P.domains.allowed.join("\n");
      $("#safesearch").checked = P.domains.safesearch; $("#blockdoh").checked = P.domains.block_doh;
      $("#app-mode").value = P.apps.mode; $("#apps-blocked").value = appsToText(P.apps.blocked); $("#apps-allowed").value = appsToText(P.apps.allowed);
      $("#ltp-on").checked = P.learn_to_play.enabled; $("#ltp-ratio").value = P.learn_to_play.earn_ratio; $("#ltp-cap").value = P.learn_to_play.daily_cap_min;
      $("#stale-days").value = P.failsafe.stale_after_days; drawGrid();
    }
    fill();
    $("#save-policy").addEventListener("click", async () => {
      const gb = {}; $$("[data-group]").forEach(i => { if (i.value !== "") gb[i.dataset.group] = Number(i.value); });
      const body = { quota: { weekday_min: Number($("#q-wd").value), weekend_min: Number($("#q-we").value) }, schedule: sched, group_budgets_min: gb,
        domains: { blocked: lines($("#dom-blocked").value), allowed: lines($("#dom-allowed").value), safesearch: $("#safesearch").checked, block_doh: $("#blockdoh").checked,
          blocked_categories: $$("[data-blockcat]").filter(i => i.checked).map(i => i.dataset.blockcat) },
        apps: { mode: $("#app-mode").value, blocked: textToApps($("#apps-blocked").value), allowed: textToApps($("#apps-allowed").value) },
        learn_to_play: { enabled: $("#ltp-on").checked, earn_ratio: Number($("#ltp-ratio").value), daily_cap_min: Number($("#ltp-cap").value) },
        failsafe: { stale_after_days: Number($("#stale-days").value) } };
      try { const r = await api("PUT", `/api/parent/children/${$("#page-data").dataset.child}/policy`, body); $("#pol-version").textContent = r.version; toast(`Đã lưu luật (phiên bản ${r.version}). Máy con áp dụng trong vòng 60 giây.`); } catch (_) {}
    });
    const hf = $("#hashfile");
    hf.addEventListener("change", async () => {
      const out = $("#hashout"); if (!hf.files[0]) return;
      if (!(window.crypto && crypto.subtle)) { out.textContent = "Trình duyệt cần HTTPS để băm tệp. Hoặc chạy: python -m agent hash <tệp>"; return; }
      const buf = await hf.files[0].arrayBuffer(); const h = await crypto.subtle.digest("SHA-256", buf);
      out.textContent = [...new Uint8Array(h)].map(b => b.toString(16).padStart(2, "0")).join("");
    });
  }
})();
