"""IPC nội bộ Service <-> Tray UI: HTTP loopback 127.0.0.1:49152 + Local Secret Token (ACL chống giả mạo)."""
from __future__ import annotations

import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def load_or_create_token(path: Path, platform) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    path.write_text(tok)
    platform.secure_dir(path.parent, users_read=True)  # Users chỉ đọc; chỉ SYSTEM/Administrators ghi được -> không giả mạo được
    return tok


class IpcServer:
    def __init__(self, dispatch, token: str, host="127.0.0.1", port=49152):
        self.dispatch, self.token, self.host, self.port = dispatch, token, host, port
        self._httpd = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass

            def _go(self, method):
                if not hmac.compare_digest(self.headers.get("X-OGK-Token", ""), outer.token):
                    return self._send(401, {"error": "token"})
                u = urlparse(self.path)
                body = {}
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    try:
                        body = json.loads(self.rfile.read(min(n, 65536)))
                    except ValueError:
                        return self._send(400, {"error": "json"})
                try:
                    status, data = outer.dispatch(method, u.path, {k: v[0] for k, v in parse_qs(u.query).items()}, body)
                except Exception as e:  # không để lỗi nội bộ làm sập kênh IPC
                    status, data = 500, {"error": str(e)}
                self._send(status, data)

            def _send(self, status, data):
                raw = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self): self._go("GET")
            def do_POST(self): self._go("POST")

        self._H = H

    def start(self):
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._H)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, name="ogk-ipc", daemon=True).start()

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
