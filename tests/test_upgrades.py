import socket
from dnslib import A, DNSRecord, QTYPE, RR

from agent.classifier import DomainFilter
from agent.dns_proxy import DnsProxy, is_noise_domain, safesearch_target
from agent.policy_engine import Policy
from agent.process_guard import ProcessGuard, ProcInfo
from common.schema import friendly_app_name
from server.security import PW_MIN, password_problem
from tests.helpers import make_doc


def test_password_policy_min_10_chars_f5():
    assert PW_MIN == 10
    assert password_problem("Abc123456") is not None      # 9 ký tự -> từ chối
    assert password_problem("1234567890") is not None     # 10 ký tự phổ biến -> từ chối
    assert password_problem("MatKhauAnToan2026") is None  # >= 10 ký tự hợp lệ


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
    # 1. Truy vấn HTTPS (type 65) tới trang bị chặn -> trả NOERROR rỗng, không forward
    q_https = DNSRecord.question("www.tiktok.com", "HTTPS").pack()
    ans_https = DNSRecord.parse(px.handle(q_https))
    assert ans_https.header.rcode == 0 and len(ans_https.rr) == 0
    assert len(calls) == 0

    # 2. Truy vấn hợp lệ lần 1 -> forward upstream; lần 2 -> lấy từ LRU cache
    q1 = DNSRecord.question("khanacademy.org", "A").pack()
    q2 = DNSRecord.question("khanacademy.org", "A").pack()
    px.handle(q1)
    px.handle(q2)
    assert len(calls) == 1

    # 3. Flush cache -> truy vấn lại gọi upstream
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
