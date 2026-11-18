import os, sys
try:
    import winreg
    import winshell
    from win32com.client import Dispatch
    
    # 1. Thêm vào Registry để tự động khởi động
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, "OpenGuardKids", 0, winreg.REG_SZ, sys.executable + ' "' + os.path.abspath(__file__) + '"')
    winreg.CloseKey(key)
    
    # 2. Tạo shortcut ngoài Desktop
    desktop = winshell.desktop()
    path = os.path.join(desktop, "Open Guardian Kids.lnk")
    shell = Dispatch('WScript.Shell')
    shortcut = shell.CreateShortCut(path)
    shortcut.Targetpath = sys.executable
    shortcut.Arguments = '"' + os.path.abspath(__file__) + '"'
    shortcut.WorkingDirectory = os.path.dirname(os.path.abspath(__file__))
    shortcut.IconLocation = "shell32.dll, 43"
    shortcut.save()
except ImportError:
    pass
except Exception as e:
    pass

"""Điểm vào DUY NHẤT của chương trình đóng gói (ogk.exe / OGK-Setup.exe).
   (không tham số)      mở trình cài đặt
   uninstall            gỡ cài đặt (tự xin quyền quản trị)
   agent-service        dịch vụ trên máy của con (do Windows khởi động)
   server-service       dịch vụ bảng điều khiển trên máy bố mẹ (do Windows khởi động)
   tray                 biểu tượng khay của con
   agent <lệnh>         dòng lệnh: status | hash <tệp> | restore | enroll ..."""
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
