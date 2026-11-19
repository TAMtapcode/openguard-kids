import json
import urllib.request

import pytest

from agent.clock import TrustedClock
from agent.config import AgentConfig
from agent.engine import Engine
from agent.process_guard import ProcInfo
from agent.store import AgentStore
from agent.sync_client import Offline, ServerClient
from common.signing import sign
from tests.helpers import FakeClock, FakePlatform, make_doc

KEY = b"K" * 32
OPEN = [[1] * 48 for _ in range(7)]


def local_engine(tmp_path, fc=None, procs=(), require_tray=False, server=None, killed=None, **updates):
    fc = fc or FakeClock()
    cfg = AgentConfig(home=tmp_path / "ag", device_id="dev-1", child_name="Bin", enable_dns=False, ipc_port=0,
                      manage_system_dns=False, require_tray=require_tray, tick_interval=1.0)
    store = AgentStore(cfg.db_path)
    plat = FakePlatform()
    eng = Engine(cfg, store=store, platform=plat, clock=TrustedClock(store, fc.wall, fc.mono), mono=fc.mono, policy_key=KEY, server=server,
                 process_source=lambda: iter(list(procs)), killer=(lambda pid: killed.append(pid) or True) if killed is not None else None)
    updates.setdefault("schedule", OPEN)
    doc = make_doc(device_id="dev-1", **updates)
    assert eng.apply_envelope({"policy": doc, "sig": sign(doc, KEY)})
    return eng, plat, fc


def ticks(eng, fc, n):
    for _ in range(n):
        fc.advance(1)
        eng.tick()


def test_idle_and_locked_screen_pause_usage(tmp_path):
    eng, plat, fc = local_engine(tmp_path)
    ticks(eng, fc, 100)
    plat.idle = 301.0                                  # không thao tác quá 5 phút
    ticks(eng, fc, 100)
    plat.idle = 0.0
    eng.telemetry = {"ts": fc.mono(), "idle": 0.0, "locked": True, "fg": None}
    eng.cfg.require_tray = True
    ticks(eng, fc, 0)
    plat.session = False                               # không có người dùng đăng nhập
    ticks(eng, fc, 100)
    assert eng.acc.get(eng._parts()[1], "total") == pytest.approx(100, abs=1)


def test_two_independent_group_budgets_education_unlimited_game_45min(tmp_path):
    procs = [ProcInfo(500, "robloxplayerbeta.exe", None, 1)]
    killed = []
    eng, plat, fc = local_engine(tmp_path, procs=[], killed=killed)
    plat.fg = "scratch 3.exe"
    ticks(eng, fc, 600)                                 # 10 phút học: nhóm giáo dục không giới hạn
    plat.fg = "robloxplayerbeta.exe"
    ticks(eng, fc, 2699)
    day = eng._parts()[1]
    assert eng.acc.get(day, "g:education") == pytest.approx(600, abs=1) and eng.acc.get(day, "g:game") == pytest.approx(2699, abs=1)
    assert not eng.group_exhausted("game") and eng.domain_filter.check("roblox.com").blocked is False
    ticks(eng, fc, 2)                                   # chạm 45 phút
    assert eng.group_exhausted("game") and not eng.group_exhausted("education")
    v = eng.domain_filter.check("roblox.com")
    assert v.blocked and v.code == "group_budget" and not eng.domain_filter.check("khanacademy.org").blocked
    eng.guard._source = lambda: iter(procs)
    eng.tick()
    assert killed == [500] and any(n["kind"] == "blocked" and "ngân sách" in n["body"] for n in eng.notes)


def test_learn_to_play_exchange_without_parent_approval(tmp_path):
    eng, plat, fc = local_engine(tmp_path)
    plat.fg = "scratch 3.exe"
    ticks(eng, fc, 1200)                                # 20 phút học × 0,25 = 5 phút thưởng
    assert eng.state_view()["ltp"] == {"enabled": True, "available_min": 5}
    before = eng.decision.limit_sec
    assert eng.exchange(6)[0] == 409
    assert eng.exchange(5) == (200, {"ok": True, "minutes": 5})
    assert eng.decision.limit_sec == before + 300 and eng.exchange(1)[0] == 409
    assert any(e["type"] == "override_granted" and e["subject"] == "learn_to_play" for e in eng.store.pending_events(100))


