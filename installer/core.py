"""Các bước cài đặt / gỡ cài đặt Open Guardian Kids. Không có giao diện ở đây: wizard.py chỉ gọi các hàm này."""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from agent.config import VERSION, default_home
from common.installcode import InstallCodeError, parse_install_code
from server.tls import ensure_cert, local_ips

from .pairing import PairingError, authenticate_server, discover, fetch_fingerprint, find_server, normalize_server
from .system import System

SERVICE_AGENT, SERVICE_SERVER = "OpenGuardKids", "OpenGuardKidsServer"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\OpenGuardKids"
FW_RULES = ("OGK Server (TCP)", "OGK Server (UDP)")
Progress = Callable[[int, str], None]


class InstallError(Exception):
    """Thông điệp tiếng Việt cho người không rành kỹ thuật (có gợi ý cách xử lý)."""


@dataclass
class Paths:
    install_dir: Path
    server_data: Path
    agent_home: Path
    public_desktop: Path
    start_menu: Path
    port: int = 8443
    discovery_port: int | None = None

    @property
    def exe(self) -> Path: return self.install_dir / "ogk.exe"
    @property
    def downloads(self) -> Path: return self.server_data / "downloads"

    @classmethod
    def from_env(cls) -> "Paths":
        pd = Path(os.environ.get("PROGRAMDATA", "C:/ProgramData"))
        return cls(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "OpenGuardKids", pd / "OpenGuardKidsServer", default_home(),
                   Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "Desktop", pd / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Open Guardian Kids")


def _need_admin(s: System):
    if not s.is_admin():
        raise InstallError("Cần quyền quản trị (Administrator). Hãy bấm chuột phải vào OGK-Setup.exe rồi chọn “Run as administrator”.")


def _scaled(progress: Progress, lo: int, hi: int) -> Progress:
    return lambda pct, text: progress(lo + (hi - lo) * pct // 100, text)


# ---------------------------------------------------------------- các bước dùng chung
def copy_program(s: System, p: Paths):
    src = s.payload_dir()
    if not s.exists(src):
        raise InstallError("Bộ cài bị thiếu tệp chương trình. Hãy tải lại OGK-Setup.exe rồi thử lại.")
    try:
        s.copy_tree(src, p.install_dir)
    except OSError as e:
        raise InstallError(f"Không chép được chương trình vào {p.install_dir}. Hãy tắt phần mềm diệt virus tạm thời rồi thử lại. ({e})")


def register_service(s: System, p: Paths, name: str, display: str, arg: str, desc: str):
    s.run(["sc.exe", "stop", name])
    s.run(["sc.exe", "delete", name])
    rc, out = s.run(["sc.exe", "create", name, "binPath=", f'"{p.exe}" {arg}', "start=", "auto", "obj=", "LocalSystem", "DisplayName=", display])
    if rc != 0:
        raise InstallError(f"Windows không cho tạo dịch vụ “{display}”. Hãy chắc chắn bạn đang chạy bằng quyền quản trị. ({out.strip()[:120]})")
    s.run(["sc.exe", "description", name, desc])
    s.run(["sc.exe", "failure", name, "reset=", "86400", "actions=", "restart/5000/restart/5000/restart/5000"])  # tự khởi động lại khi bị dừng


def start_service(s: System, name: str, display: str):
    rc, out = s.run(["sc.exe", "start", name])
    if rc != 0 and "1056" not in out:  # 1056 = đã chạy rồi
        raise InstallError(f"Dịch vụ “{display}” không khởi động được. ({out.strip()[:120]})")


def register_uninstall(s: System, p: Paths):
    """Hiện trong Settings → Apps để gỡ như phần mềm bình thường (công khai, không giấu)."""
    s.reg_set(UNINSTALL_KEY, "DisplayName", "Open Guardian Kids")
    s.reg_set(UNINSTALL_KEY, "DisplayVersion", VERSION)
    s.reg_set(UNINSTALL_KEY, "Publisher", "Open Guardian Kids")
    s.reg_set(UNINSTALL_KEY, "InstallLocation", str(p.install_dir))
    s.reg_set(UNINSTALL_KEY, "DisplayIcon", str(p.exe))
    s.reg_set(UNINSTALL_KEY, "UninstallString", f'"{p.exe}" uninstall')
    s.reg_set(UNINSTALL_KEY, "NoModify", 1, "DWORD")
    s.reg_set(UNINSTALL_KEY, "NoRepair", 1, "DWORD")


def _rollback(s: System, p: Paths, services: list[str], created_dir: bool):
    for name in services:
        s.run(["sc.exe", "stop", name])
        s.run(["sc.exe", "delete", name])
    if created_dir:
        s.remove_tree(p.install_dir)


# ---------------------------------------------------------------- máy của bố mẹ
def install_server(s: System, p: Paths, progress: Progress, copy_files: bool = True) -> dict:
    _need_admin(s)
    fresh = not s.exists(p.install_dir)
    try:
        if copy_files:
            progress(5, "Đang chép chương trình…")
            copy_program(s, p)
        progress(25, "Đang tạo chứng chỉ bảo mật cho gia đình…")
        s.makedirs(p.server_data)
        s.secure_dir(p.server_data, users_read=False)  # dữ liệu và khóa chỉ SYSTEM/Administrators đọc được
        cert, _key, fp = ensure_cert(p.server_data / "certs")
        progress(40, "Đang cho phép máy của con trong mạng gia đình kết nối…")
        for rule in FW_RULES:
            s.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule}"])
        s.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FW_RULES[0]}", "dir=in", "action=allow", "protocol=TCP",
               f"localport={p.port}", "profile=private"])   # chỉ mạng Private (nhà), không mở ra mạng công cộng
        s.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FW_RULES[1]}", "dir=in", "action=allow", "protocol=UDP",
               "localport=48600", "profile=private"])
        s.run(["certutil", "-addstore", "-f", "Root", str(cert)])   # để trình duyệt trên máy này không cảnh báo
        progress(55, "Đang cài dịch vụ bảng điều khiển…")
        register_service(s, p, SERVICE_SERVER, "Open Guardian Kids - Bảng điều khiển phụ huynh", "server-service",
                         "Bảng điều khiển của Open Guardian Kids trong mạng gia đình.")
        own = s.own_exe()
        if own:  # chia sẻ bộ cài để phụ huynh tải về máy của con từ bảng điều khiển
            s.copy_file(own, p.downloads / "OGK-Setup.exe")
        progress(70, "Đang khởi động bảng điều khiển…")
        start_service(s, SERVICE_SERVER, "Bảng điều khiển")
        if not s.wait_https(f"https://127.0.0.1:{p.port}/healthz", 45):
            raise InstallError("Bảng điều khiển không khởi động được. Hãy khởi động lại máy rồi chạy lại OGK-Setup.exe.")
        progress(90, "Đang tạo lối tắt…")
        url = f"https://localhost:{p.port}"
        shortcut = f"[InternetShortcut]\nURL={url}\n"
        s.write_text(p.public_desktop / "Bảng điều khiển OGK.url", shortcut)
        s.write_text(p.start_menu / "Bảng điều khiển phụ huynh.url", shortcut)
        register_uninstall(s, p)
        ip = (local_ips() or ["127.0.0.1"])[0]
        progress(100, "Xong!")
        return {"fingerprint": fp, "local_url": url, "lan_url": f"https://{ip}:{p.port}"}
    except InstallError:
        _rollback(s, p, [SERVICE_SERVER], fresh)
        raise


