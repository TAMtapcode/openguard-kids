"""Kịch bản nghiệm thu D1–D5 chạy end-to-end: server FastAPI thật + Agent Engine thật."""
import shutil
import socket
import subprocess
import time

import pytest
from dnslib import DNSRecord
from sqlalchemy import func, select

from agent.bootstrap import enroll, load_secrets
from agent.clock import TrustedClock
from agent.config import AgentConfig
from agent.engine import Engine
from agent.process_guard import sha256_file
from agent.store import AgentStore
from agent.sync_client import AuthLost, Offline, PinMismatch, ServerClient
from agent.winapi import Platform
from scripts.gen_cert import make_cert
from server.config import load_settings
from server.models import Event
from tests.conftest import Parent
from tests.helpers import FakeClock, FakePlatform
from tests.live import LiveServer

OPEN = [[1] * 48 for _ in range(7)]


@pytest.fixture
def world(tmp_path):
    s = load_settings(db_url=f"sqlite:///{tmp_path / 's.db'}", master_key=b"k" * 32, cookie_secure=False)
    live = LiveServer(s).start()
    parent = Parent(live.app)
    cid = parent.add_child("Bin", 9)
    engines = []

    def make_engine(fake_time=None, threads=False, **cfgkw):
        home = tmp_path / f"agent{len(engines)}"
        cfg = AgentConfig(home=home, allow_insecure=True, require_tray=False, enable_dns=True, dns_port=0, ipc_port=0,
                          manage_system_dns=False, heartbeat_interval=30, tick_interval=0.2, upstream_dns=["127.0.0.1:1"], **cfgkw)
        code = parent.post(f"/api/parent/children/{cid}/enroll-code?consent=true").json()["code"]
        plat = FakePlatform()
        enroll(cfg, live.url, "", code, "PC-Bin", plat)
        sec = load_secrets(cfg, plat)
        client = ServerClient(live.url, "", cfg.device_id, sec["refresh_token"], allow_insecure=True, timeout=3)
        store = AgentStore(cfg.db_path)
        kw = {}
        if fake_time:
            kw = {"mono": fake_time.mono, "clock": TrustedClock(store, wall=fake_time.wall, mono=fake_time.mono)}
        eng = Engine(cfg, store=store, platform=plat, server=client, policy_key=sec["policy_key"], **kw)
        engines.append(eng)
        return eng, plat

    yield live, parent, cid, make_engine
    for e in engines:
        try:
            e.stop()
        except Exception:
            pass
    parent.c.__exit__(None, None, None)
    try:
        live.stop()
    except Exception:
        pass


def set_policy(parent, cid, **body):
    assert parent.put(f"/api/parent/children/{cid}/policy", json=body).status_code == 200


def run_ticks(eng, fc, n, step=1.0):
    for _ in range(n):
        fc.advance(step)
        eng.tick()


def notes(eng, kind):
    return [n for n in eng.notes if n["kind"] == kind]


# ======================================================================= D1
def test_D1_quota_3_minutes_warns_then_popup_then_auto_lock(world):
    live, parent, cid, make = world
    fc = FakeClock()
    eng, plat = make(fake_time=fc)
    set_policy(parent, cid, quota={"weekday_min": 3, "weekend_min": 3}, schedule=OPEN)  # đặt hạn mức 3 phút
    eng.sync_once()
    assert eng.policy.weekday_sec == 180
    run_ticks(eng, fc, 179)
    assert eng.decision.state == "ok" and plat.locks == 0
    run_ticks(eng, fc, 1)                                             # chạm 3 phút
    assert eng.decision.state == "grace" and notes(eng, "grace")      # popup hết giờ + ân hạn 60 giây để lưu bài
    assert [n["kind"] for n in eng.notes if n["kind"] == "warning"]   # đã có cảnh báo trước đó (còn 1 phút)
    run_ticks(eng, fc, 59)
    assert eng.decision.state == "grace" and plat.locks == 0
    run_ticks(eng, fc, 2)
    assert eng.decision.state == "locked" and plat.locks >= 1 and notes(eng, "locked")
    assert "111" in notes(eng, "locked")[0]["emergency"]              # NT6: thông tin khẩn cấp luôn hiển thị
    types = {e["type"] for e in eng.store.pending_events(100)}
    assert {"quota_warning", "locked"} <= types
    # trẻ xin thêm giờ -> phụ huynh duyệt -> máy mở lại
    assert eng.send_request("add-time", "", 15, "Em chưa nộp bài")[0] == 200
    rid = parent.c.get(f"/api/parent/children/{cid}/requests").json()[0]["id"]
    parent.post(f"/api/parent/requests/{rid}/decide", json={"approve": True, "minutes": 15})
    eng.sync_once()
    assert eng.decision.state == "ok" and eng.decision.remaining_sec > 800


