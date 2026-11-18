"""Cấu hình server (biến môi trường; khóa không hardcode)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    db_url: str = field(default_factory=lambda: os.getenv("OGK_DB_URL", f"sqlite:///{ROOT / 'data' / 'ogk.db'}"))
    master_key: bytes = b""                       # 32 byte - khóa AES-GCM (OGK_MASTER_KEY, base64)
    cookie_secure: bool = True                    # False chỉ khi chạy HTTP để phát triển
    session_hours: int = 12
    access_ttl_min: int = 15
    enroll_ttl_min: int = 10
    retention_days: int = 90
    audit_retention_days: int = 365
    lockout_attempts: int = 5
    lockout_minutes: int = 15
    trust_proxy: bool = False
    categories_csv: Path = ROOT / "data" / "categories.csv"
    online_window_sec: int = 150
    data_dir: Path | None = None
    cert_fingerprint: str | None = None   # để dựng mã cài đặt thân thiện
    public_url: str | None = None         # địa chỉ LAN hiển thị cho phụ huynh, ví dụ https://192.168.1.10:8443

    @property
    def jwt_secret(self) -> bytes:
        return hmac.new(self.master_key, b"ogk-jwt-v1", hashlib.sha256).digest()


def _load_master_key(default_path: Path | None = None) -> bytes:
    env = os.getenv("OGK_MASTER_KEY")
    if env:
        key = base64.urlsafe_b64decode(env + "=" * (-len(env) % 4))
        if len(key) != 32:
            raise ValueError("OGK_MASTER_KEY phải là 32 byte (base64).")
        return key
    path = Path(os.getenv("OGK_KEY_FILE") or default_path or ROOT / "data" / "master.key")
    if path.exists():
        return base64.urlsafe_b64decode(path.read_text().strip())
    path.parent.mkdir(parents=True, exist_ok=True)
    key = os.urandom(32)
    path.write_text(base64.urlsafe_b64encode(key).decode())
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key


def load_settings(**overrides) -> Settings:
    data_dir = overrides.get("data_dir")
    if data_dir and "db_url" not in overrides:
        overrides["db_url"] = f"sqlite:///{Path(data_dir) / 'ogk.db'}"
    s = Settings(**overrides)
    if not s.master_key:
        s.master_key = _load_master_key(Path(data_dir) / "master.key" if data_dir else None)
    return s
