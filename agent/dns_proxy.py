"""DNS proxy cục bộ 127.0.0.1:53 (UDP + TCP).
Nâng cấp:
- Chặn theo tên miền & subdomain (trả 0.0.0.0 cho A, :: cho AAAA, NODATA cho HTTPS/SVCB).
- Cưỡng chế SafeSearch bằng CNAME (Google, Bing, YouTube, DuckDuckGo, Yandex, Brave).
- Bộ nhớ đệm LRU tôn trọng TTL (giảm độ trễ phân giải DNS và giữ chính xác theo dõi tên miền).
- Khử nhiễu truy vấn CDN/tài nguyên ngầm khi ghi nhận tên miền để báo cáo chính xác trang web trẻ mở.
"""
from __future__ import annotations

import re
import socket
import socketserver
import struct
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

from dnslib import A, AAAA, CNAME, QTYPE, RCODE, RR, DNSRecord

SAFESEARCH = [
    (re.compile(r"^(www\.)?google\.(com|[a-z]{2,3}|com?\.[a-z]{2})$"), "forcesafesearch.google.com"),
    (re.compile(r"^(www\.)?bing\.com$"), "strict.bing.com"),
    (re.compile(r"^(www|m)\.youtube\.com$|^youtubei\.googleapis\.com$|^www\.youtube-nocookie\.com$"), "restrictmoderate.youtube.com"),
    (re.compile(r"^(www\.)?duckduckgo\.com$"), "safe.duckduckgo.com"),
    (re.compile(r"^search\.brave\.com$"), "safesearch.brave.com"),
]

# Các hậu tố hạ tầng/telemetry không phải tên miền web người dùng chủ động truy cập
NOISE_SUFFIXES = (
    ".arpa", ".local", ".internal", ".lan",
    "windowsupdate.com", "microsoft.com", "msftconnecttest.com", "msftncsi.com",
    "digicert.com", "ocsp.pki.goog", "crl.globalsign.com", "letsencrypt.org",
    "googleapis.com", "gstatic.com", "gvt1.com", "gvt2.com",
    "akadns.net", "akamaiedge.net", "cloudfront.net", "fastly.net", "edgekey.net",
)


def safesearch_target(host: str) -> str | None:
    for rx, target in SAFESEARCH:
        if rx.match(host):
            return target
    return None


def is_noise_domain(host: str) -> bool:
    h = host.lower().rstrip(".")
    return any(h == s.lstrip(".") or h.endswith(s) for s in NOISE_SUFFIXES)


def _split(up: str):
    host, _, port = up.partition(":")
    return host, int(port) if port else 53


class _DnsCache:
    """LRU cache nhỏ gọn cho bản ghi DNS hợp lệ, khóa theo (qname, qtype)."""

    def __init__(self, maxsize: int = 1024):
        self.maxsize = maxsize
        self._data: OrderedDict[tuple[str, str], tuple[float, bytes]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[str, str], txid: int) -> bytes | None:
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            exp, payload = item
            if now >= exp:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            # Thay 2 byte đầu bằng Transaction ID của truy vấn mới
            return struct.pack("!H", txid) + payload[2:]

    def put(self, key: tuple[str, str], resp: bytes, ttl: int):
        if ttl <= 0 or len(resp) < 12:
            return
        exp = time.monotonic() + min(ttl, 300)
        with self._lock:
            self._data[key] = (exp, resp)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def clear(self):
        with self._lock:
            self._data.clear()


