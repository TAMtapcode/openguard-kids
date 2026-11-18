"""Phía máy con: tìm máy của bố mẹ trong mạng, và xác thực nó bằng mã cài đặt (không cần ai sao chép mã ghim)."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import ssl
import time
from urllib.parse import urlparse

from common.installcode import fingerprint_check
from server.discovery import DISCOVERY_PORT, MAGIC

DEFAULT_PORT = 8443


class PairingError(Exception):
    """Thông điệp tiếng Việt, đọc được bởi người không rành kỹ thuật."""


def normalize_server(text: str) -> tuple[str, str, int]:
    t = (text or "").strip()
    if not t:
        raise PairingError("Bạn chưa nhập địa chỉ máy của bố mẹ.")
    if "://" not in t:
        t = "https://" + t
    u = urlparse(t)
    if not u.hostname:
        raise PairingError(f"Địa chỉ “{text}” chưa đúng. Ví dụ đúng: 192.168.1.10 hoặc may-bo-me.")
    return u.scheme, u.hostname, u.port or DEFAULT_PORT


def broadcast_addresses() -> list[str]:
    """Địa chỉ broadcast của từng card mạng: Windows chỉ gửi 255.255.255.255 ra MỘT card (có thể là card ảo VPN/Hyper-V)."""
    try:
        import psutil
        addrs = psutil.net_if_addrs()
    except Exception:
        return []
    out = []
    for nics in addrs.values():
        for a in nics:
            if a.family != socket.AF_INET or not a.netmask:
                continue
            try:
                net = ipaddress.IPv4Network(f"{a.address}/{a.netmask}", strict=False)
            except ValueError:
                continue
            if net.prefixlen < 31 and not net.is_loopback and not net.is_link_local:
                out.append(str(net.broadcast_address))
    return list(dict.fromkeys(out))


def discover(timeout: float = 3.0, port: int = DISCOVERY_PORT, targets=None) -> list[dict]:
    """Hỏi cả mạng LAN: 'có máy chủ OGK nào không?'. Trả danh sách {name, url} (rỗng nếu không thấy)."""
    targets = targets or [(a, port) for a in ("255.255.255.255", *broadcast_addresses(), "127.0.0.1")]
    found: dict[str, dict] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.3)
        end, next_send = time.monotonic() + timeout, 0.0
        while time.monotonic() < end:
            if time.monotonic() >= next_send:          # gửi lại vài lần: gói UDP qua Wi-Fi dễ bị rơi
                for t in targets:
                    try:
                        s.sendto(MAGIC, t)
                    except OSError:
                        pass
                next_send = time.monotonic() + 0.8
            try:
                data, _ = s.recvfrom(1024)
                m = json.loads(data)
                if m.get("app") == "ogk" and m.get("url"):
                    found[m["url"]] = {"name": m.get("name", ""), "url": m["url"]}
            except socket.timeout:
                continue
            except (ValueError, OSError):
                continue
    return list(found.values())


def fetch_fingerprint(host: str, port: int, timeout: float = 5.0) -> str:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE   # chỉ để LẤY chứng chỉ; tin cậy do mã cài đặt quyết định
    with socket.create_connection((host, port), timeout=timeout) as raw, ctx.wrap_socket(raw) as tls:
        return hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()


def authenticate_server(scheme: str, host: str, port: int, check: str | None, allow_insecure: bool = False) -> str | None:
    """Trả mã ghim đầy đủ (SHA-256) của máy chủ nếu nó khớp mã cài đặt; ném PairingError (tiếng Việt) nếu không."""
    if scheme == "http":
        if allow_insecure and host in ("localhost", "127.0.0.1", "::1"):
            return None
        raise PairingError("Máy chủ phải dùng kết nối an toàn (https).")
    if not check:
        raise PairingError("Mã cài đặt thiếu phần xác thực. Hãy dùng đủ 16 ký tự do bảng điều khiển của bố mẹ tạo ra.")
    try:
        fp = fetch_fingerprint(host, port)
    except (OSError, ssl.SSLError):
        raise PairingError(f"Không kết nối được tới máy của bố mẹ ({host}). Hãy kiểm tra: máy bố mẹ đang bật, "
                           "cùng mạng Wi-Fi/dây mạng với máy này, và đã cài phần “Bảng điều khiển”.")
    if fingerprint_check(fp) != check:
        raise PairingError(f"Máy tại {host} không khớp với mã cài đặt này. Hãy tạo mã mới trên bảng điều khiển và nhập lại cẩn thận.")
    return fp


def find_server(check: str | None, candidates: list[dict], allow_insecure: bool = False):
    """Thử lần lượt các máy tìm thấy, chọn máy nào khớp mã. Trả (url, fingerprint); None nếu không máy nào trả lời;
    ném PairingError của máy cuối cùng nếu có máy trả lời nhưng không khớp/không kết nối được (để báo đúng nguyên nhân)."""
    last = None
    for c in candidates:
        try:
            scheme, host, port = normalize_server(c["url"])
            return c["url"], authenticate_server(scheme, host, port, check, allow_insecure)
        except PairingError as e:
            last = e
    if last:
        raise last
    return None
