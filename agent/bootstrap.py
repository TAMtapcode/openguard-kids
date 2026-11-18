"""Ghép đôi thiết bị, lưu bí mật (DPAPI trên Windows), dựng Engine từ cấu hình đã lưu."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import logging.handlers
import socket
import uuid

from .config import VERSION, AgentConfig
from .engine import Engine
from .sync_client import ServerClient
from .winapi import get_platform


def setup_logging(cfg: AgentConfig, console=False):
    cfg.home.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(cfg.log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger("ogk")
    root.setLevel(logging.INFO)
    root.addHandler(h)
    if console:
        root.addHandler(logging.StreamHandler())


def device_fingerprint() -> str:
    return hashlib.sha256(f"{socket.gethostname()}|{uuid.getnode()}".encode()).hexdigest()


def save_secrets(cfg: AgentConfig, platform, refresh_token: str, policy_key: bytes):
    cfg.secrets_path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({"refresh_token": refresh_token, "policy_key": base64.b64encode(policy_key).decode()}).encode()
    cfg.secrets_path.write_bytes(platform.protect(raw))
    try:
        cfg.secrets_path.chmod(0o600)
    except OSError:
        pass
    platform.secure_dir(cfg.secrets_path.parent, users_read=False)  # chỉ SYSTEM/Administrators


def load_secrets(cfg: AgentConfig, platform) -> dict | None:
    if not cfg.secrets_path.exists():
        return None
    d = json.loads(platform.unprotect(cfg.secrets_path.read_bytes()))
    return {"refresh_token": d["refresh_token"], "policy_key": base64.b64decode(d["policy_key"])}


def enroll(cfg: AgentConfig, server_url: str, pin: str, code: str, device_name: str | None = None, platform=None) -> dict:
    platform = platform or get_platform()
    client = ServerClient(server_url, pin, "", "", allow_insecure=cfg.allow_insecure)
    res = client.enroll(code.strip().upper(), device_fingerprint(), device_name or socket.gethostname(), VERSION)
    cfg.server_url, cfg.pin_sha256, cfg.device_id, cfg.child_name = server_url, pin, res["device_id"], res["child_name"]
    cfg.save()
    save_secrets(cfg, platform, res["refresh_token"], base64.b64decode(res["policy_key"]))
    platform.secure_dir(cfg.home, users_read=True)
    return res


def build_engine(cfg: AgentConfig, platform=None, **kw) -> Engine:
    platform = platform or get_platform()
    sec = load_secrets(cfg, platform) if cfg.device_id else None
    server = None
    if sec:
        server = ServerClient(cfg.server_url, cfg.pin_sha256, cfg.device_id, sec["refresh_token"],
                              on_refresh_token=lambda t: save_secrets(cfg, platform, t, sec["policy_key"]),
                              allow_insecure=cfg.allow_insecure)
    return Engine(cfg, platform=platform, server=server, policy_key=sec["policy_key"] if sec else None, **kw)
