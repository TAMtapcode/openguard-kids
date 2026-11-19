"""Bộ máy luật thuần (không phụ thuộc OS): chính sách, hạn mức/lịch/ân hạn/cảnh báo, bộ đếm sử dụng."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from common.schema import match_suffix, normalize_host


def local_dt(epoch: float, tz_min: int) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc) + timedelta(minutes=tz_min)


class Policy:
    def __init__(self, doc: dict):
        self.doc = doc
        self.version = int(doc["version"])
        self.tz = int(doc.get("tz_offset_min", 420))
        q = doc["quota"]
        self.weekday_sec, self.weekend_sec = q["weekday_min"] * 60, q["weekend_min"] * 60
        self.schedule = doc["schedule"]
        self.grace = int(doc.get("grace_seconds", 60))
        self.warn = sorted({int(m) * 60 for m in doc.get("warn_minutes", [10, 5, 1])}, reverse=True)
        self.idle_seconds = int(doc.get("idle_seconds", 300))
        self.group_budget = {g: int(m) * 60 for g, m in doc.get("group_budgets_min", {}).items()}
        apps = doc.get("apps", {})
        self.app_mode = apps.get("mode", "blocklist")
        self.app_blocked = apps.get("blocked", [])
        self.app_allowed = apps.get("allowed", [])
        d = doc.get("domains", {})
        self.d_blocked, self.d_allowed = set(d.get("blocked", [])), set(d.get("allowed", []))
        self.blocked_cats = set(d.get("blocked_categories", []))
        self.block_doh, self.safesearch = bool(d.get("block_doh", True)), bool(d.get("safesearch", True))
        self.doh = set(doc.get("doh_domains", []))
        self.categories: dict = doc.get("categories", {})
        self.app_groups: dict = doc.get("app_groups", {})
        self.system_cats = set(doc.get("meta", {}).get("system_categories", []))
        self.set_by = doc.get("set_by", "Phụ huynh")
        self.ltp = doc.get("learn_to_play", {"enabled": False})
        self.stale_days = int(doc.get("failsafe", {}).get("stale_after_days", 7))

    def limit_sec(self, weekday: int) -> int:
        return self.weekend_sec if weekday >= 5 else self.weekday_sec

    def slot_allowed(self, weekday: int, slot: int) -> bool:
        return bool(self.schedule[weekday][slot])

    def seconds_until_disallowed(self, dt: datetime) -> float:
        wd, minute = dt.weekday(), dt.hour * 60 + dt.minute
        slot = minute // 30
        if not self.slot_allowed(wd, slot):
            return 0.0
        into = (minute % 30) * 60 + dt.second
        for k in range(1, 7 * 48 + 1):
            idx = (wd * 48 + slot + k) % (7 * 48)
            if not self.schedule[idx // 48][idx % 48]:
                return k * 1800 - into
        return math.inf

    def category_of(self, host: str) -> str:
        k = match_suffix(host, self.categories)
        return self.categories[k] if k else "uncategorized"

    def app_group(self, name: str, entry: dict | None = None) -> str | None:
        name = (name or "").lower()
        if entry and entry.get("group"):
            return entry["group"]
        return self.app_groups.get(name)


@dataclass
class Decision:
    state: str = "ok"                # ok | grace | locked
    reason: str | None = None        # quota | schedule | manual
    remaining_sec: float = math.inf
    limit_sec: float = 0.0
    grace_left: float | None = None
    warnings: list = field(default_factory=list)


class Enforcer:
    """Trạng thái ok -> grace (60s để lưu bài) -> locked; cảnh báo còn 10/5/1 phút."""

    def __init__(self, mono=time.monotonic):
        self._mono = mono
        self._grace_start: float | None = None
        self._last_state: str | None = None
        self._armed: dict | None = None

    def reset(self, state: str = "ok"):
        self._grace_start, self._last_state, self._armed = None, state, None

    def evaluate(self, policy: Policy, dt: datetime, used_sec: float, bonus_sec: float, manual_lock: bool) -> Decision:
        limit = policy.limit_sec(dt.weekday()) + bonus_sec
        quota_left = limit - used_sec
        remaining = min(quota_left, policy.seconds_until_disallowed(dt))
        d = Decision(remaining_sec=remaining, limit_sec=limit)
        if manual_lock:
            d.state, d.reason = "locked", "manual"
            self._grace_start = None
        elif remaining <= 0:
            d.reason = "quota" if quota_left <= 0 else "schedule"
            if self._grace_start is None:
                if self._last_state == "ok" and policy.grace > 0:
                    self._grace_start = self._mono()   # chỉ cho ân hạn khi đang dùng giữa chừng
                    d.state, d.grace_left = "grace", float(policy.grace)
                else:
                    d.state = "locked"
            else:
                left = policy.grace - (self._mono() - self._grace_start)
                d.state, d.grace_left = ("grace", left) if left > 0 else ("locked", None)
        else:
            self._grace_start = None
        if d.state == "ok":
            armed_now = {t: remaining > t for t in policy.warn}
            if self._armed is not None:
                fired = [t for t in policy.warn if remaining <= t and self._armed.get(t, False)]
                if fired:
                    d.warnings = [min(fired)]
            self._armed = armed_now
        self._last_state = d.state
        return d


class UsageAccountant:
    """Đếm thời gian sử dụng thực bằng đồng hồ phần cứng. Mỗi nhịp tối đa MAX_STEP giây
    (chống đếm vọt khi máy ngủ/treo). Bộ đếm lưu bền để khởi động lại không đặt lại hạn mức."""
    MAX_STEP = 5.0

    def __init__(self, store, mono=time.monotonic):
        self.store, self._mono = store, mono
        self._last = mono()
        self._data: dict[str, dict] = {}
        self._dirty: set[str] = set()

    def _day(self, day) -> dict:
        if day not in self._data:
            self._data[day] = self.store.counters(day)
        return self._data[day]

    def get(self, day, key) -> float:
        return float(self._day(day).get(key, 0.0))

    def add(self, day, key, amount):
        d = self._day(day)
        d[key] = d.get(key, 0.0) + amount
        self._dirty.add(day)

    def tick(self, day, active: bool, groups=()) -> float:
        now = self._mono()
        dt = max(0.0, min(now - self._last, self.MAX_STEP))
        self._last = now
        if not active:
            return 0.0
        self.add(day, "total", dt)
        for g in groups:
            self.add(day, f"g:{g}", dt)
        return dt

    def counters_view(self, day):
        return list(self._day(day).items())

    def flush(self):
        for day in list(self._dirty):
            self.store.set_counters(day, self._data[day])
        self._dirty.clear()

    def reload(self):
        self._data.clear()
        self._dirty.clear()
