import os, sys
os.environ.setdefault("PYTHONUTF8", "1")
for _s in (sys.stdout, sys.stderr):
    if _s and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
"""python -m agent <lệnh>
   enroll  --server URL --pin SHA256 --code ABCD2345   Ghép đôi (chạy bằng quyền Administrator)
   run     [--dev]                                     Chạy ở chế độ console (phát triển / chẩn đoán)
   tray                                                 Mở Companion UI (biểu tượng khay)
   status                                               Xem trạng thái từ Service
   restore                                              Trả lại DNS gốc (khi gỡ cài đặt)
   hash    FILE                                         In SHA-256 của tệp (để thêm vào danh sách chặn)
   service install|start|stop|remove                    Quản lý Windows Service"""
import argparse
import json
import sys
import time
from pathlib import Path

from .bootstrap import build_engine, enroll, setup_logging
from .config import VERSION, AgentConfig
from .process_guard import sha256_file
from .winapi import get_platform

CONSENT = ("Bạn đang cài Open Guardian Kids. Ứng dụng này HIỂN THỊ biểu tượng khay, chỉ ghi thời lượng/tên ứng dụng/tên miền, "
           "không chụp màn hình, không ghi phím, không đọc tin nhắn. Chỉ cài trên máy của con bạn khi có sự đồng ý của phụ huynh.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agent", description="Open Guardian Kids - Agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enroll")
    e.add_argument("--server", required=True)
    e.add_argument("--pin", default="", help="SHA-256 của chứng chỉ máy chủ (ghim chứng chỉ)")
    e.add_argument("--code", required=True)
    e.add_argument("--name")
    e.add_argument("--insecure-http", action="store_true")
    e.add_argument("--home")
    r = sub.add_parser("run")
    r.add_argument("--dev", action="store_true", help="Không đổi DNS hệ thống, DNS cổng 5353, không yêu cầu khay")
    r.add_argument("--home")
    sub.add_parser("tray")
    sub.add_parser("status")
    h = sub.add_parser("hash")
    h.add_argument("file")
    sub.add_parser("restore", help="Trả lại DNS gốc và gỡ chính sách DoH của trình duyệt (dùng khi gỡ cài đặt)")
    sv = sub.add_parser("service")
    sv.add_argument("action", choices=["install", "start", "stop", "remove", "restart"])
    a = ap.parse_args(argv)

    if a.cmd == "hash":
        print(sha256_file(a.file) or "Không đọc được tệp.")
        return 0
    if a.cmd == "tray":
        from .tray_ui import main as tray_main
        tray_main()
        return 0
    if a.cmd == "service":
        from . import service
        service.win32serviceutil.HandleCommandLine(service.OGKService, argv=[sys.argv[0], a.action])
        return 0

    cfg = AgentConfig.load(Path(a.home) if getattr(a, "home", None) else None)
    if a.cmd == "enroll":
        plat = get_platform()
        if not (plat.is_admin() or a.insecure_http or a.home):
            print("Cần chạy bằng quyền Administrator để lưu cấu hình bảo vệ.")
            return 2
        print(CONSENT)
        cfg.allow_insecure = a.insecure_http
        try:
            res = enroll(cfg, a.server, a.pin, a.code, a.name, plat)
        except Exception as ex:
            print(f"Ghép đôi thất bại: {ex}")
            return 1
        print(f"Đã ghép đôi với hồ sơ của {res['child_name']} (device_id={res['device_id']}). Hãy cài và khởi động dịch vụ.")
        return 0
    if a.cmd == "status":
        from .tray_ui import Ipc
        print(json.dumps(Ipc(cfg).call("GET", "/v1/state")[1], indent=2, ensure_ascii=False))
        return 0
    if a.cmd == "restore":
        eng = build_engine(cfg)
        eng._restore_system()
        print("Đã khôi phục cấu hình DNS gốc.")
        return 0
    if a.cmd == "run":
        plat = get_platform()
        mutex_name = f"Local\\OGK_Agent_Run_{abs(hash(str(cfg.home.resolve())))}"
        if plat.acquire_single_instance(mutex_name) is None:
            print("Open Guardian Kids Agent đang chạy rồi, không mở thêm bản trùng lặp.")
            return 0
        if a.dev:
            cfg.manage_system_dns, cfg.dns_port, cfg.require_tray, cfg.allow_insecure, cfg.block_taskmgr = False, 5353, False, True, False
        setup_logging(cfg, console=True)
        eng = build_engine(cfg)
        eng.start()
        print(f"OGK Agent {VERSION} đang chạy (Ctrl+C để dừng). DNS {cfg.dns_host}:{cfg.dns_port}, IPC {cfg.ipc_host}:{cfg.ipc_port}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            eng.stop()
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())