def test_learn_to_play_respects_daily_cap_and_parent_switch(tmp_path):
    eng, plat, fc = local_engine(tmp_path, learn_to_play={"enabled": True, "earn_ratio": 1.0, "daily_cap_min": 10})
    plat.fg = "scratch 3.exe"
    ticks(eng, fc, 3600)
    assert eng.state_view()["ltp"]["available_min"] == 10
    off, _, fc2 = local_engine(tmp_path / "off", learn_to_play={"enabled": False, "earn_ratio": 1, "daily_cap_min": 10})
    assert off.exchange(5)[0] == 403


def test_tray_missing_relaunch_then_agent_stops_enforcing_and_reports(tmp_path):
    eng, plat, fc = local_engine(tmp_path, require_tray=True, quota={"weekday_min": 1, "weekend_min": 1})
    ticks(eng, fc, 30)
    assert plat.launched >= 1 and not eng.suspended                       # thử bật lại khay
    assert eng.acc.get(eng._parts()[1], "total") >= 29                     # chưa có khay: vẫn tính giờ (không thành cách né giờ)
    ticks(eng, fc, 70)                                                     # quá hạn ân hạn UI (90s)
    assert eng.suspended and eng.snapshot()["state"] == "suspended" and eng.snapshot()["ui_ok"] is False
    assert eng.domain_filter.check("tiktok.com").blocked is False          # tự dừng chặn
    locks = plat.locks
    ticks(eng, fc, 10)
    assert plat.locks == locks
    eng.ipc_dispatch("POST", "/v1/telemetry", {}, {"idle": 0, "locked": False, "fg": "chrome.exe"})   # khay quay lại
    ticks(eng, fc, 1)
    assert not eng.suspended and eng.domain_filter.check("tiktok.com").blocked


def test_smart_failsafe_after_stale_days(tmp_path):
    eng, plat, fc = local_engine(tmp_path)
    eng.store.kv_set("last_sync", f"{eng.clock.now():.0f}")
    assert not eng.is_stale() and not eng.domain_filter.check("youtube.com").blocked
    fc.advance(8 * 86400)                                                  # mất kết nối 8 ngày (> 7)
    assert eng.is_stale()
    assert eng.domain_filter.check("youtube.com").code == "stale" and eng.domain_filter.check("roblox.com").blocked
    assert not eng.domain_filter.check("khanacademy.org").blocked and not eng.domain_filter.check("truong.edu.vn").blocked


def test_manual_lock_survives_restart_and_commands_are_deduplicated(tmp_path):
    eng, plat, fc = local_engine(tmp_path)
    assert eng.handle_command({"id": 7, "action": "ADD_TIME", "minutes": 15})
    assert eng.handle_command({"id": 7, "action": "ADD_TIME", "minutes": 15})   # đến lần 2 (WS + heartbeat) không cộng thêm
    assert eng.acc.get(eng._parts()[1], "bonus") == 900
    eng.handle_command({"id": 8, "action": "LOCK_NOW"})
    assert eng.decision.state == "locked" and plat.locks == 1
    eng2 = Engine(eng.cfg, store=eng.store, platform=FakePlatform(), clock=eng.clock, mono=fc.mono, policy_key=KEY,
                  process_source=lambda: iter(()))
    eng2.load_cached_policy()
    ticks(eng2, fc, 1)
    assert eng2.decision.state == "locked" and eng2.decision.reason == "manual"
    eng2.handle_command({"id": 9, "action": "UNLOCK"})
    assert eng2.decision.state == "ok"


def test_wipe_local_keeps_todays_quota_counter(tmp_path):
    eng, plat, fc = local_engine(tmp_path)
    plat.fg = "chrome.exe"
    ticks(eng, fc, 120)
    eng.event("app_stop", "chrome.exe", 100)
    eng.handle_command({"id": 1, "action": "WIPE_LOCAL"})
    day = eng._parts()[1]
    assert eng.store.queue_size() == 0 and eng.acc.get(day, "total") == pytest.approx(120, abs=1) and eng.acc.get(day, "app:chrome.exe") == 0


def test_eye_break_reminder_after_60_minutes_and_sleepy_garden(tmp_path):
    eng, plat, fc = local_engine(tmp_path, quota={"weekday_min": 600, "weekend_min": 600})
    plat.fg = "chrome.exe"
    ticks(eng, fc, 3601)
    assert any(n["kind"] == "eye_break" for n in eng.notes)
    assert eng.state_view()["garden"]["mood"] == "sleepy" and eng.state_view()["garden"]["eye_break_due"]


