"""Cơ sở dữ liệu cục bộ SQLite (WAL): bản sao chính sách, bộ đếm sử dụng, hàng đợi sự kiện ngoại tuyến."""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path

MAX_QUEUE = 20000


class AgentStore:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        with self._lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS counters(day TEXT, k TEXT, v REAL, PRIMARY KEY(day, k));
            CREATE TABLE IF NOT EXISTS queue(idem_key TEXT PRIMARY KEY, ts INTEGER, type TEXT, subject TEXT,
                                             duration_sec INTEGER, policy_id INTEGER, seq INTEGER);
            CREATE TABLE IF NOT EXISTS applied_cmds(id INTEGER PRIMARY KEY, ts REAL);
            """)

    # ---- kv
    def kv_get(self, k, default=None):
        with self._lock:
            r = self.db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def kv_set(self, k, v):
        with self._lock:
            self.db.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))

    # ---- bộ đếm theo ngày
    def counters(self, day) -> dict:
        with self._lock:
            return dict(self.db.execute("SELECT k, v FROM counters WHERE day=?", (day,)).fetchall())

    def set_counters(self, day, values: dict):
        with self._lock:
            self.db.execute("BEGIN")
            self.db.executemany("INSERT INTO counters(day,k,v) VALUES(?,?,?) ON CONFLICT(day,k) DO UPDATE SET v=excluded.v",
                                [(day, k, float(v)) for k, v in values.items()])
            self.db.execute("COMMIT")

    def prune_counters(self, keep_days: set):
        with self._lock:
            days = [r[0] for r in self.db.execute("SELECT DISTINCT day FROM counters")]
            for d in days:
                if d not in keep_days:
                    self.db.execute("DELETE FROM counters WHERE day=?", (d,))

    # ---- hàng đợi sự kiện (idempotency key = UUIDv4)
    def add_event(self, type_, subject, duration_sec, policy_id, ts) -> str:
        key = str(uuid.uuid4())
        with self._lock:
            self.db.execute("INSERT INTO queue VALUES(?,?,?,?,?,?,?)",
                            (key, int(ts), type_, subject, int(duration_sec), int(policy_id), time.monotonic_ns()))
            n = self.db.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
            if n > MAX_QUEUE:
                self.db.execute("DELETE FROM queue WHERE idem_key IN (SELECT idem_key FROM queue ORDER BY ts LIMIT ?)", (n - MAX_QUEUE,))
        return key

    def pending_events(self, limit=200) -> list[dict]:
        with self._lock:
            rows = self.db.execute("SELECT idem_key, ts, type, subject, duration_sec, policy_id FROM queue ORDER BY ts, seq LIMIT ?", (limit,)).fetchall()
        return [dict(idem_key=r[0], ts=r[1], type=r[2], subject=r[3], duration_sec=r[4], policy_id=r[5]) for r in rows]

    def delete_events(self, keys):
        with self._lock:
            self.db.executemany("DELETE FROM queue WHERE idem_key=?", [(k,) for k in keys])

    def queue_size(self) -> int:
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM queue").fetchone()[0]

    # ---- lệnh đã áp dụng (khử trùng khi lệnh đến cả qua WS lẫn heartbeat)
    def command_applied(self, cid) -> bool:
        with self._lock:
            return self.db.execute("SELECT 1 FROM applied_cmds WHERE id=?", (cid,)).fetchone() is not None

    def mark_command(self, cid):
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO applied_cmds VALUES(?,?)", (cid, time.time()))
            self.db.execute("DELETE FROM applied_cmds WHERE ts < ?", (time.time() - 7 * 86400,))

    def reset_commands(self):
        """Ghép đôi mới / gỡ thiết bị: quên id lệnh cũ để lệnh từ máy chủ mới (id bắt đầu lại) không bị coi là trùng."""
        with self._lock:
            self.db.execute("DELETE FROM applied_cmds")
            self.db.execute("DELETE FROM kv WHERE k='state_cmd_seq'")

    def wipe_activity(self, keep_day: str):
        """'Xóa dữ liệu của con': xóa hàng đợi + thống kê; GIỮ bộ đếm tổng/thưởng của hôm nay để hạn mức không bị đặt lại."""
        with self._lock:
            self.db.execute("DELETE FROM queue")
            self.db.execute("DELETE FROM counters WHERE day<>?", (keep_day,))
            self.db.execute("DELETE FROM counters WHERE day=? AND k NOT IN ('total','bonus','gbonus','ltp_spent') AND k NOT LIKE 'g:%'", (keep_day,))