# ======================================================================= D2
def test_D2_renamed_exe_detected_by_sha256_and_terminated_with_explanation(world, tmp_path):
    from agent.process_guard import ProcInfo, ProcessGuard
    live, parent, cid, make = world
    exe = tmp_path / "winword.exe"
    exe.write_bytes(b"MZ-fake-game-binary-payload-for-d2-test")
    digest = sha256_file(str(exe))
    set_policy(parent, cid, apps={"blocked": [{"name": "game.exe", "sha256": digest, "group": "game"}]})
    eng, plat = make()
    eng.sync_once()
    killed = []
    eng.guard = ProcessGuard(
        lambda: eng.policy,
        eng.group_exhausted,
        lambda: eng.suspended or not eng.applies,
        source=lambda: iter([ProcInfo(8888, "winword.exe", str(exe), 1.0)]),
        killer=lambda pid: killed.append(pid) or True,
    )
    eng.tick()
    assert killed == [8888]                                           # bị kết thúc ngay nhờ khớp SHA-256
    n = notes(eng, "blocked")[0]
    assert n["subject"] == "winword.exe" and n["by"].startswith("Phụ huynh") and n["can_request"]   # có giải thích, có nút xin
    assert "blocked_app" in {e["type"] for e in eng.store.pending_events(100)}


# ======================================================================= D3
def test_D3_windows_clock_back_2h_quota_not_increased_and_event_recorded(world):
    live, parent, cid, make = world
    fc = FakeClock()
    eng, plat = make(fake_time=fc)
    set_policy(parent, cid, quota={"weekday_min": 90, "weekend_min": 90}, schedule=OPEN)
    eng.sync_once()
    run_ticks(eng, fc, 300)
    used_before = eng.acc.get(eng._parts()[1], "total")
    trusted_before = eng.clock.now()
    fc.w -= 2 * 3600                                                  # lùi đồng hồ Windows 2 tiếng
    run_ticks(eng, fc, 60)
    day = eng._parts()[1]
    assert eng.acc.get(day, "total") == pytest.approx(used_before + 60, abs=1)   # vẫn tính đúng 60 giây, không tăng/giảm
    assert eng.clock.now() == pytest.approx(trusted_before + 60, abs=1)
    assert any(e["subject"].startswith("clock_tamper") and e["type"] == "quota_warning" for e in eng.store.pending_events(100))
    assert notes(eng, "clock")                                        # NT4: thông báo minh bạch cho trẻ
    assert eng.decision.remaining_sec == pytest.approx(90 * 60 - used_before - 60, abs=2)


# ======================================================================= D4
def test_D4_offline_rules_still_enforced_and_resync_without_duplicates(world, tmp_path):
    live, parent, cid, make = world
    fc = FakeClock()
    eng, plat = make(fake_time=fc)
    set_policy(parent, cid, quota={"weekday_min": 2, "weekend_min": 2}, schedule=OPEN,
               domains={"blocked": ["chan-thu.example.net"]})
    eng.sync_once()
    eng.dns.start()
    live.stop()                                                       # rút dây mạng: máy chủ biến mất
    eng.sync_once()
    assert eng.online is False
    # luật chặn web vẫn hoạt động offline
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
    s.sendto(DNSRecord.question("www.chan-thu.example.net").pack(), ("127.0.0.1", eng.dns.port))
    assert [str(r.rdata) for r in DNSRecord.parse(s.recv(4096)).rr] == ["0.0.0.0"]
    eng.dns.stop()
    # hạn mức vẫn được cưỡng chế offline
    plat.fg = "winword.exe"
    run_ticks(eng, fc, 125)
    assert eng.decision.state in ("grace", "locked") and eng.decision.reason == "quota"
    run_ticks(eng, fc, 61)
    assert eng.decision.state == "locked" and plat.locks >= 1
    eng.flush_all()
    queued = eng.store.pending_events(500)
    assert len(queued) >= 3 and len({e["idem_key"] for e in queued}) == len(queued)
    # cắm dây lại
    live.start()
    parent2 = Parent(live.app, "p2@example.com")
    try:
        # mô phỏng: máy chủ đã nhận lô nhưng Agent chưa kịp xóa hàng đợi (mất mạng giữa chừng)
        eng.server.http.close()
        eng.server.access_token = None
        res1 = eng.server.post_events(queued)
        assert res1["accepted"] == len(queued)
        eng.sync_once()                                               # đồng bộ lại cùng lô -> phải khử trùng
        assert eng.online and eng.store.queue_size() == 0
        with live.app.state.SessionLocal() as db:
            n = db.scalar(select(func.count()).select_from(Event))
            assert n >= len(queued)
            keys = list(db.scalars(select(Event.idem_key)))
            assert len(keys) == len(set(keys))
        assert eng.server.post_events(queued)["duplicates"] == len(queued)
    finally:
        parent2.c.__exit__(None, None, None)


