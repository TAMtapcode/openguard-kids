"""Điểm vào DUY NHẤT của chương trình đóng gói (ogk.exe / OGK-Setup.exe).
   (không tham số)      mở trình cài đặt
   uninstall            gỡ cài đặt (tự xin quyền quản trị)
   agent-service        dịch vụ trên máy của con (do Windows khởi động)
   server-service       dịch vụ bảng điều khiển trên máy bố mẹ (do Windows khởi động)
   server-tray          chạy máy chủ ngầm trên máy bố mẹ, chỉ hiện icon khay
   tray                 biểu tượng khay của con
   agent <lệnh>         dòng lệnh: status | hash <tệp> | restore | enroll ..."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def resolve(argv: list[str]) -> tuple[str, list[str]]:
    """Trả (tên lệnh, đối số còn lại). Tách riêng để dễ kiểm thử."""
    if not argv or argv[0].startswith("-"):
        return "setup", argv
    return argv[0], argv[1:]


def main(argv=None) -> int:
    cmd, rest = resolve(list(sys.argv[1:] if argv is None else argv))
    if cmd == "setup":
        from installer.system import System
        if not rest and getattr(sys, "frozen", False) and not System().payload_dir().exists():
            import tkinter
            from tkinter import messagebox
            tkinter.Tk().withdraw()
            messagebox.showinfo("Open Guardian Kids", "Open Guardian Kids đã được cài đặt trên máy này.\n\nĐể gỡ: Settings → Apps → Open Guardian Kids.")
            return 0
        from installer.wizard import main as wizard
        return wizard(rest)
    if cmd == "uninstall":
        from installer.system import ensure_admin_or_relaunch
        if not ensure_admin_or_relaunch(["uninstall"] + rest):
            return 0
        from installer.wizard import main as wizard
        return wizard(["--uninstall"] + rest)
    if cmd == "agent-service":
        from agent.service import run_dispatcher
        run_dispatcher()
        return 0
    if cmd == "server-service":
        from server.service import run_dispatcher
        run_dispatcher()
        return 0
    if cmd == "server-tray":
        from server.tray_icon import run_with_tray_icon
        data_dir = Path(rest[0]) if rest else (Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "OpenGuardKidsServer")
        run_with_tray_icon(data_dir)
        return 0
    if cmd == "tray":
        from agent.tray_ui import main as tray
        tray()
        return 0
    if cmd == "agent":
        from agent.__main__ import main as agent
        return agent(rest)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
