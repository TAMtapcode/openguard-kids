"""Đồng hồ tin cậy: neo vào giờ máy chủ, sau đó chỉ tiến theo đồng hồ phần cứng (monotonic).
Chỉnh đồng hồ Windows không ảnh hưởng hạn mức lẫn lịch tuần; độ lệch được phát hiện và ghi nhận."""
from __future__ import annotations

import time

TOLERANCE = 120.0  # giây


class TrustedClock:
    def __init__(self, store=None, wall=time.time, mono=time.monotonic):
        self._wall, self._mono = wall, mono
        last = float(store.kv_get("last_trusted", 0) or 0) if store else 0.0
        w = wall()
        # Sau khi khởi động lại, không bao giờ tin giờ lùi hơn mốc tin cậy đã lưu.
        self._anchor_epoch = max(w, last)
        self._anchor_mono = mono()
        self.rolled_back_across_reboot = last - w > TOLERANCE
        self._reported = 0.0

    def now(self) -> float:
        return self._anchor_epoch + (self._mono() - self._anchor_mono)

    def wall_delta(self) -> float:
        return self._wall() - self.now()

    def check_tamper(self) -> float | None:
        """Trả độ lệch (giây) khi đồng hồ hệ thống lệch quá ngưỡng so với giờ tin cậy; mỗi đợt chỉ báo một lần."""
        if self.rolled_back_across_reboot:
            self.rolled_back_across_reboot = False
            return self.wall_delta()
        d = self.wall_delta()
        if abs(d) <= TOLERANCE:
            self._reported = 0.0
            return None
        if abs(d - self._reported) > TOLERANCE:
            self._reported = d
            return d
        return None

    def sync_server(self, server_epoch: float) -> float:
        diff = server_epoch - self.now()
        self._anchor_epoch, self._anchor_mono = float(server_epoch), self._mono()
        return diff

    def persist(self, store):
        store.kv_set("last_trusted", f"{self.now():.0f}")
