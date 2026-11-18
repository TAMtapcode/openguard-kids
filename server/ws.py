"""Quản lý kết nối WebSocket thường trực tới Agent (lệnh khẩn < 5 giây)."""
import asyncio

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.conns: dict[str, WebSocket] = {}
        self.loop: asyncio.AbstractEventLoop | None = None

    async def connect(self, device_id: str, ws: WebSocket):
        self.loop = asyncio.get_running_loop()
        old = self.conns.get(device_id)
        self.conns[device_id] = ws
        if old is not None:
            try:
                await old.close(code=4000)
            except Exception:
                pass

    def disconnect(self, device_id: str, ws: WebSocket):
        if self.conns.get(device_id) is ws:
            self.conns.pop(device_id, None)

    def is_connected(self, device_id: str) -> bool:
        return device_id in self.conns

    def push(self, device_id: str, frame: dict) -> bool:
        """Gọi từ luồng đồng bộ (threadpool). Trả True nếu đã gửi tới Agent."""
        ws, loop = self.conns.get(device_id), self.loop
        if ws is None or loop is None:
            return False
        try:
            asyncio.run_coroutine_threadsafe(ws.send_json(frame), loop).result(timeout=3)
            return True
        except Exception:
            return False
