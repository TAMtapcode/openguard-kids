import math
import os
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime

import pytest
from dnslib import A, DNSRecord, QTYPE, RR

from agent.classifier import DomainFilter
from agent.clock import TrustedClock
from agent.dns_proxy import DnsProxy, safesearch_target
from agent.policy_engine import Enforcer, Policy, UsageAccountant, local_dt
from agent.process_guard import ProcessGuard, sha256_file
from agent.store import AgentStore
from tests.helpers import FakeClock, lines, make_doc


# ------------------------------------------------------------------ lịch & hạn mức
def test_schedule_seconds_until_disallowed():
    p = Policy(make_doc())
    wed_10 = datetime(2026, 11, 18, 10, 15, 0)  # thứ 4, lịch mở 06:00–21:00
    assert p.seconds_until_disallowed(wed_10) == (21 * 3600) - (10 * 3600 + 15 * 60)
    assert p.seconds_until_disallowed(datetime(2026, 11, 18, 22, 0, 0)) == 0
    assert p.seconds_until_disallowed(datetime(2026, 11, 18, 5, 59, 59)) == 0


def test_quota_weekday_vs_weekend():
    p = Policy(make_doc(quota={"weekday_min": 90, "weekend_min": 120}))
    assert p.limit_sec(0) == 5400 and p.limit_sec(5) == 7200 and p.limit_sec(6) == 7200


def test_enforcer_quota_grace_lock_and_warnings():
    clk = FakeClock()
    p = Policy(make_doc(quota={"weekday_min": 3, "weekend_min": 3}))  # D1: hạn mức 3 phút
    enf = Enforcer(clk.mono)
    dt = datetime(2026, 11, 18, 10, 0, 0)
    seen = {"warn": [], "grace": None, "locked": None}
    used = 0.0
    for _ in range(400):
        d = enf.evaluate(p, dt, used, 0, False)
        seen["warn"] += d.warnings
        if d.state == "grace" and seen["grace"] is None:
            seen["grace"] = used
        if d.state == "locked" and seen["locked"] is None:
            seen["locked"] = used
            break
        clk.advance(1)
        used += 1
    assert seen["warn"] == [60]                      # còn 1 phút (10/5 phút đã qua ngay từ đầu)
    assert seen["grace"] == 180                      # hết hạn mức -> ân hạn
    assert seen["locked"] == 240                     # sau 60 giây ân hạn -> khóa


def test_enforcer_warning_thresholds_10_5_1():
    clk = FakeClock()
    p = Policy(make_doc(quota={"weekday_min": 20, "weekend_min": 20}))
    enf, warns, used = Enforcer(clk.mono), [], 0.0
    while used < 1200:
        warns += enf.evaluate(p, datetime(2026, 11, 18, 10, 0), used, 0, False).warnings
        used += 1
    assert warns == [600, 300, 60]


def test_enforcer_add_time_resets_lock_and_manual_lock():
    clk = FakeClock()
    p = Policy(make_doc(quota={"weekday_min": 1, "weekend_min": 1}))
    enf, dt = Enforcer(clk.mono), datetime(2026, 11, 18, 10, 0)
    enf.evaluate(p, dt, 0, 0, False)
    assert enf.evaluate(p, dt, 60, 0, False).state == "grace"
    clk.advance(61)
    assert enf.evaluate(p, dt, 60, 0, False).state == "locked"
    assert enf.evaluate(p, dt, 60, 900, False).state == "ok"        # +15 phút
    assert enf.evaluate(p, dt, 60, 900, True).reason == "manual"   # khóa tức thì từ phụ huynh


def test_schedule_lock_is_immediate_outside_window():
    clk = FakeClock()
    p = Policy(make_doc())
    d = Enforcer(clk.mono).evaluate(p, datetime(2026, 11, 18, 23, 0), 0, 0, False)
    assert d.state == "locked" and d.reason == "schedule"          # ngoài khung giờ: khóa ngay, không ân hạn


# ------------------------------------------------------------------ đếm giờ & chống chỉnh đồng hồ
def test_usage_counts_only_when_active_and_caps_step(tmp_path):
    clk = FakeClock()
    acc = UsageAccountant(AgentStore(tmp_path / "a.db"), clk.mono)
    for _ in range(60):
        clk.advance(1); acc.tick("d", True, {"game"})
    for _ in range(60):
        clk.advance(1); acc.tick("d", False)                         # khóa màn hình/không thao tác: không tính
    clk.advance(3600); acc.tick("d", True)                           # máy ngủ 1 giờ: chỉ tính tối đa 5 giây
    assert acc.get("d", "total") == pytest.approx(65, abs=0.01) and acc.get("d", "g:game") == pytest.approx(60, abs=0.01)


