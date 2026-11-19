"""Băm mật khẩu Argon2id, mã hoá AES-GCM, JWT, giới hạn tần suất (F5: mật khẩu tối thiểu 10 ký tự)."""
from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from collections import defaultdict, deque

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_ph = PasswordHasher()  # Argon2id mặc định
_DUMMY = _ph.hash("dummy-password-for-timing")


def hash_password(p: str) -> str:
    return _ph.hash(p)


def verify_password(h: str | None, p: str) -> bool:
    try:
        return _ph.verify(h or _DUMMY, p) and h is not None
    except (VerificationError, InvalidHashError):
        return False


PW_MIN = 10  # F5: Bắt buộc tối thiểu 10 ký tự
_COMMON_PW = {
    "1234567890", "0987654321", "1111111111", "0000000000", "password12", "password123",
    "qwertyuiop", "qwerty1234", "abc1234567", "abcd123456", "1qaz2wsx3e", "iloveyou12",
    "matkhau123", "matkhau1234", "admin12345", "1231231231", "8888888888", "6666666666",
}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,59}$")


def password_problem(p: str) -> str | None:
    if len(p) < PW_MIN:
        return f"Mật khẩu cần tối thiểu {PW_MIN} ký tự."
    if len(p) > 200:
        return "Mật khẩu quá dài (tối đa 200 ký tự)."
    if len(set(p)) < 4 or p.lower() in _COMMON_PW:
        return "Mật khẩu quá dễ đoán, hãy chọn mật khẩu khác."
    return None


def login_problem(login: str) -> str | None:
    """Tên đăng nhập là email hoặc tên ngắn (chữ thường, số, . _ -), đã chuẩn hóa strip().lower()."""
    if _EMAIL_RE.match(login) or _USERNAME_RE.match(login):
        return None
    return "Email hoặc tên đăng nhập không hợp lệ (tên đăng nhập: 3–60 ký tự chữ, số, dấu . _ -)."


class Vault:
    """AES-GCM cho trường nhạy cảm. Khóa nạp lúc khởi động từ biến môi trường/tệp khóa."""
    _aes: AESGCM | None = None

    @classmethod
    def set_key(cls, key: bytes):
        cls._aes = AESGCM(key)

    @classmethod
    def seal(cls, data: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + cls._aes.encrypt(nonce, data, b"ogk")

    @classmethod
    def open(cls, blob: bytes) -> bytes:
        return cls._aes.decrypt(blob[:12], blob[12:], b"ogk")


def sha256hex(s: str | bytes) -> str:
    return hashlib.sha256(s.encode() if isinstance(s, str) else s).hexdigest()


def new_token(n: int = 32) -> str:
    return secrets.token_urlsafe(n)


ENROLL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # bỏ ký tự dễ nhầm (I,O,0,1)


def new_enroll_code() -> str:
    return "".join(secrets.choice(ENROLL_ALPHABET) for _ in range(8))


def make_access_token(device_id: str, secret: bytes, ttl_min: int) -> str:
    now = int(time.time())
    return jwt.encode({"sub": device_id, "typ": "access", "iat": now, "exp": now + ttl_min * 60}, secret, algorithm="HS256")


def decode_access_token(token: str, secret: bytes) -> str | None:
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return claims.get("sub") if claims.get("typ") == "access" else None


class RateLimiter:
    def __init__(self, limit: int, window_sec: int):
        self.limit, self.window = limit, window_sec
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 10000:
                self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v})
            return True
