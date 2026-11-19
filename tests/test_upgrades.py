from dnslib import A, DNSRecord, QTYPE, RR

from agent.classifier import DomainFilter
from agent.config import AgentConfig
from agent.dns_proxy import DnsProxy, is_noise_domain, safesearch_target
from agent.engine import Engine
from agent.policy_engine import Policy
from agent.process_guard import ProcessGuard, ProcInfo
from agent.store import AgentStore
from common.schema import friendly_app_name
from server import services
from server.models import Event, now
from server.security import PW_MIN, password_problem
from tests.conftest import bearer
from tests.helpers import FakeClock, FakePlatform, make_doc


def test_password_policy_min_10_chars_f5():
    assert PW_MIN == 10
    assert password_problem("Abc123456") is not None
    assert password_problem("1234567890") is not None
    assert password_problem("MatKhauAnToan2026") is None


def test_friendly_app_names_and_noise_domains():
    assert friendly_app_name("robloxplayerbeta.exe") == "Roblox"
    assert friendly_app_name("chrome.exe") == "Google Chrome"
    assert friendly_app_name("custom_game_client.exe") == "Custom Game Client"
    assert is_noise_domain("ocsp.pki.goog")
    assert is_noise_domain("www.msftconnecttest.com")
    assert not is_noise_domain("khanacademy.org")


def test_dns_blocks_https_svcb_and_uses_cache():
    pol = Policy(make_doc())
    calls = []

    class DummyProxy(DnsProxy):
        def forward(self, data: bytes) -> bytes:
            calls.append(data)
            req = DNSRecord.parse(data)
            r = req.reply()
            r.add_answer(RR(req.q.qname, QTYPE.A, rdata=A("93.184.216.34"), ttl=120))
            return r.pack()

    px = DummyProxy(DomainFilter(lambda: pol), ["127.0.0.1:53"], get_policy=lambda: pol)
    q_https = DNSRecord.question("www.tiktok.com", "HTTPS").pack()
    ans_https = DNSRecord.parse(px.handle(q_https))
    assert ans_https.header.rcode == 0 and len(ans_https.rr) == 0
    assert len(calls) == 0

    q1 = DNSRecord.question("khanacademy.org", "A").pack()
    q2 = DNSRecord.question("khanacademy.org", "A").pack()
    px.handle(q1)
    px.handle(q2)
    assert len(calls) == 1

    px.flush_cache()
    px.handle(q2)
    assert len(calls) == 2
    assert safesearch_target("search.brave.com") == "safesearch.brave.com"


def test_process_guard_sets_friendly_display_name():
    pol = Policy(make_doc(apps={"blocked": [{"name": "robloxplayerbeta.exe", "group": "game"}]}))
    g = ProcessGuard(
        lambda: pol,
        source=lambda: iter([ProcInfo(4321, "robloxplayerbeta.exe", None, 1.0)]),
        killer=lambda pid: True,
    )
    vs = g.scan()
    assert len(vs) == 1
    assert vs[0].display_name == "Roblox"


def test_browser_context_tracks_domain_duration_and_friendly_today_view(tmp_path):
    clk = FakeClock()
    plat = FakePlatform()
    plat.fg = "chrome.exe"
    cfg = AgentConfig(home=tmp_path, require_tray=False, enable_dns=False)
    eng = Engine(cfg, store=AgentStore(tmp_path / "a.db"), platform=plat, mono=clk.mono)
    eng.policy = Policy(make_doc())

    eng.tick()
    eng._on_dns_query("www.khanacademy.org")
    for _ in range(30):
        clk.advance(1.0)
        eng.tick()

    today = eng.today_view()
    assert dict(today["apps"]).get("Google Chrome", 0) >= 30
    assert dict(today["domains"]).get("khanacademy.org", 0) >= 30


def test_server_reports_use_friendly_app_names_without_exe(app, parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    ev = [
        {"idem_key": "friendly-1", "ts": now(), "type": "app_stop", "subject": "robloxplayerbeta.exe", "duration_sec": 600},
        {"idem_key": "friendly-2", "ts": now(), "type": "blocked_app", "subject": "discord.exe"},
    ]
    assert device_client.post("/api/v1/events/sync", json={"events": ev}, headers=bearer(d["access_token"])).status_code == 200
    rep = parent.c.get(f"/api/parent/children/{cid}/report").json()
    assert rep["apps"][0]["subject"] == "Roblox"
    assert rep["blocked"]["top"][0]["subject"] == "Discord"
