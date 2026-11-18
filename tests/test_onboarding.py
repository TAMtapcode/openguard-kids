import socket
import threading
import time
from pathlib import Path

import pytest
import requests

from common.installcode import InstallCodeError, fingerprint_check, make_install_code, parse_install_code
from installer.pairing import PairingError, authenticate_server, discover, fetch_fingerprint, find_server, normalize_server
from server.config import load_settings
from server.discovery import DiscoveryResponder
from server.runner import run
from server.security import ENROLL_ALPHABET
from server.tls import ensure_cert, fingerprint_of_pem
from tests.conftest import Parent
from tests.live import LiveServer, free_port

FP = "ab" * 32


# ------------------------------------------------------------------ mã cài đặt
def test_install_code_roundtrip_and_format():
    code = make_install_code("ABCD2345", FP)
    assert len(code.replace("-", "")) == 16 and code.count("-") == 3 and code.startswith("ABCD-2345-")
    assert parse_install_code(code) == ("ABCD2345", fingerprint_check(FP))
    assert parse_install_code("  abcd 2345  " + code[10:].lower()) == ("ABCD2345", fingerprint_check(FP))   # chịu được chữ thường/khoảng trắng
    assert parse_install_code("abcd2345") == ("ABCD2345", None)
    assert set(fingerprint_check(FP)) <= set(ENROLL_ALPHABET)
    assert fingerprint_check("cd" * 32) != fingerprint_check(FP)


@pytest.mark.parametrize("bad,msg", [("", "chưa nhập"), ("ABCD-2345-XXXX-0O1I", "không hợp lệ"), ("ABCD-2345-XX", "16 ký tự")])
def test_install_code_errors_are_friendly_vietnamese(bad, msg):
    with pytest.raises(InstallCodeError, match=msg):
        parse_install_code(bad)


# ------------------------------------------------------------------ chứng chỉ tự động
def test_ensure_cert_creates_once_and_keeps_fingerprint(tmp_path):
    c1, k1, fp1 = ensure_cert(tmp_path / "certs")
    c2, k2, fp2 = ensure_cert(tmp_path / "certs")
    assert fp1 == fp2 == fingerprint_of_pem(c1) and len(fp1) == 64 and k1.exists()      # mã ghim không đổi giữa các lần chạy
    from cryptography import x509
    san = x509.load_pem_x509_certificate(c1.read_bytes()).extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san.get_values_for_type(x509.DNSName) and "127.0.0.1" in [str(i) for i in san.get_values_for_type(x509.IPAddress)]


# ------------------------------------------------------------------ chạy server "tự lo mọi thứ" + tìm trong LAN
def test_runner_boots_https_with_autocert_and_is_discoverable(tmp_path):
    port, dport, stop = free_port(), free_port(), threading.Event()
    t = threading.Thread(target=run, args=(tmp_path / "data",), kwargs=dict(host="127.0.0.1", port=port, stop_event=stop, discovery_port=dport), daemon=True)
    t.start()
    try:
        for _ in range(100):
            try:
                fp = fetch_fingerprint("127.0.0.1", port, timeout=0.5)
                break
            except OSError:
                time.sleep(0.1)
        else:
            pytest.fail("server không lên")
        assert fp == fingerprint_of_pem(tmp_path / "data" / "certs" / "server.pem")
        assert requests.get(f"https://127.0.0.1:{port}/healthz", verify=False, timeout=3).json() == {"ok": True}
        found = discover(timeout=1.5, port=dport, targets=[("127.0.0.1", dport)])
        assert found and found[0]["url"] == f"https://127.0.0.1:{port}"
        # mã cài đặt xác thực đúng máy chủ này
        scheme, host, p = normalize_server(found[0]["url"])
        check = fingerprint_check(fp)
        assert authenticate_server(scheme, host, p, check) == fp
        assert find_server(check, found) == (found[0]["url"], fp)
        with pytest.raises(PairingError, match="không khớp"):
            authenticate_server(scheme, host, p, "AAAAAAAA")                                  # mã sai/máy giả mạo -> từ chối
        assert find_server("AAAAAAAA", found) is None
        with pytest.raises(PairingError, match="thiếu phần xác thực"):
            authenticate_server(scheme, host, p, None)
    finally:
        stop.set()
        t.join(10)