def test_usage_accuracy_within_30s_per_hour(tmp_path):
    clk = FakeClock()
    acc = UsageAccountant(AgentStore(tmp_path / "a.db"), clk.mono)
    for _ in range(3600):
        clk.advance(1.0); acc.tick("d", True)
    assert abs(acc.get("d", "total") - 3600) <= 30


def test_counters_survive_restart(tmp_path):
    clk, st = FakeClock(), AgentStore(tmp_path / "a.db")
    acc = UsageAccountant(st, clk.mono)
    for _ in range(100):
        clk.advance(1); acc.tick("d", True)
    acc.flush()
    assert UsageAccountant(AgentStore(tmp_path / "a.db"), clk.mono).get("d", "total") == pytest.approx(100)


def test_clock_rollback_does_not_change_trusted_time_and_is_reported(tmp_path):
    c = FakeClock()
    tc = TrustedClock(None, wall=c.wall, mono=c.mono)
    t0 = tc.now(); c.advance(600)
    c.w -= 2 * 3600                                                   # lùi đồng hồ Windows 2 giờ
    assert tc.now() == pytest.approx(t0 + 600)                        # giờ tin cậy vẫn đúng
    delta = tc.check_tamper()
    assert delta is not None and delta == pytest.approx(-2 * 3600 - 0, abs=1)
    assert tc.check_tamper() is None                                  # chỉ báo một lần mỗi đợt


def test_clock_rollback_across_reboot_uses_persisted_trusted_time(tmp_path):
    st, c = AgentStore(tmp_path / "a.db"), FakeClock()
    tc = TrustedClock(st, c.wall, c.mono); c.advance(500); tc.persist(st)
    c2 = FakeClock(wall=c.w - 3 * 3600, mono=5.0)                     # khởi động lại sau khi lùi đồng hồ 3 giờ
    tc2 = TrustedClock(st, c2.wall, c2.mono)
    assert tc2.now() >= tc.now() - 1 and tc2.check_tamper() is not None


def test_clock_server_sync():
    c = FakeClock(); tc = TrustedClock(None, c.wall, c.mono)
    tc.sync_server(c.w + 10); assert tc.now() == pytest.approx(c.w + 10)


# ------------------------------------------------------------------ lọc tên miền
def _filter(doc=None, **kw):
    pol = Policy(doc or make_doc())
    return DomainFilter(lambda: pol, **kw), pol


def test_block_set_of_50_and_no_false_positive_on_20_edu():
    doc = make_doc(domains={"blocked_categories": ["social", "game", "entertainment", "inappropriate"],
                            "blocked": [f"blocked-test-{i:02d}.example.net" for i in range(1, 11)]})
    f, _ = _filter(doc)
    block = lines("social_8.txt") + lines("games_7.txt") + lines("doh_5.txt") + lines("simulated_10.txt") + lines("entertainment_10.txt") + lines("inappropriate_10.txt")
    assert len(block) == 50
    blocked = sum(f.check(d).blocked for d in block)
    assert blocked / 50 >= 0.95 and blocked == 50
    assert not any(f.check(d).blocked for d in lines("edu_20.txt"))      # 0 dương tính giả
    assert len(lines("edu_20.txt")) == 20


def test_subdomains_are_matched_but_lookalikes_are_not():
    f, _ = _filter()
    assert f.check("www.facebook.com").blocked and f.check("a.b.c.tiktok.com").blocked
    assert f.check("FACEBOOK.COM.").blocked                         # chuẩn hoá hoa/thường, dấu chấm cuối
    assert not f.check("notfacebook.com").blocked and not f.check("facebook.com.vn.example.org").category == "social"


def test_exempt_list_is_immutable_even_if_parent_blocks_it():
    doc = make_doc(domains={"blocked": ["moet.gov.vn", "tongdai111.vn"], "blocked_categories": ["education", "social"]})
    f, _ = _filter(doc, is_stale=lambda: True)
    for d in ("moet.gov.vn", "truong-abc.edu.vn", "sub.hcm.gov.vn", "tongdai111.vn", "111.vn", "x.vnedu.vn"):
        assert not f.check(d).blocked and f.check(d).code == "exempt"


