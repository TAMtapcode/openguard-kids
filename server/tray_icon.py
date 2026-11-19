"""Icon khay cho máy chủ trên máy bố mẹ: chạy server và hiển thị rõ trạng thái minh bạch."""
from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

from PIL import Image, ImageDraw
import pystray

from .runner import run

TRANSPARENCY_NOTE = (
    "Máy này đang chạy Open Guardian Kids Server (bảng điều khiển phụ huynh).\n"
    "• Không áp dụng bộ lọc DNS, khóa ứng dụng hay khóa màn hình trên máy bố mẹ.\n"
    "• Chỉ quản lý các thiết bị của trẻ đã được ghép đôi hợp lệ."
)


def _make_icon() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill="#25855a")
    draw.polygon([(32, 14), (46, 30), (32, 50), (18, 30)], fill="white")
    return img


def _show_transparency_info(icon: pystray.Icon):
    try:
        icon.notify(TRANSPARENCY_NOTE, "Minh bạch — Máy chủ OGK")
    except Exception:
        pass


def run_with_tray_icon(data_dir: Path, host: str = "0.0.0.0", port: int = 8443, https: bool = True):
    stop_evt = threading.Event()
    srv_thread = threading.Thread(
        target=run,
        args=(data_dir,),
        kwargs={"host": host, "port": port, "https": https, "stop_event": stop_evt},
        daemon=True,
    )
    srv_thread.start()

    scheme = "https" if https else "http"
    dashboard_url = f"{scheme}://localhost:{port}"
    icon: pystray.Icon | None = None

    def _open_dashboard(_icon=None, _item=None):
        try:
            webbrowser.open(dashboard_url)
        except Exception:
            pass

    def _on_info(_icon=None, _item=None):
        if icon is not None:
            _show_transparency_info(icon)

    def _on_exit(_icon=None, _item=None):
        stop_evt.set()
        if icon is not None:
            icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Mở bảng điều khiển", _open_dashboard, default=True),
        pystray.MenuItem("Thông tin minh bạch", _on_info),
        pystray.MenuItem("Tắt", _on_exit),
    )
    icon = pystray.Icon("OGKServer", _make_icon(), f"Open Guardian Kids Server ({scheme.upper()} :{port})", menu)
    try:
        icon.run()
    finally:
        stop_evt.set()
        srv_thread.join(timeout=5)