def test_pairing_errors_when_server_unreachable_or_bad_address():
    with pytest.raises(PairingError, match="Không kết nối được"):
        authenticate_server("https", "127.0.0.1", free_port(), "AAAAAAAA")
    with pytest.raises(PairingError, match="chưa nhập"):
        normalize_server("  ")
    assert normalize_server("192.168.1.10") == ("https", "192.168.1.10", 8443)
    assert normalize_server("https://may-bo-me:9000") == ("https", "may-bo-me", 9000)
    with pytest.raises(PairingError, match="https"):
        authenticate_server("http", "192.168.1.10", 8000, None)
    assert discover(timeout=0.4, port=free_port(), targets=[("127.0.0.1", free_port())]) == []


def test_discovery_ignores_unknown_packets():
    r = DiscoveryResponder(8443, port=0, bind="127.0.0.1")
    r.start()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.6)
        s.sendto(b"hello", ("127.0.0.1", r.port))
        with pytest.raises(socket.timeout):
            s.recv(100)
    finally:
        r.stop()


# ------------------------------------------------------------------ API cho phụ huynh/người mới
def _live(tmp_path, **kw):
    s = load_settings(db_url=f"sqlite:///{tmp_path / 's.db'}", master_key=b"k" * 32, cookie_secure=False, data_dir=tmp_path, **kw)
    return LiveServer(s).start()


def test_enroll_code_returns_install_code_with_cert_check(tmp_path):
    live = _live(tmp_path, cert_fingerprint=FP)
    p = Parent(live.app)
    try:
        cid = p.add_child()
        r = p.post(f"/api/parent/children/{cid}/enroll-code?consent=true").json()
        enroll, check = parse_install_code(r["install_code"])
        assert enroll == r["code"] and check == fingerprint_check(FP)
    finally:
        p.c.__exit__(None, None, None)
        live.stop()


def test_local_setup_only_from_loopback_and_only_once(tmp_path):
    live = _live(tmp_path, cert_fingerprint=FP)
    body = {"parent_name": "Mẹ Bin", "email": "me@example.com", "password": "Mat-khau-du-manh-1", "child_name": "Bin", "child_age": 8, "consent": True}
    url = f"{live.url}/api/setup/local"
    try:
        assert requests.post(url, json={**body, "consent": False}, timeout=5).status_code == 422
        assert requests.post(url, json={**body, "password": "ngan"}, timeout=5).status_code == 422
        r = requests.post(url, json=body, timeout=5)
        assert r.status_code == 200 and parse_install_code(r.json()["install_code"])[1] == fingerprint_check(FP)
        assert requests.post(url, json=body, timeout=5).status_code == 409                    # đã thiết lập: đóng cửa
    finally:
        live.stop()


def test_local_setup_rejects_remote_host(tmp_path):
    from fastapi.testclient import TestClient
    s = load_settings(db_url=f"sqlite:///{tmp_path / 's.db'}", master_key=b"k" * 32, cookie_secure=False)
    from server.main import create_app
    with TestClient(create_app(s)) as tc:   # TestClient báo host = "testclient", tức là không phải máy chủ local
        r = tc.post("/api/setup/local", json={"parent_name": "A", "email": "a@b.co", "password": "Mat-khau-du-manh-1", "child_name": "B", "consent": True})
        assert r.status_code == 403


def test_download_route_serves_installer_when_available(tmp_path):
    live = _live(tmp_path)
    try:
        assert requests.get(f"{live.url}/download/OGK-Setup.exe", timeout=5).status_code == 404
        (tmp_path / "downloads").mkdir()
        (tmp_path / "downloads" / "OGK-Setup.exe").write_bytes(b"MZ-fake")
        r = requests.get(f"{live.url}/download/OGK-Setup.exe", timeout=5)
        assert r.status_code == 200 and r.content == b"MZ-fake"
    finally:
        live.stop()
