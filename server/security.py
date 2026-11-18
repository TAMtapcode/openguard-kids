"""Băm mật khẩu Argon2id, mã hoá AES-GCM, JWT, giới hạn tần suất."""
from __future__ import annotations

import hashlib
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


def password_problem(p: str) -> str | None:
    if len(p) < 10:
        return "Mật khẩu cần tối thiểu 10 ký tự."
    if len(set(p)) < 4:
        return "Mật khẩu quá đơn giản, hãy dùng nhiều ký tự khác nhau hơn."
    return None


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
