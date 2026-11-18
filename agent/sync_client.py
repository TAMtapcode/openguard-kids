"""Kết nối tới OGK-Server: HTTPS (TLS 1.2+) có ghim chứng chỉ, tự làm mới token, WebSocket lệnh khẩn."""
from __future__ import annotations

import hashlib
import json
import ssl
import threading
import time
from urllib.parse import urlparse

import requests
import urllib3
from requests.adapters import HTTPAdapter


class Offline(Exception):
    """Không liên lạc được máy chủ (mất mạng, hết giờ chờ, hoặc chứng chỉ không khớp)."""


class PinMismatch(Offline):
    """Chứng chỉ của máy chủ không khớp mã ghim -> từ chối kết nối (chống Man-In-The-Middle)."""


class AuthLost(Exception):
    """Refresh token bị từ chối: thiết bị đã bị gỡ khỏi quản lý."""


class _PinAdapter(HTTPAdapter):
    def __init__(self, fingerprint: str, **kw):
        self._fp = fingerprint
        super().__init__(**kw)

    def init_poolmanager(self, *a, **kw):
        kw["assert_fingerprint"] = self._fp
        super().init_poolmanager(*a, **kw)


def _norm_fp(fp: str) -> str:
    return fp.replace(":", "").strip().lower()


class ServerClient:
    def __init__(self, base_url, pin_sha256, device_id, refresh_token, on_refresh_token=lambda t: None, timeout=8.0, allow_insecure=False):
        u = urlparse(base_url)
        self.base, self.device_id, self.timeout = base_url.rstrip("/"), device_id, timeout
        self.pin = _norm_fp(pin_sha256 or "")
        self.scheme = u.scheme
        if u.scheme == "https":
            if not self.pin:
                raise ValueError("Thiếu mã ghim chứng chỉ (pin_sha256). Từ chối kết nối HTTPS không ghim.")
        elif not (allow_insecure and u.hostname in ("localhost", "127.0.0.1", "::1")):
            raise ValueError("Chỉ cho phép http:// khi phát triển trên localhost.")
        self.refresh_token, self._on_refresh = refresh_token, on_refresh_token
        self.access_token, self._exp = None, 0.0
        self._lock = threading.Lock()
        self.http = requests.Session()
        self.http.trust_env = False  # không để biến môi trường/proxy hệ thống làm yếu việc ghim chứng chỉ
        if u.scheme == "https":
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # xác thực bằng ghim, không bằng CA
            self.http.verify = False
            self.http.mount("https://", _PinAdapter(self.pin))

    # ------------------------------------------------------------- token
    def _refresh(self):
        r = self._send("POST", "/api/v1/token/refresh", json={"device_id": self.device_id, "refresh_token": self.refresh_token}, auth=False)
        if r.status_code == 401:
            raise AuthLost()
        r.raise_for_status()
        d = r.json()
        self.access_token, self._exp = d["access_token"], time.monotonic() + d["expires_in"] - 30
        self.refresh_token = d["refresh_token"]  # xoay vòng
        self._on_refresh(self.refresh_token)

    def token(self) -> str:
        with self._lock:
            if not self.access_token or time.monotonic() >= self._exp:
                self._refresh()
            return self.access_token

    def _send(self, method, path, auth=True, **kw):
        headers = kw.pop("headers", {})
        if auth:
            headers["Authorization"] = f"Bearer {self.token()}"
        if self.scheme == "https":
            kw["verify"] = False  # xác thực bằng mã ghim (assert_fingerprint), không dựa vào kho CA
        try:
            return self.http.request(method, self.base + path, headers=headers, timeout=self.timeout, **kw)
        except requests.exceptions.SSLError as e:
            raise PinMismatch(str(e))
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            raise Offline(str(e))

    def call(self, method, path, **kw):
        r = self._send(method, path, **kw)
        if r.status_code == 401:
            with self._lock:
                self.access_token = None
            r = self._send(method, path, **kw)
            if r.status_code == 401:
                raise AuthLost()
        return r

    # ------------------------------------------------------------- API
    def heartbeat(self, payload):
        r = self.call("POST", "/api/v1/heartbeat", json=payload); r.raise_for_status(); return r.json()

    def get_policy(self):
        r = self.call("GET", "/api/v1/policy"); r.raise_for_status(); return r.json()

    def post_events(self, events):
        r = self.call("POST", "/api/v1/events/sync", json={"events": events}); r.raise_for_status(); return r.json()

    def post_request(self, kind, body):
        r = self.call("POST", f"/api/v1/requests/{kind}", json=body)
        if r.status_code >= 400:
            try:
                msg = r.json().get("detail")
            except ValueError:
                msg = None
            raise RuntimeError(msg if isinstance(msg, str) else "Không gửi được yêu cầu.")
        return r.json()

    def list_requests(self):
        r = self.call("GET", "/api/v1/requests"); r.raise_for_status(); return r.json()

    def audit(self):
        r = self.call("GET", "/api/v1/audit"); r.raise_for_status(); return r.json()

    def ack(self, cid):
        self.call("POST", f"/api/v1/commands/{cid}/ack")

    def enroll(self, code, fingerprint, device_name, version):
        r = self._send("POST", "/api/v1/enrollment", auth=False, json={"code": code, "fingerprint": fingerprint, "device_name": device_name, "agent_version": version})
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = r.text
            raise RuntimeError(detail if isinstance(detail, str) else str(detail))
        return r.json()


class CommandChannel(threading.Thread):
    """WebSocket thường trực /ws/agent/{device_id}: nhận lệnh khẩn, gửi ack. Tự kết nối lại với backoff."""

    def __init__(self, client: ServerClient, on_command, on_state=lambda up: None):
        super().__init__(name="ogk-ws", daemon=True)
        self.client, self.on_command, self.on_state = client, on_command, on_state
        self._halt = threading.Event()
        self.connected = False

    def stop(self):
        self._halt.set()

    def run(self):
        import websocket
        backoff = 1.0
        while not self._halt.is_set():
            ws = None
            try:
                url = self.client.base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + f"/ws/agent/{self.client.device_id}"
                opts = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False} if url.startswith("wss") else {}
                ws = websocket.create_connection(url, header=[f"Authorization: Bearer {self.client.token()}"], sslopt=opts, timeout=10)
                if url.startswith("wss"):
                    der = ws.sock.getpeercert(binary_form=True)
                    if hashlib.sha256(der).hexdigest() != self.client.pin:
                        raise PinMismatch("pin")
                ws.settimeout(20)
                self.connected, backoff = True, 1.0
                self.on_state(True)
                while not self._halt.is_set():
                    try:
                        msg = json.loads(ws.recv())
                    except websocket.WebSocketTimeoutException:
                        ws.send(json.dumps({"ping": 1}))
                        continue
                    if "action" in msg:
                        try:
                            self.on_command(msg)
                        finally:
                            if msg.get("id") is not None:
                                ws.send(json.dumps({"ack": msg["id"]}))
            except Exception:
                pass
            finally:
                if self.connected:
                    self.on_state(False)
                self.connected = False
                try:
                    ws and ws.close()
                except Exception:
                    pass
            if self._halt.wait(backoff):
                break
            backoff = min(backoff * 2, 30.0)
