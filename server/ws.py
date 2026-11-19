"""Quản lý kết nối WebSocket thường trực tới Agent (lệnh khẩn < 5 giây).

Đảm bảo thứ tự: mọi khung gửi tới một thiết bị đi qua MỘT khóa asyncio riêng của thiết bị đó,
nên hai lệnh bấm liên tiếp (Khóa → Mở khóa) không bao giờ bị gửi chồng chéo hay đảo thứ tự
vì chúng được tạo ở hai luồng threadpool khác nhau.
"""
import asyncio

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.conns: dict[str, WebSocket] = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    def _lock(self, device_id: str) -> asyncio.Lock:
        lk = self.locks.get(device_id)
        if lk is None:
            lk = self.locks[device_id] = asyncio.Lock()
        return lk

    async def connect(self, device_id: str, ws: WebSocket):
        self.loop = asyncio.get_running_loop()
        old = self.conns.get(device_id)
        self.conns[device_id] = ws
        if old is not None and old is not ws:
            try:
                await old.close(code=4000)
            except Exception:
                pass

    def disconnect(self, device_id: str, ws: WebSocket):
        if self.conns.get(device_id) is ws:
            self.conns.pop(device_id, None)
            self.locks.pop(device_id, None)

    def is_connected(self, device_id: str) -> bool:
        return device_id in self.conns

    async def send(self, device_id: str, frame: dict) -> bool:
        """Gửi tuần tự, có hạn chót; thất bại thì coi như kết nối đã chết để lệnh đi đường heartbeat."""
        ws = self.conns.get(device_id)
        if ws is None:
            return False
        async with self._lock(device_id):
            if self.conns.get(device_id) is not ws:
                return False
            try:
                await asyncio.wait_for(ws.send_json(frame), timeout=3)
                return True
            except Exception:
                if self.conns.get(device_id) is ws:   # kết nối chết: bỏ ngay để is_connected không báo sai
                    self.conns.pop(device_id, None)
                return False

    def push(self, device_id: str, frame: dict) -> bool:
        """Gọi từ luồng đồng bộ (threadpool). Trả True nếu đã gửi tới Agent."""
        loop = self.loop
        if loop is None or device_id not in self.conns:
            return False
        try:
            return asyncio.run_coroutine_threadsafe(self.send(device_id, frame), loop).result(timeout=5)
        except Exception:
            return False

    def kick(self, device_id: str, frames: list[dict] | None = None) -> None:
        """Gửi nốt các khung cuối (nếu có) rồi đóng kết nối (dùng khi xóa hồ sơ/thiết bị)."""
        loop, ws = self.loop, self.conns.get(device_id)
        if loop is None or ws is None:
            return

        async def _do():
            for f in frames or []:
                await self.send(device_id, f)
            try:
                await ws.close(code=4401)
            except Exception:
                pass
            self.disconnect(device_id, ws)
        try:
            asyncio.run_coroutine_threadsafe(_do(), loop).result(timeout=5)
        except Exception:
            pass