# ---------------------------------------------------------------- máy của con
def _friendly_enroll_error(msg: str) -> str:
    if "hết hạn" in msg or "không đúng" in msg:
        return "Mã cài đặt không đúng hoặc đã hết hạn (mã chỉ dùng được một lần trong 10 phút). Hãy tạo mã mới trên bảng điều khiển của bố mẹ."
    if "Thử quá nhiều" in msg:
        return "Bạn đã thử quá nhiều lần. Hãy đợi một phút rồi thử lại."
    return f"Ghép đôi không thành công: {msg}"


def install_child(s: System, p: Paths, progress: Progress, install_code: str, server: str | None = None,
                  monitored_users: list[str] | None = None, accept: bool = False, copy_files: bool = True,
                  allow_insecure: bool = False) -> dict:
    from agent.bootstrap import enroll
    from agent.config import AgentConfig
    from agent.sync_client import Offline
    if not accept:
        raise InstallError("Cần tích xác nhận bạn là cha/mẹ hoặc người giám hộ hợp pháp và đồng ý trước khi cài đặt.")
    _need_admin(s)
    try:
        enroll_code, check = parse_install_code(install_code)
    except InstallCodeError as e:
        raise InstallError(str(e))
    progress(5, "Đang tìm máy của bố mẹ trong mạng…")
    try:
        if server:
            scheme, host, port = normalize_server(server)
            url, fp = f"{scheme}://{host}:{port}", authenticate_server(scheme, host, port, check, allow_insecure)
        else:
            kw = {"port": p.discovery_port} if p.discovery_port else {}
            found = find_server(check, discover(**kw), allow_insecure)
            if not found:
                raise InstallError("Không tìm thấy máy của bố mẹ trong mạng. Hãy kiểm tra máy bố mẹ đang bật và cùng Wi-Fi với máy này, "
                                   "hoặc bấm “Nâng cao” để nhập địa chỉ máy bố mẹ.")
            url, fp = found
    except PairingError as e:
        raise InstallError(str(e))
    progress(25, f"Đã xác nhận đúng máy của bố mẹ ({url}) ✓")
    fresh = not s.exists(p.install_dir)
    try:
        if copy_files:
            progress(35, "Đang chép chương trình…")
            copy_program(s, p)
        progress(55, "Đang kết nối với bố mẹ…")
        cfg = AgentConfig(home=p.agent_home, allow_insecure=allow_insecure, monitored_users=list(monitored_users or []))
        try:
            res = enroll(cfg, url, fp or "", enroll_code)
        except Offline:
            raise InstallError("Mất kết nối tới máy của bố mẹ giữa chừng. Hãy thử lại.")
        except RuntimeError as e:
            raise InstallError(_friendly_enroll_error(str(e)))
        progress(75, "Đang cài dịch vụ đồng hành…")
        register_service(s, p, SERVICE_AGENT, "Open Guardian Kids Agent", "agent-service",
                         "Công cụ đồng hành giúp trẻ dùng máy lành mạnh. Minh bạch: biểu tượng khay luôn hiển thị.")
        s.reg_set(RUN_KEY, "OpenGuardKidsTray", f'"{p.exe}" tray')   # biểu tượng khay chạy khi đăng nhập (luôn hiện diện)
        start_service(s, SERVICE_AGENT, "Open Guardian Kids")
        register_uninstall(s, p)
        progress(100, "Xong!")
        return {"child": res["child_name"], "server": url}
    except InstallError:
        _rollback(s, p, [SERVICE_AGENT], fresh and copy_files)
        raise


