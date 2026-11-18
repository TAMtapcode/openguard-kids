import socket
import threading
import time

import uvicorn

from server.main import create_app


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    """Chạy server FastAPI thật bằng uvicorn trong một luồng (HTTP hoặc HTTPS)."""

    def __init__(self, settings, ssl=None, port=None):
        self.settings, self.ssl, self.port = settings, ssl or {}, port or free_port()
        self.app = self.srv = self.thread = None

    @property
    def url(self):
        return f"{'https' if self.ssl else 'http'}://127.0.0.1:{self.port}"

    def start(self):
        self.app = create_app(self.settings)
        self.srv = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error", **self.ssl))
        self.thread = threading.Thread(target=self.srv.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            if self.srv.started:
                return self
            time.sleep(0.1)
        raise RuntimeError("server did not start")

    def stop(self):
        self.srv.should_exit = True
        self.thread.join(10)
