"""Hệ thống giả cho test trình cài đặt: ghi lại lệnh/registry; `sc start OpenGuardKidsServer` chạy server thật trong luồng."""
import threading
from pathlib import Path

from installer.core import SERVICE_SERVER, Paths
from installer.system import System
from tests.live import free_port


class FakeSystem(System):
    def __init__(self, tmp: Path, admin=True, current="Me", users=("Me", "Bin"), fail_create=False, with_own_exe=False):
        self.tmp, self.admin, self.current, self.users, self.fail_create = tmp, admin, current, list(users), fail_create
        self.commands: list[list[str]] = []
        self.regs: dict = {}
        self.deleted_keys: list = []
        self.deleted_values: list = []
        self.secured: list = []
        self.delayed_delete: list = []
        self.stop_evt = threading.Event()
        self.threads: list = []
        payload = tmp / "payload"
        (payload / "_internal").mkdir(parents=True)
        (payload / "ogk.exe").write_text("fake-exe")
        (payload / "_internal" / "lib.dll").write_text("x")
        self._payload = payload
        self._own = None
        if with_own_exe:
            self._own = tmp / "OGK-Setup.exe"
            self._own.write_bytes(b"MZ-setup")
        self.paths = Paths(tmp / "PF" / "OpenGuardKids", tmp / "PD" / "Server", tmp / "PD" / "Agent", tmp / "Public",
                           tmp / "StartMenu", port=free_port(), discovery_port=free_port())

    # --- giả lập
    def is_admin(self): return self.admin

    def run(self, args, timeout=90):
        self.commands.append([str(a) for a in args])
        if args[:2] == ["sc.exe", "create"] and self.fail_create:
            return 5, "Access is denied."
        if args[:3] == ["sc.exe", "start", SERVICE_SERVER]:
            from server.runner import run
            t = threading.Thread(target=run, args=(self.paths.server_data,), daemon=True,
                                 kwargs=dict(host="127.0.0.1", port=self.paths.port, stop_event=self.stop_evt, discovery_port=self.paths.discovery_port))
            t.start()
            self.threads.append(t)
        return 0, ""

    def has(self, *prefix):
        return any(c[:len(prefix)] == list(prefix) for c in self.commands)

    def reg_set(self, key, name, value, kind="SZ"): self.regs[(key, name)] = value
    def reg_delete_value(self, key, name): self.deleted_values.append((key, name)); self.regs.pop((key, name), None)
    def reg_delete_key(self, key):
        self.deleted_keys.append(key)
        for k in [k for k in self.regs if k[0] == key]:
            self.regs.pop(k)
    def secure_dir(self, p, users_read): self.secured.append((Path(p), users_read))
    def delete_later(self, p): self.delayed_delete.append(Path(p))
    def payload_dir(self): return self._payload
    def own_exe(self): return self._own
    def local_users(self): return self.users
    def current_user(self): return self.current

    def shutdown(self):
        self.stop_evt.set()
        for t in self.threads:
            t.join(10)
