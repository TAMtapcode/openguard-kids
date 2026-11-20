"""Cấu hình Agent. Thư mục dữ liệu: %ProgramData%\OpenGuardKids (ACL: chỉ SYSTEM/Administrators được ghi)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path

VERSION = "1.0.0"
IPC_PORT = 49152


def default_home() -> Path:
    env = os.environ.get("OGK_HOME")
    if env:
        return Path(env)
    return Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "OpenGuardKids"


def hash_pin(pin: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), 120_000)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    if not pin or not stored or "$" not in stored:
        return False
    parts = stored.split("$")
    if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
        return False
    expected = hash_pin(pin, parts[1])
    return hmac.compare_digest(expected, stored)


@dataclass
class AgentConfig:
    home: Path = field(default_factory=default_home)
    server_url: str = ""
    pin_sha256: str = ""
    device_id: str = ""
    child_name: str = ""
    upstream_dns: list = field(default_factory=lambda: ["1.1.1.1", "8.8.8.8"])
    dns_host: str = "127.0.0.1"
    dns_port: int = 53
    ipc_host: str = "127.0.0.1"
    ipc_port: int = IPC_PORT
    heartbeat_interval: float = 60.0
    tick_interval: float = 1.0
    manage_system_dns: bool = True
    allow_insecure: bool = False      # chỉ cho phát triển/test (http://localhost)
    require_tray: bool = True         # NT1: không có biểu tượng khay -> agent tự dừng chặn
    ui_grace_sec: float = 90.0
    enable_dns: bool = True
    block_taskmgr: bool = True        # Chặn mở Task Manager trên tài khoản con (bỏ qua nếu là Admin hoặc --dev)
    monitored_users: list = field(default_factory=list)  # rỗng = mọi tài khoản Windows trên máy; ngược lại chỉ các tài khoản này
    parent_pin_hash: str = ""         # Mã PIN/mật khẩu cục bộ của bố mẹ để tắt nhanh trên máy con

    @property
    def db_path(self) -> Path: return self.home / "agent.db"
    @property
    def secrets_path(self) -> Path: return self.home / "secrets" / "device.bin"
    @property
    def ipc_token_path(self) -> Path: return self.home / "run" / "ipc.token"
    @property
    def config_path(self) -> Path: return self.home / "config.json"
    @property
    def log_path(self) -> Path: return self.home / "agent.log"

    def save(self):
        self.home.mkdir(parents=True, exist_ok=True)
        d = {k: v for k, v in asdict(self).items() if k != "home"}
        self.config_path.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, home: Path | None = None) -> "AgentConfig":
        cfg = cls(home=home or default_home())
        if cfg.config_path.exists():
            for k, v in json.loads(cfg.config_path.read_text(encoding="utf-8")).items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        return cfg