def test_domain_and_app_usage_aggregated_without_urls(tmp_path):
    eng, plat, fc = local_engine(tmp_path)
    plat.fg = "chrome.exe"
    for i in range(5):
        fc.advance(20)
        eng.tick()
        eng._on_dns_query("www.khanacademy.org")
    ticks(eng, fc, 70)
    eng.flush_all()
    ev = eng.store.pending_events(200)
    dom = [e for e in ev if e["type"] == "domain_query"]
    assert dom and {e["subject"] for e in dom} == {"khanacademy.org"} and sum(e["duration_sec"] for e in dom) >= 80   # 4 khoảng × 20s
    assert any(e["type"] == "app_stop" and e["subject"] == "chrome.exe" for e in ev)
    assert all("/" not in e["subject"] and "http" not in e["subject"] for e in ev)
    plat.idle = 999                                          # truy vấn nền khi trẻ không thao tác không bị tính
    ticks(eng, fc, 100)
    n = eng._dom_agg.get("windowsupdate.com", [0, 0])[1]
    eng._on_dns_query("windowsupdate.com")
    assert eng._dom_agg.get("windowsupdate.com", [0, 0])[1] == n


# ------------------------------------------------------------------ yêu cầu của trẻ
class CaptureServer:
    def __init__(self, fail=None): self.sent, self.fail = [], fail
    def post_request(self, kind, body):
        if self.fail: raise self.fail
        self.sent.append((kind, body)); return {"ok": True}


def test_false_block_request_strips_url_to_domain_only(tmp_path):
    srv = CaptureServer()
    eng, *_ = local_engine(tmp_path, server=srv)
    assert eng.send_request("false-block", "https://HocVui.example.org/bai-hoc?id=3#x", 0, "Trang học")[0] == 200
    assert srv.sent[0] == ("false-block", {"subject": "hocvui.example.org", "minutes": 0, "message": "Trang học"})
    assert eng.send_request("false-block", "", 0, "")[0] == 422
    assert any(e["type"] == "unlock_request" for e in eng.store.pending_events(10))


def test_request_queued_offline_and_flushed_later(tmp_path):
    eng, *_ = local_engine(tmp_path, server=CaptureServer(fail=Offline("x")))
    code, res = eng.send_request("add-time", "", 20, "Em cần thêm giờ")
    assert code == 202 and res["queued"] and len(json.loads(eng.store.kv_get("outbox"))) == 1
    ok = CaptureServer()
    eng.server = ok
    eng._flush_outbox()
    assert ok.sent[0][0] == "add-time" and json.loads(eng.store.kv_get("outbox")) == []


# ------------------------------------------------------------------ IPC có token
def test_ipc_requires_secret_token_and_serves_state(tmp_path):
    eng, plat, fc = local_engine(tmp_path)
    from agent.ipc import IpcServer, load_or_create_token
    tok = load_or_create_token(eng.cfg.ipc_token_path, plat)
    srv = IpcServer(eng.ipc_dispatch, tok, port=0)
    srv.start()
    try:
        url = f"http://127.0.0.1:{srv.port}/v1/state"
        with pytest.raises(urllib.error.HTTPError) as ei:
            urllib.request.urlopen(urllib.request.Request(url), timeout=3)
        assert ei.value.code == 401
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(urllib.request.Request(url, headers={"X-OGK-Token": "sai"}), timeout=3)
        with urllib.request.urlopen(urllib.request.Request(url, headers={"X-OGK-Token": tok}), timeout=3) as r:
            st = json.loads(r.read())
        assert st["child"] == "Bin" and st["policy_version"] == 1 and st["state"] == "ok"
    finally:
        srv.stop()


# ------------------------------------------------------------------ máy dùng chung: chỉ tài khoản của con bị quản lý
def test_shared_pc_only_childs_windows_account_is_limited(tmp_path):
    procs = [ProcInfo(900, "evil.exe", None, 1)]
    killed = []
    eng, plat, fc = local_engine(tmp_path, procs=procs, killed=killed, quota={"weekday_min": 1, "weekend_min": 1},
                                 apps={"blocked": [{"name": "evil.exe"}]})
    eng.cfg.monitored_users = ["Bin"]
    plat.user = "Mẹ"                                           # bố mẹ đang dùng máy
    ticks(eng, fc, 300)
    day = eng._parts()[1]
    assert eng.acc.get(day, "total") == 0 and plat.locks == 0 and killed == []   # không tính giờ, không khóa, không đóng ứng dụng
    assert eng.domain_filter.check("tiktok.com").blocked is False                 # DNS cũng không chặn tài khoản bố mẹ
    assert eng.state_view()["applies"] is False
    plat.user = "PC-NHA\\bin"                                   # con đăng nhập (so khớp không phân biệt hoa/thường, bỏ tên miền)
    ticks(eng, fc, 61)
    assert eng.state_view()["applies"] is True and eng.acc.get(day, "total") >= 60
    assert killed and eng.domain_filter.check("tiktok.com").blocked
    ticks(eng, fc, 70)
    assert eng.decision.state == "locked" and plat.locks >= 1
    plat.user = "Mẹ"                                            # bố mẹ quay lại: hết khóa ngay, không bị ảnh hưởng
    locks = plat.locks
    ticks(eng, fc, 20)
    assert eng.decision.state == "ok" and plat.locks == locks


