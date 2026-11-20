"""Engine: điều phối toàn bộ Agent (đếm giờ, cưỡng chế, DNS, tiến trình, đồng bộ, IPC).
Chạy trong Windows Service (LocalSystem); cũng chạy được ở chế độ console để phát triển/kiểm thử."""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from urllib.parse import urlparse

from common.schema import BROWSER_PROCESSES, CATEGORIES, friendly_app_name, is_valid_subject, second_level_domain
from common.signing import verify

from .classifier import DomainFilter
from .clock import TrustedClock
from .config import VERSION, AgentConfig
from .dns_proxy import DnsProxy
from .ipc import IpcServer, load_or_create_token
from .policy_engine import Decision, Enforcer, Policy, UsageAccountant, local_dt
from .process_guard import ProcessGuard
from .store import AgentStore
from .sync_client import AuthLost, Offline

log = logging.getLogger("ogk")
IGNORED_APPS = {"explorer.exe", "searchhost.exe", "lockapp.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
                "pythonw.exe", "ogk.exe", "ogk-tray.exe", "applicationframehost.exe", "textinputhost.exe"}
DOMAIN_WINDOW = 120.0         # giây: hai truy vấn cùng tên miền cách nhau < 120s được coi là đang xem liên tục
BROWSER_ACTIVE_WINDOW = 90.0  # giây: duy trì đếm tên miền gần nhất khi trình duyệt vẫn đang ở tiền cảnh & có thao tác
RESTORE_BREAK = 300.0         # nghỉ >= 5 phút thì tính lại thời gian dùng liên tục
EYE_BREAK = 3600.0
MANUAL_LOCK_MIN = 60          # phút: "Khóa ngay" tự mở sau thời gian này để máy không bao giờ bị kẹt khóa