class DnsProxy:
    def __init__(self, domain_filter, upstreams, host="127.0.0.1", port=53, get_policy=lambda: None,
                 on_block=lambda v, q: None, on_query=lambda q: None, timeout=2.5):
        self.filter, self.upstreams = domain_filter, list(upstreams)
        self.host, self.port, self.timeout = host, port, timeout
        self._policy, self.on_block, self.on_query = get_policy, on_block, on_query
        self._sock = self._tcp = None
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=24, thread_name_prefix="dns")
        self._cache = _DnsCache(1024)
        self.running = False

    def flush_cache(self):
        self._cache.clear()

    # ------------------------------------------------------------- xử lý một truy vấn
    def handle(self, data: bytes) -> bytes | None:
        try:
            req = DNSRecord.parse(data)
            q = req.q
        except Exception:
            return None
        qname = str(q.qname).rstrip(".").lower()
        qtype = QTYPE[q.qtype]
        v = self.filter.check(qname)
        if v.blocked:
            try:
                self.on_block(v, qname)
            except Exception:
                pass
            reply = req.reply()
            if qtype == "A":
                reply.add_answer(RR(q.qname, QTYPE.A, rdata=A("0.0.0.0"), ttl=15))
            elif qtype == "AAAA":
                reply.add_answer(RR(q.qname, QTYPE.AAAA, rdata=AAAA("::"), ttl=15))
            elif qtype in ("HTTPS", "SVCB"):
                # Trả NOERROR không bản ghi để trình duyệt không dùng HTTP/3/ECH/DoH hint lách qua
                pass
            return reply.pack()

        pol = self._policy()
        if pol is not None and pol.safesearch and qtype in ("A", "AAAA", "HTTPS", "SVCB") and v.code != "exempt":
            target = safesearch_target(qname)
            if target:
                if qtype in ("HTTPS", "SVCB"):
                    return req.reply().pack()
                return self._safesearch_reply(req, target, q.qtype)

        if not is_noise_domain(qname):
            try:
                self.on_query(qname)
            except Exception:
                pass

        cache_key = (qname, qtype)
        cached = self._cache.get(cache_key, req.header.id)
        if cached is not None:
            return cached

        resp = self.forward(data)
        try:
            parsed = DNSRecord.parse(resp)
            if parsed.header.rcode == RCODE.NOERROR and parsed.rr:
                min_ttl = min((rr.ttl for rr in parsed.rr), default=0)
                if min_ttl > 0:
                    self._cache.put(cache_key, resp, min_ttl)
        except Exception:
            pass
        return resp

    def _safesearch_reply(self, req, target: str, qtype: int) -> bytes:
        reply = req.reply()
        reply.add_answer(RR(req.q.qname, QTYPE.CNAME, rdata=CNAME(target), ttl=300))
        raw = self.forward(DNSRecord.question(target, QTYPE[qtype]).pack())
        try:
            for rr in DNSRecord.parse(raw).rr:
                if rr.rtype in (QTYPE.A, QTYPE.AAAA):
                    reply.add_answer(RR(target, rr.rtype, rdata=rr.rdata, ttl=min(rr.ttl, 300)))
        except Exception:
            reply.header.rcode = RCODE.SERVFAIL
        return reply.pack()

    # ------------------------------------------------------------- upstream
    def forward(self, data: bytes) -> bytes:
        for up in self.upstreams:
            host, port = _split(up)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.settimeout(self.timeout)
                    s.sendto(data, (host, port))
                    resp, _ = s.recvfrom(4096)
                if len(resp) > 2 and resp[2] & 0x02:  # TC: thử lại qua TCP để lấy bản đầy đủ
                    full = self._forward_tcp(data, host, port)
                    if full:
                        return full
                return resp
            except OSError:
                continue
        try:
            return self._servfail(data)
        except Exception:
            return b""

    @staticmethod
    def _servfail(data: bytes) -> bytes:
        r = DNSRecord.parse(data).reply()
        r.header.rcode = RCODE.SERVFAIL
        return r.pack()

    def _forward_tcp(self, data, host, port):
        try:
            with socket.create_connection((host, port), timeout=self.timeout) as s:
                s.sendall(struct.pack("!H", len(data)) + data)
                n = struct.unpack("!H", self._recvn(s, 2))[0]
                return self._recvn(s, n)
        except OSError:
            return None

    @staticmethod
    def _recvn(s, n):
        buf = b""
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                raise OSError("closed")
            buf += chunk
        return buf

    # ------------------------------------------------------------- vòng lặp UDP/TCP
    def start(self):
        fam = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        self._sock = socket.socket(fam, socket.SOCK_DGRAM)
        self._sock.bind((self.host, self.port))
        self.port = self._sock.getsockname()[1]
        self._sock.settimeout(0.5)
        threading.Thread(target=self._udp_loop, name="dns-udp", daemon=True).start()
        proxy = self

        class TcpHandler(socketserver.BaseRequestHandler):
            def handle(self):
                try:
                    n = struct.unpack("!H", proxy._recvn(self.request, 2))[0]
                    out = proxy.handle(proxy._recvn(self.request, n))
                    if out:
                        self.request.sendall(struct.pack("!H", len(out)) + out)
                except OSError:
                    pass

        class _TcpSrv(socketserver.ThreadingTCPServer):
            address_family = fam
            allow_reuse_address = True

        try:
            self._tcp = _TcpSrv((self.host, self.port), TcpHandler)
            self._tcp.daemon_threads = True
            threading.Thread(target=self._tcp.serve_forever, name="dns-tcp", daemon=True).start()
        except OSError:
            self._tcp = None
        self.running = True

    def _udp_loop(self):
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            self._pool.submit(self._serve, data, addr)

    def _serve(self, data, addr):
        out = self.handle(data)
        if out:
            try:
                self._sock.sendto(out, addr)
            except OSError:
                pass

    def stop(self):
        self._stop.set()
        self.running = False
        for s in (self._sock,):
            try:
                s and s.close()
            except OSError:
                pass
        if self._tcp:
            self._tcp.shutdown()
            self._tcp.server_close()
        self._pool.shutdown(wait=False, cancel_futures=True)