def test_manual_lock_expires_by_itself_so_the_pc_never_stays_stuck(tmp_path):
    eng, plat, fc = local_engine(tmp_path)
    eng.handle_command({"id": 1, "action": "LOCK_NOW", "minutes": 30})
    until = float(eng.store.kv_get("manual_lock_until"))
    assert eng.decision.state == "locked" and 29 * 60 <= until - eng.clock.now() <= 30 * 60 + 1
    assert eng.state_view()["lock_text"] and eng.state_view()["manual_until"] == int(until)
    eng.store.kv_set("manual_lock_until", f"{eng.clock.now() - 1:.0f}")         # hết thời hạn khóa
    eng.enforce_now()
    assert eng.decision.state == "ok"
    eng.store.kv_set("manual_lock", "1")                                         # trạng thái kẹt của bản cũ không còn tác dụng
    eng.enforce_now()
    assert eng.decision.state == "ok"
    eng.handle_command({"id": 2, "action": "LOCK_NOW"})
    assert abs(float(eng.store.kv_get("manual_lock_until")) - eng.clock.now() - 3600) <= 1   # mặc định 60 phút


def test_time_behind_the_lock_screen_does_not_consume_quota(tmp_path):
    eng, plat, fc = local_engine(tmp_path)
    plat.fg = "chrome.exe"
    eng.handle_command({"id": 1, "action": "LOCK_NOW", "minutes": 1})
    assert 59 <= float(eng.store.kv_get("manual_lock_until")) - eng.clock.now() <= 61   # khóa ngắn giữ đúng thời lượng bố mẹ chọn
    before = eng.acc.get(eng._parts()[1], "total")
    ticks(eng, fc, 30)
    assert eng.decision.state == "locked" and eng.acc.get(eng._parts()[1], "total") == before


def test_stale_lock_arriving_after_unlock_is_ignored(tmp_path):
    """Lỗi gốc: Khóa(id 5) rồi Mở khóa(id 6) bấm nhanh; lệnh Khóa đến muộn (qua heartbeat) không được khóa lại máy."""
    eng, plat, fc = local_engine(tmp_path)
    assert eng.handle_command({"id": 6, "action": "UNLOCK"})
    assert eng.handle_command({"id": 5, "action": "LOCK_NOW", "minutes": 60})      # lệnh cũ đến muộn
    assert eng.decision.state == "ok" and float(eng.store.kv_get("manual_lock_until", "0")) == 0
    assert eng.handle_command({"id": 7, "action": "LOCK_NOW", "minutes": 60})      # lệnh mới hơn vẫn có tác dụng
    assert eng.decision.state == "locked"
    assert eng.handle_command({"id": 6, "action": "UNLOCK"})                       # trùng id: bỏ qua
    assert eng.decision.state == "locked"


def test_unlock_overrides_quota_lock_and_unknown_command_is_not_acked(tmp_path):
    eng, plat, fc = local_engine(tmp_path)
    eng.acc.add(eng._parts()[1], "total", 10 * 3600)                               # đã quá hạn mức
    eng.enforce_now()
    assert eng.decision.state == "locked" and eng.decision.reason in ("quota", "schedule")
    assert eng.handle_command({"id": 3, "action": "UNLOCK", "minutes": 10})
    assert eng.decision.state == "ok"                                               # "Mở khóa" có tác dụng thật
    assert eng.handle_command({"id": 4, "action": "LOCK_NOW", "minutes": 5})
    assert eng.decision.state == "locked" and eng.decision.reason == "manual"
    assert eng.handle_command({"id": 99, "action": "KHONG_CO"}) is False           # lệnh lạ: không ack
