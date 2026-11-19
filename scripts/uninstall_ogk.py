"""Gỡ bỏ và xóa sạch dữ liệu Open Guardian Kids trên máy Windows (khôi phục DNS, gỡ dịch vụ, xóa tự khởi động và dữ liệu cục bộ)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _run(args: list[str]) -> None:
    try:
        kw = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        subprocess.run(args, capture_output=True, timeout=30, **kw)
    except Exception:
        pass


def clean_uninstall(silent: bool = False) -> None:
    if sys.platform == "win32":
        import winreg
        for hive, name in (
            (winreg.HKEY_CURRENT_USER, "OpenGuardKids"),
            (winreg.HKEY_CURRENT_USER, "OpenGuardKidsTray"),
            (winreg.HKEY_LOCAL_MACHINE, "OpenGuardKids"),
            (winreg.HKEY_LOCAL_MACHINE, "OpenGuardKidsTray"),
        ):
            try:
                with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as k:
                    winreg.DeleteValue(k, name)
            except OSError:
                pass
        try:
            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\OpenGuardKids")
        except OSError:
            pass

    for svc in ("OpenGuardKids", "OpenGuardKidsServer"):
        _run(["sc.exe", "stop", svc])
    try:
        root = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(root))
        from agent.bootstrap import build_engine
        from agent.config import AgentConfig
        build_engine(AgentConfig.load())._restore_system()
    except Exception:
        pass
    for svc in ("OpenGuardKids", "OpenGuardKidsServer"):
        _run(["sc.exe", "delete", svc])
    _run(["ipconfig", "/flushdns"])

    shortcut_candidates = [
        Path(os.environ.get("USERPROFILE", "")) / "Desktop" / "Open Guardian Kids.lnk",
        Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop" / "Open Guardian Kids.lnk",
        Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop" / "Bảng điều khiển OGK.url",
    ]
    for p in shortcut_candidates:
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            pass

    pd = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    ad = Path(os.environ.get("APPDATA", ""))
    data_dirs = [
        pd / "OpenGuardKids",
        pd / "OpenGuardKidsServer",
        pd / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Open Guardian Kids",
        Path.cwd() / "agent-home",
    ]
    if str(ad):
        data_dirs.append(ad / "OpenGuardKids")
    for d in data_dirs:
        try:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass
    try:
        db = Path.cwd() / "ogk.db"
        if db.is_file():
            db.unlink()
    except OSError:
        pass

    if sys.platform == "win32":
        _run(["taskkill", "/F", "/T", "/IM", "ogk.exe"])
        if not silent:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0,
                    "Đã xóa toàn bộ dữ liệu, khôi phục mạng và gỡ cài đặt Open Guardian Kids thành công!",
                    "Open Guardian Kids",
                    0x40,
                )
            except Exception:
                pass


if __name__ == "__main__":
    clean_uninstall(silent="--silent" in sys.argv)