def test_system_vs_parent_reason_attribution():
    f, _ = _filter()
    v = f.check("tiktok.com")
    assert v.blocked and "Hệ thống" in v.by and "Mạng xã hội" in v.reason
    doc = make_doc(domains={"blocked": ["example.org"]})
    v2 = DomainFilter(lambda: Policy(doc)).check("x.example.org")
    assert v2.blocked and "Phụ huynh (Mẹ Bin)" in v2.by


def test_parent_allow_overrides_category_and_longest_match_wins():
    doc = make_doc(domains={"allowed": ["discord.com"], "blocked": ["bad.discord.com"], "blocked_categories": ["social"]})
    f, _ = _filter(doc)
    assert not f.check("discord.com").blocked and f.check("bad.discord.com").blocked


def test_group_budget_exhaustion_blocks_group_only():
    f, _ = _filter(group_exhausted=lambda g: g == "game")
    assert f.check("roblox.com").code == "group_budget" and not f.check("khanacademy.org").blocked


def test_smart_failsafe_stale_policy():
    stale = True
    f, _ = _filter(is_stale=lambda: stale)
    assert f.check("youtube.com").code == "stale"            # giải trí: fail-closed
    assert f.check("roblox.com").blocked                     # game: fail-closed
    assert not f.check("khanacademy.org").blocked            # giáo dục: fail-open
    assert not f.check("vnexpress.net").blocked              # chưa phân loại: theo chính sách đã lưu


def test_doh_endpoints_blocked():
    f, _ = _filter()
    assert all(f.check(d).code == "doh" for d in lines("doh_5.txt"))


def test_classification_table_has_200_entries_in_6_groups():
    from tests.helpers import CDB
    assert len(CDB.map) >= 200 and set(CDB.map.values()) == {"education", "entertainment", "social", "game", "inappropriate", "uncategorized"}


# ------------------------------------------------------------------ DNS proxy thật qua UDP
class FakeUpstream:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self.sock.bind(("127.0.0.1", 0)); self.sock.settimeout(0.3)
        self.port, self.queries, self._stop = self.sock.getsockname()[1], [], False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self._stop:
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            req = DNSRecord.parse(data); self.queries.append(str(req.q.qname).rstrip("."))
            r = req.reply(); r.add_answer(RR(req.q.qname, QTYPE.A, rdata=A("93.184.216.34"), ttl=60))
            self.sock.sendto(r.pack(), addr)

    def close(self):
        self._stop = True; self.sock.close()


@pytest.fixture
def dns():
    up = FakeUpstream(); pol = Policy(make_doc()); blocks, queries = [], []
    px = DnsProxy(DomainFilter(lambda: pol), [f"127.0.0.1:{up.port}"], port=0, get_policy=lambda: pol,
                  on_block=lambda v, q: blocks.append(q), on_query=queries.append)
    px.start()
    yield px, up, blocks, queries
    px.stop(); up.close()


