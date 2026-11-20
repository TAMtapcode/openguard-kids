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
DNS_BYPASS_FW_RULES = ("OGK Block DoT 853", "OGK Block Public DNS", "OGK Allow Upstream DNS", "OGK Allow Upstream DNS TCP")
PUBLIC_DNS_IPS = "1.1.1.1,1.0.0.1,8.8.8.8,8.8.4.4,9.9.9.9,149.112.112.112,208.67.222.222,208.67.220.220"


class Platform:
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
    def set_taskmgr_policy(self, block: bool, username: str = "") -> None: pass
    def launch_tray(self) -> bool: return False
    def acquire_single_instance(self, name: str): return object()
    def is_window_on_current_desktop(self, hwnd: int) -> bool: return True
    def is_window_foreground(self, hwnd: int) -> bool: return True
    def bring_window_to_front(self, hwnd: int) -> None: pass
    def virtual_screen_rect(self) -> tuple[int, int, int, int] | None: return None
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
        self._vdm = None
        self._vdm_inited = False
        self._mutex_handles: list = []

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
        self._LII = LASTINPUTINFO

    def acquire_single_instance(self, name: str):
        try:
            self.kernel32.SetLastError(0)
            h = self.kernel32.CreateMutexW(None, False, name)
            if not h:
                return object()
            if self.kernel32.GetLastError() == 183:
                self.kernel32.CloseHandle(h)
                return None
            self._mutex_handles.append(h)
            return h
        except Exception:
            return object()

    def _root_hwnd(self, hwnd: int) -> int:
        try:
            GA_ROOT = 2
            r = self.user32.GetAncestor(self.wt.HWND(int(hwnd)), GA_ROOT)
            return int(r) if r else int(hwnd)
        except Exception:
            return int(hwnd)

    def _get_vdm(self):
        if self._vdm_inited:
            return self._vdm
        self._vdm_inited = True
        try:
            ct = self.ct
            ole32 = ct.windll.ole32

            class GUID(ct.Structure):
                _fields_ = [("Data1", ct.c_uint32), ("Data2", ct.c_uint16),
                            ("Data3", ct.c_uint16), ("Data4", ct.c_ubyte * 8)]

            def make_guid(s: str) -> GUID:
                import uuid
                return GUID.from_buffer_copy(uuid.UUID(s).bytes_le)

            ole32.CoInitializeEx(None, 2)
            clsid = make_guid("{AA509086-5CA9-4C25-8F95-589D3C07B48A}")
            iid = make_guid("{A5CD92FF-29BE-454C-8D04-D82879FB3F1B}")
            ptr = ct.c_void_p()
            hr = ole32.CoCreateInstance(ct.byref(clsid), None, 1, ct.byref(iid), ct.byref(ptr))
            if hr == 0 and ptr.value:
                self._vdm = ptr
        except Exception:
            self._vdm = None
        return self._vdm

    def is_window_on_current_desktop(self, hwnd: int) -> bool:
        vdm = self._get_vdm()
        if not vdm:
            return True
        try:
            ct, wt = self.ct, self.wt
            vtable = ct.cast(vdm, ct.POINTER(ct.POINTER(ct.c_void_p))).contents
            fn_type = ct.WINFUNCTYPE(ct.c_long, ct.c_void_p, wt.HWND, ct.POINTER(wt.BOOL))
            is_on_cur = fn_type(vtable[3])
            out = wt.BOOL(1)
            hr = is_on_cur(vdm, wt.HWND(self._root_hwnd(hwnd)), ct.byref(out))
            if hr == 0:
                return bool(out.value)
        except Exception:
            pass
        return True

    def is_window_foreground(self, hwnd: int) -> bool:
        try:
            fg = self.user32.GetForegroundWindow()
            if not fg:
                return False
            return int(fg) in (int(hwnd), self._root_hwnd(hwnd))
        except Exception:
            return True

    def bring_window_to_front(self, hwnd: int) -> None:
        try:
            rh = self._root_hwnd(hwnd)
            HWND_TOPMOST = -1
            SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
            self.user32.ShowWindow(rh, 9)
            self.user32.SetWindowPos(rh, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            self.user32.SetForegroundWindow(rh)
        except Exception:
            pass

    def virtual_screen_rect(self) -> tuple[int, int, int, int] | None:
        try:
            x = int(self.user32.GetSystemMetrics(76))
            y = int(self.user32.GetSystemMetrics(77))
            w = int(self.user32.GetSystemMetrics(78))
            h = int(self.user32.GetSystemMetrics(79))
            if w > 0 and h > 0:
                return (x, y, w, h)
        except Exception:
            pass
        return None

    def idle_seconds(self) -> float:
        lii = self._LII()
        lii.cbSize = self.ct.sizeof(lii)
        if not self.user32.GetLastInputInfo(self.ct.byref(lii)):
            return 0.0
        return ((self.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000.0

    def session_locked(self) -> bool:
        h = self.user32.OpenInputDesktop(0, False, 0x0100)
        if not h:
            return True
        ok = self.user32.SwitchDesktop(h)
        self.user32.CloseDesktop(h)
        return not ok

    def foreground_process(self) -> str | None:
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

    def _console_session(self):
        sid = self.kernel32.WTSGetActiveConsoleSessionId()
        return None if sid == 0xFFFFFFFF else int(sid)

    def active_user(self) -> str:
        sid = self._console_session()
        if sid is None:
            return ""
        buf, n = self.ct.c_wchar_p(), self.wt.DWORD()
        if not self.wts.WTSQuerySessionInformationW(None, sid, 5, self.ct.byref(buf), self.ct.byref(n)):
            return ""
        user = buf.value or ""
        self.wts.WTSFreeMemory(buf)
        return user

    def list_local_users(self) -> list[str]:
        out = self._ps("Get-LocalUser | Where-Object { $_.Enabled } | Select-Object -ExpandProperty Name")
        return [l.strip() for l in out.splitlines() if l.strip() and l.strip().lower() not in ("administrator", "defaultaccount", "wdagutilityaccount", "guest")]

    def lock_session(self) -> None:
        pass

    def flush_dns(self) -> None:
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15)

    def _ps(self, script: str) -> str:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW, timeout=60)
        return r.stdout

    def _set_dns_firewall(self, block: bool) -> None:
        for rule in DNS_BYPASS_FW_RULES:
            subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule}"],
                           capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15)
        if block:
            # Chặn DoT (TCP 853); KHÔNG chặn UDP 53 toàn cục vì Windows Firewall outbound block rule
            # luôn thắng allow rule, dễ làm chính OGK DNS proxy không gửi được truy vấn upstream ra mạng.
            subprocess.run([
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={DNS_BYPASS_FW_RULES[0]}", "dir=out", "action=block", "protocol=TCP", "remoteport=853",
            ], capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15)

    def set_dns_loopback(self, state_file: Path) -> bool:
        if not Path(state_file).exists():
            out = self._ps("Get-DnsClientServerAddress | Select-Object InterfaceIndex,AddressFamily,ServerAddresses | ConvertTo-Json -Compress")
            Path(state_file).write_text(out or "[]", encoding="utf-8")
        self._ps(
            "Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { "
            "Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses 127.0.0.1 -ErrorAction SilentlyContinue; "
            "Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses '::1' -ErrorAction SilentlyContinue }"
        )
        self._set_dns_firewall(True)
        return True

    def restore_dns(self, state_file: Path) -> None:
        self._set_dns_firewall(False)
        sf = Path(state_file)
        try:
            data = json.loads(sf.read_text(encoding="utf-8") or "[]") if sf.exists() else []
        except ValueError:
            data = []
        if isinstance(data, dict):
            data = [data]
        for e in data:
            idx = int(e["InterfaceIndex"])
            addrs = e.get("ServerAddresses") or []
            if not addrs or all(a in ("127.0.0.1", "::1") for a in addrs):
                self._ps(f"Set-DnsClientServerAddress -InterfaceIndex {idx} -ResetServerAddresses -ErrorAction SilentlyContinue")
            else:
                self._ps(
                    f"Set-DnsClientServerAddress -InterfaceIndex {idx} -ServerAddresses "
                    + ",".join(f"'{a}'" for a in addrs)
                    + " -ErrorAction SilentlyContinue"
                )
        if not data:
            self._ps("Get-NetAdapter | ForEach-Object { Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ResetServerAddresses -ErrorAction SilentlyContinue }")
        sf.unlink(missing_ok=True)

    def set_browser_doh_policy(self, block: bool) -> None:
        import winreg
        keys = [
            (r"SOFTWARE\Policies\Google\Chrome", "DnsOverHttpsMode", "off"),
            (r"SOFTWARE\Policies\Microsoft\Edge", "DnsOverHttpsMode", "off"),
            (r"SOFTWARE\Policies\BraveSoftware\Brave", "DnsOverHttpsMode", "off"),
            (r"SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS", "Enabled", 0),
            (r"SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS", "Locked", 1),
        ]
        for path, name, val in keys:
            try:
                if block:
                    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_SET_VALUE) as k:
                        kind = winreg.REG_DWORD if isinstance(val, int) else winreg.REG_SZ
                        winreg.SetValueEx(k, name, 0, kind, val)
                else:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_SET_VALUE) as k:
                        winreg.DeleteValue(k, name)
            except OSError:
                pass

    def set_taskmgr_policy(self, block: bool, username: str = "") -> None:
        import winreg
        subpath = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
        if not block:
            try:
                i = 0
                while True:
                    sid = winreg.EnumKey(winreg.HKEY_USERS, i)
                    i += 1
                    try:
                        with winreg.OpenKey(winreg.HKEY_USERS, f"{sid}\\{subpath}", 0, winreg.KEY_SET_VALUE) as k:
                            winreg.DeleteValue(k, "DisableTaskMgr")
                    except OSError:
                        pass
            except OSError:
                pass
            return

        user = (username or self.active_user()).split("\\")[-1].strip()
        if not user:
            return
        ps = (
            f"$u = '{user.replace(chr(39), chr(39)*2)}'; "
            "$acc = New-Object System.Security.Principal.NTAccount($u); "
            "$sid = $acc.Translate([System.Security.Principal.SecurityIdentifier]).Value; "
            "$isAdm = (Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction SilentlyContinue | Where-Object { $_.SID.Value -eq $sid }) -ne $null; "
            "if (-not $isAdm) { Write-Output $sid }"
        )
        sid = self._ps(ps).strip()
        if not sid or not sid.startswith("S-1-5-21-"):
            return
        try:
            with winreg.CreateKeyEx(winreg.HKEY_USERS, f"{sid}\\{subpath}", 0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "DisableTaskMgr", 0, winreg.REG_DWORD, 1)
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
            if getattr(sys, "frozen", False):
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

    def _blob(self, data: bytes):
        class BLOB(self.ct.Structure):
            _fields_ = [("cbData", self.wt.DWORD), ("pbData", self.ct.POINTER(self.ct.c_char))]
        buf = self.ct.create_string_buffer(data, len(data))
        return BLOB(len(data), self.ct.cast(buf, self.ct.POINTER(self.ct.c_char))), BLOB(), buf

    def _dpapi(self, data: bytes, protect: bool) -> bytes:
        crypt32 = self.ct.windll.crypt32
        bin_, bout, _keep = self._blob(data)
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        flags = 0x5 if protect else 0x1
        if not fn(self.ct.byref(bin_), None, None, None, None, flags, self.ct.byref(bout)):
            raise self.ct.WinError()
        try:
            return self.ct.string_at(bout.pbData, bout.cbData)
        finally:
            self.kernel32.LocalFree(self.ct.cast(bout.pbData, self.ct.c_void_p))

    def protect(self, data: bytes) -> bytes: return self._dpapi(data, True)
    def unprotect(self, data: bytes) -> bytes: return self._dpapi(data, False)

    def is_admin(self) -> bool:
        try:
            return bool(self.ct.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False


def get_platform() -> Platform:
    return WindowsPlatform() if IS_WIN else Platform()
