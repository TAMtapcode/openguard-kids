"""Thao tác hệ thống thật của trình cài đặt (Windows). Tách riêng để test bằng hệ thống giả."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


class System:
    def is_admin(self) -> bool:
        from agent.winapi import get_platform
        return get_platform().is_admin()

    def run(self, args: list[str], timeout: int = 90) -> tuple[int, str]:
        try:
            kw = {"creationflags": CREATE_NO_WINDOW} if sys.platform == "win32" else {}
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, errors="replace", **kw)
            return r.returncode, (r.stdout or "") + (r.stderr or "")
        except (OSError, subprocess.TimeoutExpired) as e:
            return 1, str(e)

    # ---- tệp
    def makedirs(self, p: Path): Path(p).mkdir(parents=True, exist_ok=True)
    def copy_tree(self, src: Path, dst: Path): shutil.copytree(src, dst, dirs_exist_ok=True)
    def copy_file(self, src: Path, dst: Path): Path(dst).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst)
    def write_text(self, p: Path, text: str): Path(p).parent.mkdir(parents=True, exist_ok=True); Path(p).write_text(text, encoding="utf-8")
    def exists(self, p: Path) -> bool: return Path(p).exists()
    def remove_tree(self, p: Path): shutil.rmtree(p, ignore_errors=True)
    def remove_file(self, p: Path): Path(p).unlink(missing_ok=True)

    def delete_later(self, p: Path):
        """Xóa thư mục chứa chính chương trình đang chạy: chờ vài giây cho tiến trình thoát rồi mới xóa."""
        subprocess.Popen(["cmd", "/c", f'ping -n 4 127.0.0.1 >nul & rmdir /s /q "{p}"'], creationflags=CREATE_NO_WINDOW)

    def secure_dir(self, p: Path, users_read: bool):
        from agent.winapi import get_platform
        get_platform().secure_dir(p, users_read=users_read)

    # ---- registry (chỉ Windows)
    def reg_set(self, key: str, name: str, value, kind: str = "SZ"):
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, getattr(winreg, "REG_" + kind), value)

    def reg_delete_value(self, key: str, name: str):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
        except OSError:
            pass

    def reg_delete_key(self, key: str):
        import winreg
        try:
            winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, key)
        except OSError:
            pass

    # ---- ngữ cảnh
    def payload_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys._MEIPASS) / "payload"
        return Path(__file__).resolve().parent.parent / "dist" / "ogk"

    def own_exe(self) -> Path | None:
        return Path(sys.executable) if getattr(sys, "frozen", False) else None

    def local_users(self) -> list[str]:
        from agent.winapi import get_platform
        p = get_platform()
        return p.list_local_users() if hasattr(p, "list_local_users") else []

    def current_user(self) -> str:
        return os.environ.get("USERNAME", "")

    def wait_https(self, url: str, timeout: float) -> bool:
        import requests
        import urllib3
        urllib3.disable_warnings()
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if requests.get(url, verify=False, timeout=2).status_code == 200:
                    return True
            except requests.RequestException:
                time.sleep(0.5)
        return False

    def open_url(self, url: str):
        import webbrowser
        webbrowser.open(url)


def ensure_admin_or_relaunch(args: list[str]) -> bool:
    """True nếu đã có quyền quản trị. Nếu chưa: mở lại chính chương trình với hộp thoại UAC của Windows rồi trả False (tiến trình này nên thoát)."""
    if System().is_admin():
        return True
    if sys.platform == "win32":
        import ctypes
        params = " ".join(f'"{a}"' if " " in a else a for a in args)
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    return False
