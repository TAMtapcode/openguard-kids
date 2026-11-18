"""Giám sát tiến trình mỗi giây: đối chiếu TÊN + SHA-256 tệp thực thi (đổi tên file vẫn bị phát hiện)."""
from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import psutil

PROTECTED_NAMES = frozenset({
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "services.exe", "lsass.exe", "winlogon.exe",
    "svchost.exe", "explorer.exe", "dwm.exe", "fontdrvhost.exe", "ogk-service.exe", "ogk-tray.exe", "systemd", "init",
})
MAX_HASH_BYTES = 1 << 30
_SYSTEM_ROOTS = [r.lower() for r in (os.environ.get("SystemRoot"), os.environ.get("windir")) if r]


@dataclass
class ProcInfo:
    pid: int
    name: str
    exe: str | None
    create_time: float = 0.0


@dataclass
class Violation:
    pid: int
    name: str
    sha256: str | None
    reason: str          # blocklist | allowlist | group_budget
    group: str | None
    killed: bool = False


def sha256_file(path: str) -> str | None:
    try:
        if os.path.getsize(path) > MAX_HASH_BYTES:
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def default_source():
    for p in psutil.process_iter(["pid", "name", "exe", "create_time"]):
        try:
            i = p.info
            yield ProcInfo(i["pid"], (i["name"] or "").lower(), i.get("exe"), i.get("create_time") or 0.0)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def default_killer(pid: int) -> bool:
    try:
        p = psutil.Process(pid)
        p.kill()  # Windows: TerminateProcess
        p.wait(timeout=3)
        return True
    except psutil.TimeoutExpired:
        return False
    except psutil.NoSuchProcess:
        return True
    except psutil.AccessDenied:
        return False


class ProcessGuard:
    def __init__(self, get_policy, group_exhausted=lambda g: False, is_suspended=lambda: False,
                 source=default_source, killer=default_killer, hasher=sha256_file):
        self._policy, self._exhausted, self._suspended = get_policy, group_exhausted, is_suspended
        self._source, self._killer, self._hasher = source, killer, hasher
        self._hash_cache: dict = {}
        self._handled: set = set()
        self._own_pid = os.getpid()

    def _hash(self, exe: str | None):
        if not exe:
            return None
        try:
            st = os.stat(exe)
        except OSError:
            return None
        key = (exe, st.st_mtime_ns, st.st_size)
        if key not in self._hash_cache:
            if len(self._hash_cache) > 2000:
                self._hash_cache.clear()
            self._hash_cache[key] = self._hasher(exe)
        return self._hash_cache[key]

    @staticmethod
    def _match(entries, name, sha):
        for e in entries:
            if (e.get("sha256") and sha and e["sha256"] == sha) or (e.get("name") and e["name"] == name):
                return e
        return None

    def _is_system_path(self, exe):
        e = (exe or "").lower()
        return any(e.startswith(r) for r in _SYSTEM_ROOTS)

    def scan(self) -> list[Violation]:
        p = self._policy()
        if p is None or self._suspended():
            return []
        need_hash = any(e.get("sha256") for e in p.app_blocked + p.app_allowed)
        out: list[Violation] = []
        live = set()
        for pr in self._source():
            if pr.pid <= 4 or pr.pid == self._own_pid or pr.name in PROTECTED_NAMES:
                continue
            ident = (pr.pid, pr.create_time)
            live.add(ident)
            if ident in self._handled:
                continue
            sha = self._hash(pr.exe) if need_hash and not self._is_system_path(pr.exe) else None
            reason = group = None
            if p.app_mode == "allowlist":
                if not self._is_system_path(pr.exe) and not self._match(p.app_allowed, pr.name, sha):
                    reason = "allowlist"
            else:
                e = self._match(p.app_blocked, pr.name, sha)
                if e:
                    reason, group = "blocklist", p.app_group(pr.name, e)
            if reason is None:
                g = p.app_group(pr.name)
                if g and self._exhausted(g):
                    reason, group = "group_budget", g
            if reason:
                v = Violation(pr.pid, pr.name, sha, reason, group or p.app_group(pr.name))
                v.killed = self._killer(pr.pid)
                if v.killed:
                    self._handled.add(ident)
                out.append(v)
        self._handled &= live
        return out
