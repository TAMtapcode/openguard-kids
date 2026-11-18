import json
from pathlib import Path
from types import SimpleNamespace

from common.schema import DEFAULT_APP_GROUPS
from server.categories import CategoryDB
from server.services import default_policy, validate_policy_update

ROOT = Path(__file__).resolve().parent.parent
CDB = CategoryDB(ROOT / "data" / "categories.csv")


def make_doc(age=9, version=1, device_id="dev-1", **updates):
    """Chính sách mặc định của server (cho trẻ 6–11) + cập nhật đã qua kiểm tra, dạng Agent nhận."""
    base = default_policy(SimpleNamespace(age=age))
    doc = validate_policy_update(base, updates) if updates else base
    doc.update(version=version, child_id=1, device_id=device_id, issued_at=0, set_by="Mẹ Bin",
               categories=CDB.map, doh_domains=CDB.doh, app_groups=DEFAULT_APP_GROUPS)
    return doc


def lines(name):
    return (ROOT / "data" / "testset" / name).read_text().split()


class FakeClock:
    """Đồng hồ giả: wall (đồng hồ Windows) và mono (đồng hồ phần cứng) độc lập để mô phỏng chỉnh giờ."""
    def __init__(self, wall=1_800_000_000.0, mono=1000.0):
        self.w, self.m = wall, mono

    def wall(self): return self.w
    def mono(self): return self.m

    def advance(self, s):
        self.w += s
        self.m += s


from agent.winapi import Platform  # noqa: E402


class FakePlatform(Platform):
    def __init__(self):
        self.locks, self.flushes, self.restored, self.fg, self.idle, self.launched = 0, 0, 0, None, 0.0, 0
        self.session = True
        self.user = "child"

    def has_user_session(self): return self.session
    def active_user(self): return self.user if self.session else ""
    def lock_session(self): self.locks += 1
    def flush_dns(self): self.flushes += 1
    def restore_dns(self, f): self.restored += 1
    def foreground_process(self): return self.fg
    def idle_seconds(self): return self.idle
    def launch_tray(self): self.launched += 1; return True
