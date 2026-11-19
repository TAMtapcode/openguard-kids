"""Icon khay tối giản cho máy chủ trên máy bố mẹ: chạy ngầm và chỉ hiện icon, không popup/thông tin."""
from __future__ import annotations

import threading
from pathlib import Path

from PIL import Image, ImageDraw
import pystray

from .runner import run


def _make_icon() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill="#25855a")
    draw.polygon([(32, 14), (46, 30), (32, 50), (18, 30)], fill="white")
    return img


def run_with_tray_icon(data_dir: Path, host: str = "0.0.0.0", port: int = 8443, https: bool = True):
    stop_evt = threading.Event()
    srv_thread = threading.Thread(
        target=run,
        args=(data_dir,),
        kwargs={"host": host, "port": port, "https": https, "stop_event": stop_evt},
        daemon=True,
    )
    srv_thread.start()

    icon = None

    def _on_exit(_icon=None, _item=None):
        stop_evt.set()
        if icon is not None:
            icon.stop()

    menu = pystray.Menu(pystray.MenuItem("Tắt", _on_exit))
    icon = pystray.Icon("OGKServer", _make_icon(), "Open Guardian Kids", menu)
    try:
        icon.run()
    finally:
        stop_evt.set()
        srv_thread.join(timeout=5)