class Engine:
    def __init__(self, cfg: AgentConfig, store: AgentStore | None = None, platform=None, clock=None, mono=time.monotonic,
                 server=None, policy_key: bytes | None = None, process_source=None, killer=None):
        from .winapi import get_platform
        self.cfg, self.mono, self.server, self.policy_key = cfg, mono, server, policy_key
        self.store = store or AgentStore(cfg.db_path)
        if cfg.device_id and self.store.kv_get("cmd_device_id") != cfg.device_id:
            self.store.reset_commands()
            self.store.kv_set("cmd_device_id", cfg.device_id)
        self.platform = platform or get_platform()
        self.clock = clock or TrustedClock(self.store, mono=mono)
        self.lock = threading.RLock()
        self.policy: Policy | None = None
        self.acc = UsageAccountant(self.store, mono)
        self.enforcer = Enforcer(mono)
        self.decision = Decision()
        self.enrolled = bool(cfg.device_id)
        self.online = False
        self.notes: deque = deque(maxlen=100)
        self._nid = 0
        self._rl: dict = {}
        self.telemetry = {"ts": None, "idle": 0.0, "locked": False, "fg": None}
        self._ui_missing_since = None
        self._last_tray_launch = -1e9
        self.suspended = False
        self.applies = True
        self._last_active = -1e9
        self._dom_groups: dict = {}
        self._dom_last: dict = {}
        self._dom_agg: dict = {}
        self._active_dom: str | None = None
        self._active_dom_ts: float = -1e9
        self._app_cur, self._app_seg = None, 0.0
        self._cont, self._inactive, self._eye_t = 0.0, 0.0, 0.0
        self._last_flush = self._last_slow = mono()
        self._pruned_day = None
        self._stop, self._wake = threading.Event(), threading.Event()
        self._threads: list = []
        host = urlparse(cfg.server_url).hostname if cfg.server_url else None
        self.domain_filter = DomainFilter(lambda: self.policy, self.group_exhausted, self.is_stale,
                                          extra_exempt={host} if host else set(), suspended=lambda: self.suspended or not self.applies)
        kw = {k: v for k, v in (("source", process_source), ("killer", killer)) if v}
        self.guard = ProcessGuard(lambda: self.policy, self.group_exhausted, lambda: self.suspended or not self.applies, **kw)
        self.dns = DnsProxy(self.domain_filter, cfg.upstream_dns, cfg.dns_host, cfg.dns_port, lambda: self.policy,
                            self._on_dns_block, self._on_dns_query)
        self.ipc: IpcServer | None = None
        self.ws = None
        self._dns_state = cfg.home / "run" / "dns_prev.json"

    # ================================================================= tiện ích
    def _parts(self):
        tz = self.policy.tz if self.policy else 420
        dt = local_dt(self.clock.now(), tz)
        return dt, dt.date().isoformat()

    def event(self, type_, subject="", duration=0):
        if subject and not is_valid_subject(subject):
            subject = subject.replace("/", "_").replace("?", "_")[:120]
        self.store.add_event(type_, subject, duration, self.policy.version if self.policy else 0, int(self.clock.now()))

    def notify(self, kind, title, body, **extra):
        with self.lock:
            self._nid += 1
            self.notes.append({"id": self._nid, "kind": kind, "title": title, "body": body, "ts": time.time(), **extra})

    def _rate_ok(self, key, secs) -> bool:
        now = self.mono()
        if now - self._rl.get(key, -1e9) < secs:
            return False
        self._rl[key] = now
        return True

    def _group_bonus_sec(self, day: str, g: str) -> float:
        return self.acc.get(day, f"gbonus:{g}") + self.acc.get(day, "gbonus")

    def group_exhausted(self, g) -> bool:
        p = self.policy
        if not p or g not in p.group_budget:
            return False
        _, day = self._parts()
        return self.acc.get(day, f"g:{g}") >= p.group_budget[g] + self._group_bonus_sec(day, g)

    def is_stale(self) -> bool:
        if not self.policy:
            return False
        last = float(self.store.kv_get("last_sync", 0) or 0)
        return last > 0 and self.clock.now() - last > self.policy.stale_days * 86400

    # ================================================================= chính sách
    def load_cached_policy(self):
        raw = self.store.kv_get("policy_env")
        if raw and self.policy_key:
            env = json.loads(raw)
            if verify(env["policy"], env["sig"], self.policy_key):
                self.policy = Policy(env["policy"])

    def apply_envelope(self, env: dict) -> bool:
        """Từ chối chính sách sai chữ ký, sai thiết bị hoặc phiên bản cũ hơn (chống phát lại)."""
        with self.lock:
            doc = env.get("policy", {})
            if not self.policy_key or not verify(doc, env.get("sig", ""), self.policy_key):
                self.notify("integrity", "Chính sách không hợp lệ", "Máy từ chối một chính sách có chữ ký sai và tiếp tục dùng bản trước.")
                log.warning("policy signature rejected")
                return False
            if doc.get("device_id") != self.cfg.device_id or (self.policy and doc["version"] < self.policy.version):
                return False
            self.store.kv_set("policy_env", json.dumps(env))
            self.policy = Policy(doc)
            self.dns.flush_cache()
            if getattr(self, "_dns6", None):
                self._dns6.flush_cache()
            self.platform.flush_dns()
            self._wake.set()
            self.notify("policy", "Luật mới đã được áp dụng", f"Phiên bản luật {self.policy.version}. Bạn có thể xem ai đã đổi gì trong mục “Nhật ký luật”.")
            return True

    # ================================================================= vòng lặp chính
    def tick(self):
        with self.lock:
            mono = self.mono()
            self._check_clock()
            self.applies = self._user_is_monitored()
            ui_ok = self._ui_state()
            self.suspended = (not ui_ok) and self._ui_missing_since is not None and mono - self._ui_missing_since > self.cfg.ui_grace_sec
            dt_local, day = self._parts()
            active = self._user_active() and self.decision.state != "locked"
            fg = self._foreground() if active else None
            groups = self._active_groups(active, fg, mono)
            dt = self.acc.tick(day, active, groups)
            if active:
                self._last_active = mono
            self._track(day, active, fg, dt, mono)
            self._enforce(day, dt_local)
            if self.enrolled and not self.suspended:
                self._scan_processes()
                if self.cfg.block_taskmgr and self.applies and self._rate_ok("taskmgr_pol", 15):
                    self.platform.set_taskmgr_policy(True, self.platform.active_user())
                elif (not self.applies or not self.cfg.block_taskmgr) and self._rate_ok("taskmgr_pol", 15):
                    self.platform.set_taskmgr_policy(False)
            self._housekeeping(mono, day)

    def _check_clock(self):
        d = self.clock.check_tamper()
        if d is not None:
            self.event("quota_warning", f"clock_tamper:{int(d)}s")
            self.notify("clock", "Đồng hồ máy bị chỉnh lệch",
                        f"Đồng hồ Windows lệch khoảng {abs(int(d)) // 60} phút. Thời gian dùng máy vẫn được tính bằng đồng hồ phần cứng nên chỉnh giờ không có tác dụng.")

    def _fresh_tel(self) -> bool:
        return self.telemetry["ts"] is not None and self.mono() - self.telemetry["ts"] < 10

    def _user_is_monitored(self) -> bool:
        users = {u.split("\\")[-1].lower() for u in self.cfg.monitored_users}
        return not users or self.platform.active_user().split("\\")[-1].lower() in users

    def _ui_state(self) -> bool:
        if not self.applies or not self.cfg.require_tray or not self.platform.has_user_session():
            self._ui_missing_since = None
            return True
        if self._fresh_tel():
            if self._ui_missing_since is not None:
                self.notify("resumed", "Open Guardian Kids đã hoạt động trở lại", "Biểu tượng khay đã hiển thị lại.")
            self._ui_missing_since = None
            return True
        now = self.mono()
        if self._ui_missing_since is None:
            self._ui_missing_since = now
        if now - self._last_tray_launch > 10:
            self._last_tray_launch = now
            self.platform.launch_tray()
        return False

    def _user_active(self) -> bool:
        if not self.applies or not self.platform.has_user_session():
            return False
        if self._fresh_tel():
            locked, idle = self.telemetry["locked"], self.telemetry["idle"]
        elif self.cfg.require_tray:
            locked, idle = False, 0.0
        else:
            locked, idle = self.platform.session_locked(), self.platform.idle_seconds()
        limit = self.policy.idle_seconds if self.policy else 300
        return (not locked) and idle < limit

    def _foreground(self):
        fg = self.telemetry["fg"] if self._fresh_tel() else (None if self.cfg.require_tray else self.platform.foreground_process())
        return fg

    def _active_groups(self, active, fg, mono) -> set:
        if not active or not self.policy:
            return set()
        g = set()
        if fg:
            grp = self.policy.app_group(fg)
            if grp:
                g.add(grp)
        g |= {c for c, t in self._dom_groups.items() if mono - t < DOMAIN_WINDOW}
        return g

    def _track(self, day, active, fg, dt, mono=None):
        mono = self.mono() if mono is None else mono
        name = fg if (active and fg and fg not in IGNORED_APPS) else None
        if name != self._app_cur:
            self._flush_app()
            self._app_cur = name
            if name:
                self.event("app_start", name)
        if name and dt:
            self._app_seg += dt
            self.acc.add(day, f"app:{name}", dt)

        # Kết hợp tín hiệu DNS với ngữ cảnh trình duyệt đang ở tiền cảnh để tính thời lượng web chính xác hơn
        if active and dt and fg and fg.lower() in BROWSER_PROCESSES:
            if self._active_dom and (mono - self._active_dom_ts) <= BROWSER_ACTIVE_WINDOW:
                agg = self._dom_agg.setdefault(self._active_dom, [0.0, 0])
                agg[0] += dt
                self.acc.add(day, f"dom:{self._active_dom}", dt)
                self._dom_last[self._active_dom] = mono

        if active:
            self._cont += dt
            self._inactive = 0.0
            if self._cont - self._eye_t >= EYE_BREAK:
                self._eye_t = self._cont
                self.notify("eye_break", "Nghỉ mắt một chút nhé", "Em đã dùng máy liên tục 60 phút. Hãy nhìn ra xa 20 giây và đứng dậy vận động.")
        else:
            self._inactive += max(0.0, self.cfg.tick_interval)
            if self._inactive >= RESTORE_BREAK:
                self._cont = self._eye_t = 0.0

    def _flush_app(self):
        if self._app_cur and self._app_seg >= 1:
            self.event("app_stop", self._app_cur, int(self._app_seg))
        self._app_seg = 0.0

    def _flush_domains(self):
        for sld, (sec, cnt) in list(self._dom_agg.items()):
            if cnt or sec >= 1:
                self.event("domain_query", sld, int(sec))
        self._dom_agg.clear()

    # ----------------------------------------------------------------- cưỡng chế thời gian
    def _enforce(self, day, dt_local):
        if not (self.policy and self.enrolled) or self.suspended or not self.applies:
            prev, self.decision = self.decision, Decision(state="suspended" if self.suspended else "ok")
            return
        manual = float(self.store.kv_get("manual_lock_until", "0") or 0) > self.clock.now()
        prev = self.decision
        ov_left = float(self.store.kv_get("unlock_override_until", "0") or 0) - self.clock.now()
        if not manual and ov_left > 0:
            self.enforcer.reset("ok")
            limit = self.policy.limit_sec(dt_local.weekday()) + self.acc.get(day, "bonus")
            self.decision = Decision(state="ok", remaining_sec=ov_left, limit_sec=limit)
            return
        dec = self.enforcer.evaluate(self.policy, dt_local, self.acc.get(day, "total"), self.acc.get(day, "bonus"), manual)
        self.decision = dec
        for _ in dec.warnings:
            mins = max(1, math.ceil(dec.remaining_sec / 60))
            self.event("quota_warning", f"{mins}min")
            self.notify("warning", "Sắp hết giờ", f"Còn khoảng {mins} phút. Hãy lưu bài và chuẩn bị nghỉ nhé.", remaining_sec=int(dec.remaining_sec))
        if dec.state == "grace" and prev.state != "grace":
            self.event("quota_warning", f"grace_{self.policy.grace}s")
            self.notify("grace", "Hết giờ rồi!", f"Em có {self.policy.grace} giây để lưu bài trước khi máy khóa.",
                        grace_sec=self.policy.grace, reason=dec.reason, can_request=True)
        if dec.state == "locked":
            if prev.state != "locked":
                self.event("locked", dec.reason or "quota")
                self.notify("locked", "Máy đang khóa", self._lock_text(dec.reason), reason=dec.reason, can_request=True,
                            emergency="Cần giúp đỡ khẩn cấp? Gọi Tổng đài quốc gia bảo vệ trẻ em 111 hoặc nhờ người lớn.")
                if self.platform.has_user_session():
                    self.platform.lock_session()

    @staticmethod
    def _lock_text(reason):
        return {"manual": "Bố mẹ đã khóa máy. Em có thể nhắn xin thêm giờ.",
                "schedule": "Hiện đang ngoài khung giờ dùng máy do bố mẹ đặt trong lịch tuần.",
                }.get(reason, "Hôm nay em đã dùng hết thời gian. Em có thể xin bố mẹ thêm giờ.")

    def enforce_now(self):
        with self.lock:
            dt_local, day = self._parts()
            self._enforce(day, dt_local)

    # ----------------------------------------------------------------- tiến trình & DNS
    def _scan_processes(self):
        for v in self.guard.scan():
            label = CATEGORIES.get(v.group or "", None)
            disp = v.display_name or friendly_app_name(v.name)
            if v.reason == "allowlist":
                why, by = "Chỉ các ứng dụng trong danh sách cho phép mới được mở.", f"Phụ huynh ({self.policy.set_by})"
            elif v.reason == "group_budget":
                why, by = f"Em đã dùng hết ngân sách hôm nay của nhóm “{label or v.group}”.", f"Phụ huynh ({self.policy.set_by})"
            else:
                why, by = "Ứng dụng này nằm trong danh sách chặn.", f"Phụ huynh ({self.policy.set_by})"
            self.event("blocked_app", v.name)
            if self._rate_ok(("app", v.name), 10):
                self.notify("blocked", "Ứng dụng đã bị đóng", why, subject=v.name, display_subject=disp,
                            category=label or "Chưa phân loại", by=by, kind_of="app", can_request=True, killed=v.killed)

    def _on_dns_block(self, v, qname):
        sld = second_level_domain(qname)
        with self.lock:
            if self._rate_ok(("dom", sld), 30):
                self.event("blocked_domain", sld)
            if self._rate_ok("popup", 8):
                self.notify("blocked", "Trang web bị chặn", v.reason, subject=sld, category=CATEGORIES.get(v.category or "", "—"),
                            by=v.by, kind_of="domain", can_request=True)

    def _on_dns_query(self, qname):
        with self.lock:
            mono = self.mono()
            sld = second_level_domain(qname)
            cat = self.policy.category_of(qname) if self.policy else "uncategorized"
            if mono - self._last_active > 60:
                return
            self._dom_groups[cat] = mono
            self._active_dom = sld
            self._active_dom_ts = mono
            agg = self._dom_agg.setdefault(sld, [0.0, 0])
            agg[1] += 1
            fg = (self._foreground() or "").lower()
            if fg not in BROWSER_PROCESSES:
                last = self._dom_last.get(sld)
                if last is not None and mono - last < DOMAIN_WINDOW:
                    agg[0] += mono - last
                    self.acc.add(self._parts()[1], f"dom:{sld}", mono - last)
            self._dom_last[sld] = mono

    def _housekeeping(self, mono, day):
        if mono - self._last_flush >= 10:
            self._last_flush = mono
            self.acc.flush()
            self.clock.persist(self.store)
        if mono - self._last_slow >= 60:
            self._last_slow = mono
            if self._app_cur:
                self._flush_app()
            self._flush_domains()
            self.store.kv_set("flush_day", day)
        if day != self._pruned_day:
            self._pruned_day = day
            from datetime import date, timedelta
            d0 = date.fromisoformat(day)
            self.store.prune_counters({(d0 - timedelta(days=i)).isoformat() for i in range(14)})

    # ================================================================= lệnh khẩn
    def handle_command(self, cmd: dict) -> bool:
        with self.lock:
            cid, act = cmd.get("id"), cmd.get("action")
            if cid is not None and self.store.command_applied(cid):
                return True
            _, day = self._parts()
            if act in ("LOCK_NOW", "UNLOCK") and cid is not None:
                last = int(self.store.kv_get("state_cmd_seq", "0") or 0)
                if int(cid) < last:
                    self.store.mark_command(cid)
                    return True
                self.store.kv_set("state_cmd_seq", int(cid))
            if act == "LOCK_NOW":
                mins = max(1, min(int(cmd.get("minutes") or MANUAL_LOCK_MIN), 240))
                self.store.kv_set("manual_lock_until", f"{self.clock.now() + mins * 60:.0f}")
                self.store.kv_set("unlock_override_until", "0")
                self.event("locked", "manual")
            elif act == "UNLOCK":
                self.store.kv_set("manual_lock_until", "0")
                self.store.kv_set("unlock_override_until", "0")
                self.enforce_now()
                if self.decision.state in ("locked", "grace") and self.decision.reason in ("quota", "schedule"):
                    mins = max(1, min(int(cmd.get("minutes") or 15), 240))
                    self.store.kv_set("unlock_override_until", f"{self.clock.now() + mins * 60:.0f}")
                    self.event("override_granted", "parent_unlock", mins * 60)
                    self.notify("bonus", "Bố mẹ đã mở khóa", f"Em được dùng máy thêm {mins} phút.")
            elif act == "ADD_TIME":
                m = max(0, min(int(cmd.get("minutes", 15)), 240))
                self.acc.add(day, "bonus", m * 60)
                if m > 0:
                    self.store.kv_set("manual_lock_until", "0")
                    cur_ov = float(self.store.kv_get("unlock_override_until", "0") or 0)
                    new_ov = self.clock.now() + m * 60
                    if new_ov > cur_ov:
                        self.store.kv_set("unlock_override_until", f"{new_ov:.0f}")
                    self.enforcer.reset("ok")
                self.event("override_granted", "parent_add_time", m * 60)
                self.notify("bonus", "Bố mẹ cộng thêm giờ", f"Em được thêm {m} phút. Máy đã sẵn sàng để em dùng!")
            elif act == "WIPE_LOCAL":
                self.flush_all()
                self.store.wipe_activity(day)
                self.acc.reload()
                self.notify("wipe", "Dữ liệu đã được xóa", "Bố mẹ đã xóa dữ liệu hoạt động của em trên máy này.")
            elif act == "POLICY_UPDATED":
                if self.server and self.enrolled:
                    threading.Thread(target=self.sync_once, name="ogk-policy-sync", daemon=True).start()
            else:
                return False
            if cid is not None:
                self.store.mark_command(cid)
            self.enforce_now()
            self._wake.set()
            return True

    def flush_all(self):
        with self.lock:
            self._flush_app()
            self._flush_domains()
            self.acc.flush()
            self.clock.persist(self.store)

    # ================================================================= học-để-chơi
    def ltp_available(self, day) -> float:
        l = self.policy.ltp if self.policy else {}
        if not l.get("enabled"):
            return 0.0
        earned = min(self.acc.get(day, "g:education") * float(l.get("earn_ratio", 0.25)), float(l.get("daily_cap_min", 30)) * 60)
        return max(0.0, earned - self.acc.get(day, "ltp_spent"))

    def exchange(self, minutes: int, target_group: str | None = None):
        with self.lock:
            _, day = self._parts()
            if not (self.policy and self.policy.ltp.get("enabled")):
                return 403, {"error": "Bố mẹ chưa bật tính năng đổi giờ học lấy giờ chơi."}
            avail = self.ltp_available(day)
            want = minutes * 60
            if minutes <= 0 or want > avail + 1e-6:
                return 409, {"error": f"Em mới tích lũy được {int(avail // 60)} phút để đổi."}
            self.acc.add(day, "ltp_spent", want)
            self.acc.add(day, "bonus", want)

            # Xác định đúng nhóm giải trí cần cộng bù thay vì cộng tràn tất cả các nhóm
            budgets = (self.policy.group_budget or {}) if self.policy else {}
            if target_group and target_group in budgets:
                chosen = [target_group]
            else:
                exhausted = [g for g in budgets if self.group_exhausted(g)]
                chosen = exhausted if exhausted else (["game"] if "game" in budgets else list(budgets.keys())[:1])
            for grp in chosen:
                self.acc.add(day, f"gbonus:{grp}", want)

            if want > 0:
                self.store.kv_set("manual_lock_until", "0")
                cur_ov = float(self.store.kv_get("unlock_override_until", "0") or 0)
                new_ov = self.clock.now() + want
                if new_ov > cur_ov:
                    self.store.kv_set("unlock_override_until", f"{new_ov:.0f}")
                self.enforcer.reset("ok")
            self.event("override_granted", "learn_to_play", want)
            self.enforce_now()
            return 200, {"ok": True, "minutes": minutes, "unlocked": self.decision.state != "locked", "groups": chosen}

    # ================================================================= đồng bộ máy chủ
    def snapshot(self) -> dict:
        with self.lock:
            _, day = self._parts()
            d = self.decision
            rem = d.remaining_sec if math.isfinite(d.remaining_sec) else 10_000_000
            return {"policy_version": self.policy.version if self.policy else 0, "day": day,
                    "used_today_sec": int(self.acc.get(day, "total")), "remaining_sec": int(max(-86400, min(rem, 10_000_000))),
                    "group_usage": {k[2:]: int(v) for k, v in self.acc.counters_view(day) if k.startswith("g:")},
                    "state": "suspended" if self.suspended else d.state, "ui_ok": not self.suspended, "agent_version": VERSION}

    def sync_once(self):
        if not self.server or not self.enrolled:
            return
        try:
            self.flush_all()
            hb = self.server.heartbeat(self.snapshot())
            self.online = True
            self.clock.sync_server(hb["server_time"])
            self.store.kv_set("last_sync", f"{self.clock.now():.0f}")
            if hb["policy_version"] != (self.policy.version if self.policy else 0):
                self.apply_envelope(self.server.get_policy())
            for c in hb.get("commands", []):
                if self.handle_command(c) and c.get("id") is not None:
                    try:
                        self.server.ack(c["id"])
                    except Offline:
                        pass
            self._flush_outbox()
            self._flush_events()
        except Offline:
            self.online = False
        except AuthLost:
            self.unenroll()
        except Exception:
            log.exception("sync failed")

    def _flush_events(self):
        while True:
            batch = self.store.pending_events(200)
            if not batch:
                return
            self.server.post_events(batch)
            self.store.delete_events([e["idem_key"] for e in batch])

    def _flush_outbox(self):
        box = json.loads(self.store.kv_get("outbox", "[]"))
        left = []
        for item in box:
            try:
                self.server.post_request(item["kind"], item["body"])
            except RuntimeError:
                continue
            except Offline:
                left.append(item)
        self.store.kv_set("outbox", json.dumps(left))

    def unenroll(self):
        with self.lock:
            if not self.enrolled:
                return
            self.enrolled, self.policy = False, None
            self.store.kv_set("policy_env", "")
            self.store.kv_set("manual_lock_until", "0")
            self.store.kv_set("unlock_override_until", "0")
            self.store.reset_commands()
            self.notify("unenrolled", "Máy không còn được quản lý", "Bố mẹ đã gỡ thiết bị này. Open Guardian Kids ngừng áp dụng luật.")
        self._restore_system()

    # ================================================================= IPC với Tray UI
    def state_view(self) -> dict:
        with self.lock:
            dt_local, day = self._parts()
            d, p = self.decision, self.policy
            total = self.acc.get(day, "total")
            edu = self.acc.get(day, "g:education")
            groups = {}
            if p:
                for g, b in p.group_budget.items():
                    groups[g] = {"label": CATEGORIES.get(g, g), "used_sec": int(self.acc.get(day, f"g:{g}")), "budget_sec": int(b + self._group_bonus_sec(day, g))}
            mood = "sleepy" if self._cont >= EYE_BREAK else ("happy" if total > 0 and edu / total >= 0.5 else "ok")
            return {"child": self.cfg.child_name, "enrolled": self.enrolled, "applies": self.applies, "state": "suspended" if self.suspended else d.state, "reason": d.reason,
                    "lock_text": self._lock_text(d.reason) if d.state == "locked" else None,
                    "manual_until": int(float(self.store.kv_get("manual_lock_until", "0") or 0)) or None,
                    "remaining_sec": int(d.remaining_sec) if math.isfinite(d.remaining_sec) else None, "limit_sec": int(d.limit_sec),
                    "used_sec": int(total), "grace_left": None if d.grace_left is None else int(d.grace_left), "online": self.online,
                    "policy_version": p.version if p else 0, "stale": self.is_stale(), "groups": groups,
                    "garden": {"mood": mood, "education_sec": int(edu), "continuous_sec": int(self._cont), "eye_break_due": self._cont >= EYE_BREAK},
                    "ltp": {"enabled": bool(p and p.ltp.get("enabled")), "available_min": int(self.ltp_available(day) // 60)}}

    def today_view(self) -> dict:
        with self.lock:
            _, day = self._parts()
            c = self.acc.counters_view(day)
            top_apps = sorted(
                ((friendly_app_name(k[4:]), int(v)) for k, v in c if k.startswith("app:") and v >= 1),
                key=lambda x: -x[1],
            )[:10]
            top_doms = sorted(
                ((k[4:], int(v)) for k, v in c if k.startswith("dom:") and v >= 1),
                key=lambda x: -x[1],
            )[:10]
            return {"day": day, "apps": top_apps, "domains": top_doms, "total_sec": int(self.acc.get(day, "total"))}

    def send_request(self, kind: str, subject: str, minutes: int, message: str):
        if kind not in ("add-time", "false-block"):
            return 404, {"error": "Loại yêu cầu không hợp lệ."}
        subject = (subject or "").strip()
        if subject:
            subject = (urlparse(subject if "://" in subject else "//" + subject).hostname or "").lower() or subject.split("/")[0]
            if not is_valid_subject(subject):
                return 422, {"error": "Chỉ nhập tên miền hoặc tên ứng dụng."}
        if kind == "false-block" and not subject:
            return 422, {"error": "Em cho bố mẹ biết trang hoặc ứng dụng nào bị chặn nhầm nhé."}
        body = {"subject": subject, "minutes": max(0, min(int(minutes or 0), 240)), "message": (message or "")[:500]}
        self.event("unlock_request", subject or kind)
        try:
            self.server.post_request(kind, body)
            return 200, {"ok": True}
        except Offline:
            box = json.loads(self.store.kv_get("outbox", "[]"))
            box.append({"kind": kind, "body": body})
            self.store.kv_set("outbox", json.dumps(box))
            return 202, {"ok": True, "queued": True, "message": "Chưa có mạng. Yêu cầu sẽ được gửi khi có kết nối."}
        except RuntimeError as e:
            return 429, {"error": str(e)}
        except AttributeError:
            return 503, {"error": "Máy chưa được ghép đôi."}

    def ipc_dispatch(self, method, path, q, body):
        if method == "GET" and path == "/v1/state":
            return 200, self.state_view()
        if method == "GET" and path == "/v1/today":
            return 200, self.today_view()
        if method == "GET" and path == "/v1/notifications":
            after = int(q.get("after", 0))
            with self.lock:
                return 200, {"items": [n for n in self.notes if n["id"] > after]}
        if method == "POST" and path == "/v1/telemetry":
            with self.lock:
                self.telemetry = {"ts": self.mono(), "idle": float(body.get("idle", 0)), "locked": bool(body.get("locked", False)),
                                  "fg": (body.get("fg") or None)}
            return 200, {"ok": True}
        if method == "POST" and path == "/v1/request":
            return self.send_request(body.get("kind", ""), body.get("subject", ""), body.get("minutes", 0), body.get("message", ""))
        if method == "POST" and path == "/v1/exchange":
            return self.exchange(int(body.get("minutes", 0)), body.get("group"))
        if method == "GET" and path in ("/v1/requests", "/v1/audit"):
            try:
                return 200, {"items": self.server.list_requests() if path.endswith("requests") else self.server.audit()}
            except Offline:
                return 503, {"error": "Chưa kết nối được máy chủ."}
            except AttributeError:
                return 503, {"error": "Máy chưa được ghép đôi."}
        if method == "POST" and path == "/v1/parent-stop":
            return self.parent_stop_agent(
                pin=(body.get("pin") or "").strip(),
                login=(body.get("login") or "").strip(),
                password=(body.get("password") or ""),
            )
        return 404, {"error": "not found"}

    def _verify_parent_on_server(self, login: str, password: str) -> tuple[bool, str]:
        if not self.server or not self.cfg.server_url:
            return False, "Chưa cấu hình địa chỉ máy chủ của bố mẹ."
        try:
            r = self.server._send(
                "POST",
                "/api/auth/login",
                json={"login": login, "password": password, "token": True},
                auth=False,
            )
            if r.status_code == 200:
                data = r.json()
                role = (data.get("user") or {}).get("role", "")
                if role in ("parent", "admin"):
                    return True, ""
                return False, "Tài khoản này không phải tài khoản phụ huynh."
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = None
            return False, detail or "Tài khoản hoặc mật khẩu bố mẹ không đúng."
        except Offline:
            return False, "Không kết nối được máy chủ bố mẹ (mất mạng). Hãy dùng mã PIN phụ huynh đã cài đặt."
        except Exception as e:
            return False, f"Lỗi xác thực máy chủ: {e}"

    def parent_stop_agent(self, pin: str = "", login: str = "", password: str = ""):
        """Xác thực phụ huynh (qua tài khoản Server hoặc mã PIN cục bộ) rồi tắt hoàn toàn OGK trên máy con."""
        if not self._rate_ok("parent_stop_try", 1.5):
            return 429, {"error": "Thao tác quá nhanh, vui lòng đợi 2 giây."}
        fresh_cfg = AgentConfig.load(self.cfg.home)
        if fresh_cfg.parent_pin_hash:
            self.cfg.parent_pin_hash = fresh_cfg.parent_pin_hash

        verified = False
        if login and password:
            ok, err = self._verify_parent_on_server(login, password)
            if not ok:
                return 401, {"error": err}
            verified = True
        elif pin:
            from .config import verify_pin
            if not self.cfg.parent_pin_hash:
                return 400, {"error": "Máy này chưa đặt mã PIN phụ huynh. Vui lòng chuyển sang tab 'Tài khoản bố mẹ' để đăng nhập."}
            if not verify_pin(pin, self.cfg.parent_pin_hash):
                return 401, {"error": "Mã PIN phụ huynh không đúng."}
            verified = True
        else:
            return 422, {"error": "Vui lòng nhập mã PIN phụ huynh hoặc đăng nhập tài khoản bố mẹ."}

        if not verified:
            return 401, {"error": "Xác thực phụ huynh thất bại."}

        log.info("Parent authenticated stop requested on child machine.")
        self.event("override_granted", "parent_local_stop", 0)
        self.flush_all()

        def _shutdown_all():
            import os, signal, subprocess, sys
            time.sleep(0.5)
            try:
                self._restore_system()
            except Exception:
                pass
            if sys.platform == "win32":
                try:
                    subprocess.run(["sc.exe", "stop", "OpenGuardKids"], capture_output=True, creationflags=0x08000000, timeout=10)
                except Exception:
                    pass
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/IM", "ogk.exe", "/FI", f"PID ne {os.getpid()}"],
                        capture_output=True,
                        creationflags=0x08000000,
                        timeout=8,
                    )
                except Exception:
                    pass
            try:
                self.stop()
            except Exception:
                pass
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                os._exit(0)

        threading.Thread(target=_shutdown_all, name="ogk-parent-stop", daemon=True).start()
        return 200, {"ok": True, "message": "Đã xác thực phụ huynh. Đang tắt Open Guardian Kids và khôi phục mạng..."}

    # ================================================================= vòng đời
    def _restore_system(self):
        try:
            self.platform.restore_dns(self._dns_state)
            self.platform.set_browser_doh_policy(False)
            self.platform.set_taskmgr_policy(False)
            self.platform.flush_dns()
        except Exception:
            log.exception("restore system settings failed")

    def start(self):
        self.platform.secure_dir(self.cfg.home, users_read=True)
        self.platform.secure_dir(self.cfg.secrets_path.parent, users_read=False)
        self.notify("started", "Open Guardian Kids đang chạy", "Ứng dụng giúp em dùng máy lành mạnh. Chỉ ghi thời lượng và tên ứng dụng/tên miền, không xem nội dung.")
        self.load_cached_policy()
        token = load_or_create_token(self.cfg.ipc_token_path, self.platform)
        self.ipc = IpcServer(self.ipc_dispatch, token, self.cfg.ipc_host, self.cfg.ipc_port)
        self.ipc.start()
        if self.cfg.enable_dns:
            try:
                self.dns.start()
                if self.cfg.manage_system_dns and self.cfg.dns_host == "127.0.0.1":
                    self._dns6 = DnsProxy(self.domain_filter, self.cfg.upstream_dns, "::1", self.cfg.dns_port, lambda: self.policy,
                                          self._on_dns_block, self._on_dns_query)
                    try:
                        self._dns6.start()
                    except OSError:
                        self._dns6 = None
                if self.cfg.manage_system_dns:
                    self._dns_state.parent.mkdir(parents=True, exist_ok=True)
                    self.platform.set_dns_loopback(self._dns_state)
                    self.platform.set_browser_doh_policy(True)
                    self.platform.flush_dns()
            except OSError as e:
                log.error("DNS proxy không khởi động được: %s", e)
                self.notify("error", "Bộ lọc web chưa hoạt động", "Không mở được cổng DNS 53 (có thể đang bị chương trình khác dùng).")
        self._spawn(self._main_loop, "ogk-main")
        if self.server:
            self._spawn(self._sync_loop, "ogk-sync")
            from .sync_client import CommandChannel
            self.ws = CommandChannel(self.server, self.handle_command, on_state=lambda up: self._wake.set() if up else None)
            self.ws.start()

    def _spawn(self, fn, name):
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        self._threads.append(t)

    def _main_loop(self):
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                self.tick()
            except Exception:
                log.exception("tick failed")
            self._stop.wait(max(0.0, self.cfg.tick_interval - (time.monotonic() - t0)))

    def _sync_loop(self):
        while not self._stop.is_set():
            self.sync_once()
            self._wake.wait(self.cfg.heartbeat_interval)
            self._wake.clear()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self.ws:
            self.ws.stop()
        for t in self._threads:
            t.join(timeout=3)
        self.flush_all()
        if self.cfg.enable_dns:
            self.dns.stop()
            if getattr(self, "_dns6", None):
                self._dns6.stop()
        if self.ipc:
            self.ipc.stop()
        self._restore_system()
