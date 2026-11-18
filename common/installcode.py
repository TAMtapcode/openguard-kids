"""Mã cài đặt thân thiện: 8 ký tự mã ghép đôi + 8 ký tự xác thực chứng chỉ máy chủ (dạng ABCD-2345-XXXX-XXXX).

8 ký tự cuối là 40 bit đầu của SHA-256 chứng chỉ máy chủ. Nhờ đó máy con chỉ cần nhập MỘT mã, tự lấy chứng chỉ từ
máy chủ, đối chiếu rồi ghim; người nhập không phải sao chép mã ghim 64 ký tự. Kẻ chen giữa phải tạo chứng chỉ trùng
40 bit (~10^12 lần thử) trong vòng 10 phút hiệu lực của mã nên không khả thi."""
from __future__ import annotations

import re

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # bỏ I, O, 0, 1 cho khỏi nhầm


class InstallCodeError(ValueError):
    """Thông điệp luôn là tiếng Việt, hiển thị thẳng cho người dùng."""


def fingerprint_check(fp_hex: str) -> str:
    raw = bytes.fromhex(fp_hex.replace(":", "").strip())[:5]
    n = int.from_bytes(raw, "big")
    return "".join(ALPHABET[(n >> (35 - 5 * i)) & 31] for i in range(8))


def _group(s: str) -> str:
    return "-".join(s[i:i + 4] for i in range(0, len(s), 4))


def make_install_code(enroll_code: str, fp_hex: str | None = None) -> str:
    return _group(enroll_code + (fingerprint_check(fp_hex) if fp_hex else ""))


def parse_install_code(text: str) -> tuple[str, str | None]:
    """Trả (mã ghép đôi 8 ký tự, mã xác thực chứng chỉ hoặc None). Bỏ qua khoảng trắng, dấu gạch, chữ hoa/thường."""
    s = re.sub(r"[\s\-_.]", "", text or "").upper()
    if not s:
        raise InstallCodeError("Bạn chưa nhập mã cài đặt.")
    bad = sorted({c for c in s if c not in ALPHABET})
    if bad:
        raise InstallCodeError(f"Mã có ký tự không hợp lệ: {' '.join(bad)}. Mã chỉ gồm chữ cái (không có I, O) và số từ 2 đến 9.")
    if len(s) == 8:
        return s, None
    if len(s) == 16:
        return s[:8], s[8:]
    raise InstallCodeError(f"Mã cài đặt gồm 16 ký tự (dạng ABCD-2345-XXXX-XXXX), bạn đã nhập {len(s)} ký tự.")
