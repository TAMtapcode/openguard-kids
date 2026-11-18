"""Lớp truy cập hệ điều hành. `Platform` là bản an toàn (không làm gì) dùng cho dev/test;
`WindowsPlatform` hiện thực bằng ctypes/pywin32. Cố ý KHÔNG có: chụp màn hình, keylogger, đọc tiêu đề cửa sổ."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000

SID_SYSTEM, SID_ADMINS, SID_USERS = "*S-1-5-18", "*S-1-5-32-544", "*S-1-5-32-545"


class Platform:
    """Bản không phụ thuộc OS: không có phiên người dùng, không khóa thật, không đổi DNS hệ thống."""
    def idle_seconds(self) -> float: return 0.0
    def session_locked(self) -> bool: return False
    def foreground_process(self) -> str | None: return None
    def active_user(self) -> str: return "user"
    def has_user_session(self) -> bool: return bool(self.active_user())
    def lock_session(self) -> None: pass
    def flush_dns(self) -> None: pass
    def set_dns_loopback(self, state_file: Path) -> bool: return False
    def restore_dns(self, state_file: Path) -> None: pass
    def set_browser_doh_policy(self, block: bool) -> None: pass
    def launch_tray(self) -> bool: return False
    def secure_dir(self, path: Path, users_read: bool = True) -> None:
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            os.chmod(path, 0o755 if users_read else 0o700)
        except OSError:
            pass
    def protect(self, data: bytes) -> bytes: return data
    def unprotect(self, data: bytes) -> bytes: return data
    def is_admin(self) -> bool: return hasattr(os, "geteuid") and os.geteuid() == 0


class WindowsPlatform(Platform):
    def __init__(self):
        import ctypes
        import ctypes.wintypes as wt
        self.ct, self.wt = ctypes, wt
        self.user32, self.kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self.wts = ctypes.windll.wtsapi32
        self.kernel32.GetTickCount.restype = wt.DWORD
        self.kernel32.WTSGetActiveConsoleSessionId.restype = wt.DWORD
        self.user32.OpenInputDesktop.restype = ctypes.c_void_p
        self.user32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.user32.SwitchDesktop.argtypes = [ctypes.c_void_p]
        self.user32.CloseDesktop.argtypes = [ctypes.c_void_p]

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
        self._LII = LASTINPUTINFO

    # ---- trạng thái phiên người dùng (chạy trong phiên của trẻ: Tray UI gọi rồi báo cho Service qua IPC)
    def idle_seconds(self) -> float:
        lii = self._LII()
        lii.cbSize = self.ct.sizeof(lii)
        if not self.user32.GetLastInputInfo(self.ct.byref(lii)):
            return 0.0
        return ((self.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0

    def session_locked(self) -> bool:
        h = self.user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not h:
            return True  # màn hình khóa/đăng nhập (Winlogon desktop) không mở được
        ok = self.user32.SwitchDesktop(h)
        self.user32.CloseDesktop(h)
        return not ok

    def foreground_process(self) -> str | None:
        """Chỉ lấy TÊN tiến trình đang ở tiền cảnh. Không đọc tiêu đề cửa sổ/nội dung (NT2)."""
        import psutil
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = self.wt.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
        try:
            return psutil.Process(pid.value).name().lower()
        except Exception:
            return None

    # ---- phía Service (SYSTEM, phiên 0)
    def _console_session(self):
        sid = self.kernel32.WTSGetActiveConsoleSessionId()
        return None if sid == 0xFFFFFFFF else int(sid)

    def active_user(self) -> str:
        """Tên tài khoản Windows đang đăng nhập ở phiên console ('' nếu chưa ai đăng nhập)."""
        sid = self._console_session()
        if sid is None:
            return ""
        buf, n = self.ct.c_wchar_p(), self.wt.DWORD()
        if not self.wts.WTSQuerySessionInformationW(None, sid, 5, self.ct.byref(buf), self.ct.byref(n)):  # 5 = WTSUserName
            return ""
        user = buf.value or ""
        self.wts.WTSFreeMemory(buf)
        return user

    def list_local_users(self) -> list[str]:
        """Các tài khoản Windows thông thường trên máy (để phụ huynh chọn tài khoản của con)."""
        out = self._ps("Get-LocalUser | Where-Object { $_.Enabled } | Select-Object -ExpandProperty Name")
        return [l.strip() for l in out.splitlines() if l.strip() and l.strip().lower() not in ("administrator", "defaultaccount", "wdagutilityaccount", "guest")]

    def lock_session(self) -> None:
        """Không ngắt/khóa phiên Windows (từng làm máy kẹt ở màn hình đăng nhập). Màn hình khóa do khay hiển thị trong phiên của trẻ."""

    def flush_dns(self) -> None:
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15)

    def _ps(self, script: str) -> str:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW, timeout=60)
        return r.stdout

    def set_dns_loopback(self, state_file: Path) -> bool:
        if not Path(state_file).exists():  # lưu DNS gốc để khôi phục chính xác khi dừng/gỡ
            out = self._ps("Get-DnsClientServerAddress -AddressFamily IPv4 | Select-Object InterfaceIndex,ServerAddresses | ConvertTo-Json -Compress")
            Path(state_file).write_text(out or "[]", encoding="utf-8")
        self._ps("Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object "
                 "{ Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses 127.0.0.1 }")
        return True

    def restore_dns(self, state_file: Path) -> None:
        sf = Path(state_file)
        try:
            data = json.loads(sf.read_text(encoding="utf-8") or "[]") if sf.exists() else []
        except ValueError:
            data = []
        if isinstance(data, dict):
            data = [data]
        for e in data:
            addrs = e.get("ServerAddresses") or []
            if addrs == ["127.0.0.1"] or not addrs:
                self._ps(f"Set-DnsClientServerAddress -InterfaceIndex {int(e['InterfaceIndex'])} -ResetServerAddresses")
            else:
                self._ps(f"Set-DnsClientServerAddress -InterfaceIndex {int(e['InterfaceIndex'])} -ServerAddresses " + ",".join(f"'{a}'" for a in addrs))
        if not data:
            self._ps("Get-NetAdapter | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses }")
        sf.unlink(missing_ok=True)

    def set_browser_doh_policy(self, block: bool) -> None:
        """Chính sách công khai (xem được ở chrome://policy, edge://policy): tắt DoH để DNS proxy không bị vượt qua."""
        import winreg
        keys = [(r"SOFTWARE\Policies\Google\Chrome", "DnsOverHttpsMode", "off"),
                (r"SOFTWARE\Policies\Microsoft\Edge", "DnsOverHttpsMode", "off")]
        for path, name, val in keys:
            try:
                if block:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_SET_VALUE) as k:
                        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
                else:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_SET_VALUE) as k:
                        winreg.DeleteValue(k, name)
            except OSError:
                pass

    def launch_tray(self) -> bool:
        try:
            import win32con, win32process, win32profile, win32ts
            sid = win32ts.WTSGetActiveConsoleSessionId()
            token = win32ts.WTSQueryUserToken(sid)
            env = win32profile.CreateEnvironmentBlock(token, False)
            si = win32process.STARTUPINFO()
            si.lpDesktop = "winsta0\\default"
            if getattr(sys, "frozen", False):  # bản đóng gói: cùng ogk.exe với dịch vụ, chạy chế độ tray
                cmd = f'"{sys.executable}" tray'
            else:
                cmd = f'"{sys.executable.replace("python.exe", "pythonw.exe")}" -m agent tray'
            win32process.CreateProcessAsUser(token, None, cmd, None, None, False,
                                             win32con.CREATE_UNICODE_ENVIRONMENT, env, None, si)
            return True
        except Exception:
            return False

    def secure_dir(self, path: Path, users_read: bool = True) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)
        grants = [f"{SID_SYSTEM}:(OI)(CI)F", f"{SID_ADMINS}:(OI)(CI)F"] + ([f"{SID_USERS}:(OI)(CI)RX"] if users_read else [])
        args = ["icacls", str(path), "/inheritance:r"] + [x for g in grants for x in ("/grant:r", g)]
        subprocess.run(args, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=30)

    # ---- DPAPI (phạm vi máy): bí mật thiết bị không nằm dạng rõ trên đĩa
    def _blob(self, data: bytes):
        class BLOB(self.ct.Structure):
            _fields_ = [("cbData", self.wt.DWORD), ("pbData", self.ct.POINTER(self.ct.c_char))]
        buf = self.ct.create_string_buffer(data, len(data))
        return BLOB(len(data), self.ct.cast(buf, self.ct.POINTER(self.ct.c_char))), BLOB(), buf

    def _dpapi(self, data: bytes, protect: bool) -> bytes:
        crypt32 = self.ct.windll.crypt32
        bin_, bout, _keep = self._blob(data)
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        flags = 0x5 if protect else 0x1  # UI_FORBIDDEN (| LOCAL_MACHINE khi mã hoá)
        if not fn(self.ct.byref(bin_), None, None, None, None, flags, self.ct.byref(bout)):
            raise self.ct.WinError()
        try:
            return self.ct.string_at(bout.pbData, bout.cbData)
        finally:
            self.kernel32.LocalFree(self.ct.cast(bout.pbData, self.ct.c_void_p))

    def protect(self, data: bytes) -> bytes: return data
    def unprotect(self, data: bytes) -> bytes: return data
    def is_admin(self) -> bool: return hasattr(os, "geteuid") and os.geteuid() == 0