def ask(px, name, qtype="A"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(3)
    s.sendto(DNSRecord.question(name, qtype).pack(), ("127.0.0.1", px.port))
    r = DNSRecord.parse(s.recv(4096)); s.close(); return r


def test_dns_blocked_returns_0_0_0_0_and_never_reaches_upstream(dns):
    px, up, blocks, _ = dns
    r = ask(px, "www.facebook.com")
    assert [str(x.rdata) for x in r.rr] == ["0.0.0.0"] and up.queries == [] and blocks == ["www.facebook.com"]
    assert [str(x.rdata) for x in ask(px, "tiktok.com", "AAAA").rr] == ["::"]


def test_dns_allowed_is_forwarded_untouched(dns):
    px, up, _, queries = dns
    r = ask(px, "khanacademy.org")
    assert [str(x.rdata) for x in r.rr] == ["93.184.216.34"] and up.queries == ["khanacademy.org"] and queries == ["khanacademy.org"]


def test_dns_safesearch_cname_rewrite(dns):
    px, up, _, _ = dns
    r = ask(px, "www.google.com")
    types = [QTYPE[x.rtype] for x in r.rr]
    assert types[0] == "CNAME" and str(r.rr[0].rdata).rstrip(".") == "forcesafesearch.google.com" and "A" in types
    assert up.queries == ["forcesafesearch.google.com"]
    assert str(ask(px, "www.bing.com").rr[0].rdata).rstrip(".") == "strict.bing.com"
    assert safesearch_target("google.com.vn") == "forcesafesearch.google.com" and safesearch_target("example.com") is None


def test_dns_exempt_domain_never_blocked_and_servfail_when_upstream_down():
    pol = Policy(make_doc(domains={"blocked": ["x.edu.vn"]}))
    px = DnsProxy(DomainFilter(lambda: pol), ["127.0.0.1:1"], port=0, get_policy=lambda: pol, timeout=0.3)
    px.start()
    try:
        assert ask(px, "x.edu.vn").header.rcode == 2   # không bị chặn (miễn chặn) -> chuyển tiếp; upstream chết -> SERVFAIL
    finally:
        px.stop()


# ------------------------------------------------------------------ giám sát tiến trình (D2) - tiến trình thật
@pytest.mark.skipif(not shutil.which("sleep"), reason="cần binary sleep")
def test_renamed_binary_is_detected_by_sha256_and_terminated(tmp_path):
    exe = tmp_path / "winword.exe"                                   # tệp bị đổi tên giả làm Word
    shutil.copy(shutil.which("sleep"), exe)
    digest = sha256_file(str(exe))
    doc = make_doc(apps={"blocked": [{"name": "game.exe", "sha256": digest, "group": "game"}]})
    pol = Policy(doc)
    proc = subprocess.Popen([str(exe), "60"])
    try:
        time.sleep(0.2)
        g = ProcessGuard(lambda: pol)
        vs = [v for v in g.scan() if v.pid == proc.pid]
        assert len(vs) == 1 and vs[0].reason == "blocklist" and vs[0].sha256 == digest and vs[0].killed
        assert proc.wait(timeout=3) is not None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_guard_name_match_allowlist_group_budget_and_protected():
    from agent.process_guard import ProcInfo
    procs = [ProcInfo(100, "robloxplayerbeta.exe", None, 1), ProcInfo(101, "notepad.exe", None, 2),
             ProcInfo(102, "explorer.exe", None, 3), ProcInfo(103, "evil.exe", None, 4)]
    killed = []
    pol = Policy(make_doc(apps={"blocked": [{"name": "evil.exe"}]}))
    g = ProcessGuard(lambda: pol, group_exhausted=lambda x: x == "game", source=lambda: iter(procs), killer=lambda pid: killed.append(pid) or True)
    vs = {v.name: v.reason for v in g.scan()}
    assert vs == {"robloxplayerbeta.exe": "group_budget", "evil.exe": "blocklist"} and sorted(killed) == [100, 103]
    assert g.scan() == []                                            # không báo lại cùng một tiến trình
    allow = Policy(make_doc(apps={"mode": "allowlist", "allowed": [{"name": "notepad.exe"}]}))
    g2 = ProcessGuard(lambda: allow, source=lambda: iter(procs), killer=lambda pid: True)
    assert {v.name for v in g2.scan()} == {"robloxplayerbeta.exe", "evil.exe"}   # explorer được bảo vệ, notepad được phép


def test_guard_does_nothing_when_suspended_or_no_policy():
    from agent.process_guard import ProcInfo
    src = lambda: iter([ProcInfo(100, "evil.exe", None, 1)])
    assert ProcessGuard(lambda: None, source=src).scan() == []
    pol = Policy(make_doc(apps={"blocked": [{"name": "evil.exe"}]}))
    assert ProcessGuard(lambda: pol, is_suspended=lambda: True, source=src).scan() == []


# ------------------------------------------------------------------ kho cục bộ / hàng đợi
def test_offline_queue_idempotency_keys_and_wipe(tmp_path):
    st = AgentStore(tmp_path / "a.db")
    k1 = st.add_event("app_stop", "a.exe", 10, 1, 1000); k2 = st.add_event("app_stop", "b.exe", 20, 1, 1001)
    assert k1 != k2 and len(k1) == 36 and [e["subject"] for e in st.pending_events()] == ["a.exe", "b.exe"]
    st.delete_events([k1]); assert st.queue_size() == 1
    st.set_counters("2026-11-17", {"total": 500, "app:x.exe": 30, "g:game": 10, "bonus": 900})
    st.set_counters("2026-11-10", {"total": 100})
    st.wipe_activity("2026-11-17")
    assert st.queue_size() == 0 and st.counters("2026-11-10") == {} and st.counters("2026-11-17") == {"total": 500, "g:game": 10, "bonus": 900}