# ---------------------------------------------------------------- một máy dùng chung
def _local_setup(p: Paths, fp: str, body: dict) -> dict:
    url = f"https://127.0.0.1:{p.port}/api/setup/local"
    try:
        if fetch_fingerprint("127.0.0.1", p.port) != fp:
            raise InstallError("Chứng chỉ của bảng điều khiển trên máy này không khớp. Hãy chạy lại trình cài đặt.")
        r = requests.post(url, json=body, verify=False, timeout=15)
    except (OSError, requests.RequestException):
        raise InstallError("Không kết nối được tới bảng điều khiển vừa cài. Hãy khởi động lại máy rồi chạy lại OGK-Setup.exe.")
    if r.status_code == 409:
        raise InstallError("Máy này đã được thiết lập trước đó. Hãy mở “Bảng điều khiển OGK” và đăng nhập, rồi dùng “Tạo mã cài đặt”.")
    if r.status_code >= 400:
        d = r.json().get("detail", "")
        if isinstance(d, list):
            d = "; ".join(x.get("msg", "") for x in d)
        raise InstallError(str(d) or "Thông tin chưa hợp lệ.")
    return r.json()


def install_shared(s: System, p: Paths, progress: Progress, parent_name: str, email: str, password: str, child_name: str,
                   child_age: int, child_windows_user: str, accept: bool) -> dict:
    if not accept:
        raise InstallError("Cần tích xác nhận bạn là cha/mẹ hoặc người giám hộ hợp pháp và đồng ý trước khi cài đặt.")
    if not child_windows_user.strip():
        raise InstallError("Hãy chọn tài khoản Windows của con. Chỉ tài khoản này bị giới hạn giờ, tài khoản của bố mẹ không bị ảnh hưởng.")
    if child_windows_user.strip().lower() == s.current_user().lower():
        raise InstallError("Tài khoản Windows của con không được trùng với tài khoản bạn đang dùng, nếu không bạn sẽ bị giới hạn giờ. "
                           "Hãy tạo thêm một tài khoản Windows cho con (Settings → Accounts → Family & other users).")
    info = install_server(s, p, _scaled(progress, 0, 60))
    progress(62, "Đang tạo tài khoản phụ huynh…")
    resp = _local_setup(p, info["fingerprint"], {"parent_name": parent_name, "email": email, "password": password,
                                                  "child_name": child_name, "child_age": child_age, "consent": accept})
    child = install_child(s, p, _scaled(progress, 65, 100), resp["install_code"], server=f"https://127.0.0.1:{p.port}",
                          monitored_users=[child_windows_user.strip()], accept=True, copy_files=False)
    return {**info, **child, "email": email, "child_windows_user": child_windows_user.strip()}


# ---------------------------------------------------------------- gỡ cài đặt
def uninstall(s: System, p: Paths, progress: Progress, keep_data: bool = False):
    _need_admin(s)
    progress(10, "Đang dừng các dịch vụ…")
    for name in (SERVICE_AGENT, SERVICE_SERVER):
        s.run(["sc.exe", "stop", name])
    progress(30, "Đang trả lại cấu hình mạng như ban đầu…")
    if s.exists(p.exe):
        s.run([str(p.exe), "agent", "restore"])   # trả DNS gốc, gỡ chính sách DoH
    for name in (SERVICE_AGENT, SERVICE_SERVER):
        s.run(["sc.exe", "delete", name])
    progress(55, "Đang gỡ các mục khỏi Windows…")
    s.reg_delete_value(RUN_KEY, "OpenGuardKidsTray")
    s.reg_delete_key(UNINSTALL_KEY)
    for rule in FW_RULES:
        s.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule}"])
    s.run(["certutil", "-delstore", "Root", socket.gethostname()])
    s.remove_file(p.public_desktop / "Bảng điều khiển OGK.url")
    s.remove_tree(p.start_menu)
    s.run(["ipconfig", "/flushdns"])
    progress(80, "Đang xóa tệp…")
    if not keep_data:
        s.remove_tree(p.agent_home)
        s.remove_tree(p.server_data)
    s.delete_later(p.install_dir)   # chương trình đang chạy không tự xóa mình ngay được
    progress(100, "Đã gỡ Open Guardian Kids.")