class WindowsPlatform(Platform):
    def __init__(self):
        import ctypes
        import ctypes.wintypes as wt
        self.ct, self.wt = ctypes, wt
        self.user32, self.kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self.wts = ctypes.windll.wtsapi32
        self.kernel32.GetTickCount.restype = wt.DWORD
        self.kernel32.WTSGetActiveConsoleSessionId.restype = wt.DWORD
        self.user32.OpenInputDesktop.restype = ctypes.c_void_p
        self.user32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.user32.SwitchDesktop.argtypes = [ctypes.c_void_p]
        self.user32.CloseDesktop.argtypes = [ctypes.c_void_p]

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
        self._LII = LASTINPUTINFO

    # ---- trạng thái phiên người dùng (chạy trong phiên của trẻ: Tray UI gọi rồi báo cho Service qua IPC)
    def idle_seconds(self) -> float:
        lii = self._LII()
        lii.cbSize = self.ct.sizeof(lii)
        if not self.user32.GetLastInputInfo(self.ct.byref(lii)):
            return 0.0
        return ((self.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0

    def session_locked(self) -> bool:
        h = self.user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not h:
            return True  # màn hình khóa/đăng nhập (Winlogon desktop) không mở được
        ok = self.user32.SwitchDesktop(h)
        self.user32.CloseDesktop(h)
        return not ok

    def foreground_process(self) -> str | None:
        """Chỉ lấy TÊN tiến trình đang ở tiền cảnh. Không đọc tiêu đề cửa sổ/nội dung (NT2)."""
        import psutil
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = self.wt.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
        try:
            return psutil.Process(pid.value).name().lower()
        except Exception:
            return None

    # ---- phía Service (SYSTEM, phiên 0)
    def _console_session(self):
        sid = self.kernel32.WTSGetActiveConsoleSessionId()
        return None if sid == 0xFFFFFFFF else int(sid)

    def active_user(self) -> str:
        """Tên tài khoản Windows đang đăng nhập ở phiên console ('' nếu chưa ai đăng nhập)."""
        sid = self._console_session()
        if sid is None:
            return ""
        buf, n = self.ct.c_wchar_p(), self.wt.DWORD()
        if not self.wts.WTSQuerySessionInformationW(None, sid, 5, self.ct.byref(buf), self.ct.byref(n)):  # 5 = WTSUserName
            return ""
        user = buf.value or ""
        self.wts.WTSFreeMemory(buf)
        return user

    def list_local_users(self) -> list[str]:
        """Các tài khoản Windows thông thường trên máy (để phụ huynh chọn tài khoản của con)."""
        out = self._ps("Get-LocalUser | Where-Object { $_.Enabled } | Select-Object -ExpandProperty Name")
        return [l.strip() for l in out.splitlines() if l.strip() and l.strip().lower() not in ("administrator", "defaultaccount", "wdagutilityaccount", "guest")]

    def lock_session(self) -> None:
        """Không ngắt/khóa phiên Windows (từng làm máy kẹt ở màn hình đăng nhập). Màn hình khóa do khay hiển thị trong phiên của trẻ."""

    def flush_dns(self) -> None:
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15)

    def _ps(self, script: str) -> str:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW, timeout=60)
        return r.stdout

    def set_dns_loopback(self, state_file: Path) -> bool:
        if not Path(state_file).exists():  # lưu DNS gốc để khôi phục chính xác khi dừng/gỡ
            out = self._ps("Get-DnsClientServerAddress -AddressFamily IPv4 | Select-Object InterfaceIndex,ServerAddresses | ConvertTo-Json -Compress")
            Path(state_file).write_text(out or "[]", encoding="utf-8")
        self._ps("Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object "
                 "{ Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses 127.0.0.1 }")
        return True

    def restore_dns(self, state_file: Path) -> None:
        sf = Path(state_file)
        try:
            data = json.loads(sf.read_text(encoding="utf-8") or "[]") if sf.exists() else []
        except ValueError:
            data = []
        if isinstance(data, dict):
            data = [data]
        for e in data:
            addrs = e.get("ServerAddresses") or []
            if addrs == ["127.0.0.1"] or not addrs:
                self._ps(f"Set-DnsClientServerAddress -InterfaceIndex {int(e['InterfaceIndex'])} -ResetServerAddresses")
            else:
                self._ps(f"Set-DnsClientServerAddress -InterfaceIndex {int(e['InterfaceIndex'])} -ServerAddresses " + ",".join(f"'{a}'" for a in addrs))
        if not data:
            self._ps("Get-NetAdapter | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses }")
        sf.unlink(missing_ok=True)

    def set_browser_doh_policy(self, block: bool) -> None:
        """Chính sách công khai (xem được ở chrome://policy, edge://policy): tắt DoH để DNS proxy không bị vượt qua."""
        import winreg
        keys = [(r"SOFTWARE\Policies\Google\Chrome", "DnsOverHttpsMode", "off"),
                (r"SOFTWARE\Policies\Microsoft\Edge", "DnsOverHttpsMode", "off")]
        for path, name, val in keys:
            try:
                if block:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_SET_VALUE) as k:
                        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
                else:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_SET_VALUE) as k:
                        winreg.DeleteValue(k, name)
            except OSError:
                pass

    def launch_tray(self) -> bool:
        try:
            import win32con, win32process, win32profile, win32ts
            sid = win32ts.WTSGetActiveConsoleSessionId()
            token = win32ts.WTSQueryUserToken(sid)
            env = win32profile.CreateEnvironmentBlock(token, False)
            si = win32process.STARTUPINFO()
            si.lpDesktop = "winsta0\\default"
            if getattr(sys, "frozen", False):  # bản đóng gói: cùng ogk.exe với dịch vụ, chạy chế độ tray
                cmd = f'"{sys.executable}" tray'
            else:
                cmd = f'"{sys.executable.replace("python.exe", "pythonw.exe")}" -m agent tray'
            win32process.CreateProcessAsUser(token, None, cmd, None, None, False,
                                             win32con.CREATE_UNICODE_ENVIRONMENT, env, None, si)
            return True
        except Exception:
            return False

    def secure_dir(self, path: Path, users_read: bool = True) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)
        grants = [f"{SID_SYSTEM}:(OI)(CI)F", f"{SID_ADMINS}:(OI)(CI)F"] + ([f"{SID_USERS}:(OI)(CI)RX"] if users_read else [])
        args = ["icacls", str(path), "/inheritance:r"] + [x for g in grants for x in ("/grant:r", g)]
        subprocess.run(args, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=30)

    # ---- DPAPI (phạm vi máy): bí mật thiết bị không nằm dạng rõ trên đĩa
    def _blob(self, data: bytes):
        class BLOB(self.ct.Structure):
            _fields_ = [("cbData", self.wt.DWORD), ("pbData", self.ct.POINTER(self.ct.c_char))]
        buf = self.ct.create_string_buffer(data, len(data))
        return BLOB(len(data), self.ct.cast(buf, self.ct.POINTER(self.ct.c_char))), BLOB(), buf

    def _dpapi(self, data: bytes, protect: bool) -> bytes:
        crypt32 = self.ct.windll.crypt32
        bin_, bout, keep = self._blob(data)
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        args = (self.ct.byref(bin_), None, None, None, None, 0x5 if protect else 0x1, self.ct.byref(bout)) if protect else \
               (self.ct.byref(bin_), None, None, None, None, 0x1, self.ct.byref(bout))
        if not fn(*args):
            raise self.ct.WinError()
        try:
            return self.ct.string_at(bout.pbData, bout.cbData)
        finally:
            self.kernel32.LocalFree(self.ct.cast(bout.pbData, self.ct.c_void_p))

    def protect(self, data: bytes) -> bytes: return self._dpapi(data, True)    # LOCAL_MACHINE | UI_FORBIDDEN
    def unprotect(self, data: bytes) -> bytes: return self._dpapi(data, False)

    def is_admin(self) -> bool:
        try:
            return bool(self.ct.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False


def get_platform() -> Platform:
    return WindowsPlatform() if IS_WIN else Platform()


def lock_workstation():
    # THIẾT KẾ LẠI: MÀN HÌNH PHỦ TOPMOST THAY VÌ KHÓA HỆ ĐIỀU HÀNH
    import threading
    def _overlay():
        try:
            import tkinter as tk
            root = tk.Tk()
            root.attributes("-topmost", True)
            root.attributes("-fullscreen", True)
            root.configure(bg='black')
            tk.Label(root, text="MÁY TÍNH ĐANG BỊ KHÓA BỞI PHỤ HUYNH", fg="white", bg="black", font=("Arial", 36)).pack(expand=True)
            # Tự động hủy sau 5 giây để tránh kẹt hệ thống
            root.after(5000, root.destroy)
            root.mainloop()
        except:
            pass
    threading.Thread(target=_overlay, daemon=True).start()
