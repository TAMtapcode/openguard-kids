"""Chạy server 'tự lo mọi thứ': tạo thư mục dữ liệu, chứng chỉ TLS, khóa mã hóa, và phát hiện trong LAN."""
from __future__ import annotations

import threading
from pathlib import Path

import uvicorn

from .config import load_settings
from .discovery import DiscoveryResponder
from .main import create_app
from .tls import ensure_cert, local_ips


def run(data_dir: Path, host="0.0.0.0", port=8443, https=True, stop_event: threading.Event | None = None, discovery_port=None):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    fp, ssl_kw = None, {}
    if https:
        cert, key, fp = ensure_cert(data_dir / "certs")
        ssl_kw = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    ip = (local_ips() or ["127.0.0.1"])[0]
    scheme = "https" if https else "http"
    settings = load_settings(data_dir=data_dir, cookie_secure=https, cert_fingerprint=fp, public_url=f"{scheme}://{ip}:{port}")
    app = create_app(settings)
    kw = {} if discovery_port is None else {"port": discovery_port}
    try:
        disc = DiscoveryResponder(port, scheme, **kw)
        disc.start()
    except OSError:
        disc = None  # cổng phát hiện bận: vẫn chạy bình thường, máy con nhập địa chỉ thủ công
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning", **ssl_kw))
    if stop_event is not None:
        threading.Thread(target=lambda: (stop_event.wait(), setattr(server, "should_exit", True)), daemon=True).start()
    try:
        server.run()
    finally:
        if disc:
            disc.stop()
