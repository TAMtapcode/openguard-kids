"""Ký/kiểm tra toàn vẹn chính sách bằng HMAC-SHA256 trên JSON chuẩn hoá."""
import hashlib
import hmac
import json


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign(obj, key: bytes) -> str:
    return hmac.new(key, canonical(obj), hashlib.sha256).hexdigest()


def verify(obj, sig: str, key: bytes) -> bool:
    return hmac.compare_digest(sign(obj, key), sig or "")