# ======================================================================= D5
def test_D5_parent_lock_button_locks_child_pc_in_under_5_seconds(world):
    live, parent, cid, make = world
    eng, plat = make()                                                # chạy thật: luồng tick + WebSocket
    set_policy(parent, cid, quota={"weekday_min": 600, "weekend_min": 600}, schedule=OPEN)
    eng.sync_once()
    eng.start()
    try:
        for _ in range(100):
            if eng.ws.connected and eng.policy:
                break
            time.sleep(0.1)
        assert eng.ws.connected and eng.decision.state == "ok" and plat.locks == 0
        t0 = time.monotonic()
        r = parent.post(f"/api/parent/children/{cid}/command", json={"action": "LOCK_NOW"})
        assert r.json()["delivered_now"] == 1                         # đi qua WebSocket
        while plat.locks == 0 and time.monotonic() - t0 < 6:
            time.sleep(0.02)
        latency = time.monotonic() - t0
        assert plat.locks >= 1 and latency < 5.0, latency
        print(f"\nD5 latency = {latency:.2f}s")
        assert eng.decision.reason == "manual"
        parent.post(f"/api/parent/children/{cid}/command", json={"action": "UNLOCK"})
        for _ in range(50):
            if eng.decision.state == "ok":
                break
            time.sleep(0.1)
        assert eng.decision.state == "ok"
    finally:
        eng.stop()


# ======================================================================= thu hồi / ghim chứng chỉ / toàn vẹn
def test_revoked_device_unenrolls_and_restores_system(world):
    live, parent, cid, make = world
    eng, plat = make()
    eng.sync_once()
    parent.post(f"/api/parent/devices/{eng.cfg.device_id}/revoke")
    eng.server.access_token = None
    eng.sync_once()
    assert not eng.enrolled and eng.policy is None and plat.restored >= 1 and notes(eng, "unenrolled")


def test_tampered_or_replayed_policy_rejected(world):
    live, parent, cid, make = world
    eng, _ = make()
    eng.sync_once()
    env = eng.server.get_policy()
    import copy
    bad = copy.deepcopy(env); bad["policy"]["quota"]["weekday_min"] = 9999
    v0 = eng.policy.version
    assert eng.apply_envelope(bad) is False and eng.policy.weekday_sec != 9999 * 60
    set_policy(parent, cid, quota={"weekday_min": 50, "weekend_min": 50})
    eng.sync_once()
    assert eng.policy.version == v0 + 1
    assert eng.apply_envelope(env) is False and eng.policy.version == v0 + 1   # phát lại bản cũ bị từ chối
    other = copy.deepcopy(env); other["policy"]["device_id"] = "khac"
    assert eng.apply_envelope(other) is False


def test_tls_certificate_pinning(tmp_path):
    fp = make_cert(["localhost"], ["127.0.0.1"], tmp_path / "c")
    s = load_settings(db_url=f"sqlite:///{tmp_path / 's.db'}", master_key=b"k" * 32, cookie_secure=True)
    live = LiveServer(s, ssl={"ssl_certfile": str(tmp_path / "c" / "server.pem"), "ssl_keyfile": str(tmp_path / "c" / "server.key")}).start()
    try:
        good = ServerClient(live.url, fp, "x", "y", timeout=3)
        assert good._send("GET", "/healthz", auth=False).json() == {"ok": True}
        with pytest.raises(PinMismatch):
            ServerClient(live.url, "00" * 32, "x", "y", timeout=3)._send("GET", "/healthz", auth=False)   # chứng chỉ không khớp -> từ chối
        with pytest.raises(ValueError):
            ServerClient(live.url, "", "x", "y")                                                          # HTTPS không ghim bị từ chối
        with pytest.raises(ValueError):
            ServerClient("http://10.0.0.5:8000", "", "x", "y")                                            # HTTP ngoài localhost bị từ chối
    finally:
        live.stop()
