"""Nghiệp vụ: chính sách, nhật ký kiểm toán, lệnh khẩn, báo cáo, lưu trữ 90 ngày."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from common.schema import CATEGORIES, COMMAND_ACTIONS, DEFAULT_APP_GROUPS
from common.signing import sign

from .models import (AuditLog, Child, ChildRequest, Command, DailyUsage, Device, EnrollCode, Event,
                     PolicyRow, User, WebSession, now)

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[^\\/:*?\"<>|]{1,80}$")
GROUPS = tuple(CATEGORIES)
DAYS_VI = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
CMD_TTL = {"LOCK_NOW": 600, "UNLOCK": 600, "ADD_TIME": 86400, "WIPE_LOCAL": 7 * 86400, "POLICY_UPDATED": 600}


# ---------------------------------------------------------------- chính sách
def default_policy(child: Child) -> dict:
    young = child.age <= 11  # mặc định cho trẻ 6-11 tuổi
    wd = [1 if 12 <= s < 42 else 0 for s in range(48)]  # 06:00-21:00
    we = [1 if 14 <= s < 43 else 0 for s in range(48)]  # 07:00-21:30
    blocked = ["social", "inappropriate"] if young else ["inappropriate"]
    return {
        "tz_offset_min": 420,
        "quota": {"weekday_min": 90, "weekend_min": 120},
        "schedule": [list(wd) for _ in range(5)] + [list(we) for _ in range(2)],
        "idle_seconds": 300, "grace_seconds": 60, "warn_minutes": [10, 5, 1],
        "group_budgets_min": {"game": 45, "entertainment": 60} if young else {"game": 60, "social": 45},
        "apps": {"mode": "blocklist", "blocked": [], "allowed": []},
        "domains": {"blocked": [], "allowed": [], "blocked_categories": list(blocked),
                    "block_doh": True, "safesearch": True},
        "learn_to_play": {"enabled": True, "earn_ratio": 0.25, "daily_cap_min": 30},
        "failsafe": {"stale_after_days": 7},
        "meta": {"system_categories": list(blocked), "profile": "6-11" if young else "12+"},
    }


def _int(v, lo, hi, label):
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{label} phải là số nguyên.")
    if not lo <= v <= hi:
        raise ValueError(f"{label} phải nằm trong khoảng {lo}–{hi}.")
    return v


def _domains(lst, label):
    out = []
    for raw in lst or []:
        d = str(raw).strip().lower().rstrip(".")
        if d.startswith("*."):
            d = d[2:]
        if not d:
            continue
        if not DOMAIN_RE.match(d):
            raise ValueError(f"{label}: '{raw}' không phải tên miền hợp lệ (không nhập URL đầy đủ).")
        if d not in out:
            out.append(d)
    return out


def validate_policy_update(cur: dict, data: dict) -> dict:
    """Trả về chính sách mới đã làm sạch; ném ValueError (tiếng Việt) nếu dữ liệu sai."""
    new = json.loads(json.dumps(cur))
    if "quota" in data:
        q = data["quota"] or {}
        new["quota"] = {"weekday_min": _int(q.get("weekday_min", cur["quota"]["weekday_min"]), 0, 1440, "Hạn mức ngày thường"),
                        "weekend_min": _int(q.get("weekend_min", cur["quota"]["weekend_min"]), 0, 1440, "Hạn mức cuối tuần")}
    if "schedule" in data:
        s = data["schedule"]
        if not (isinstance(s, list) and len(s) == 7 and all(isinstance(r, list) and len(r) == 48 for r in s)):
            raise ValueError("Lịch tuần phải là ma trận 7×48.")
        new["schedule"] = [[1 if int(x) else 0 for x in r] for r in s]
    if "group_budgets_min" in data:
        gb = {}
        for g, v in (data["group_budgets_min"] or {}).items():
            if g not in GROUPS:
                raise ValueError(f"Nhóm '{g}' không tồn tại.")
            if v in (None, ""):
                continue
            gb[g] = _int(v, 0, 1440, f"Ngân sách nhóm {CATEGORIES[g]}")
        new["group_budgets_min"] = gb
    if "apps" in data:
        a = data["apps"] or {}
        mode = a.get("mode", cur["apps"]["mode"])
        if mode not in ("blocklist", "allowlist"):
            raise ValueError("Chế độ ứng dụng không hợp lệ.")

        def entries(lst, label):
            out = []
            for e in lst or []:
                name = (e.get("name") or "").strip().lower()
                sha = (e.get("sha256") or "").strip().lower()
                grp = e.get("group") or DEFAULT_APP_GROUPS.get(name)
                if not name and not sha:
                    continue
                if name and not NAME_RE.match(name):
                    raise ValueError(f"{label}: tên tiến trình '{name}' không hợp lệ.")
                if sha and not SHA_RE.match(sha):
                    raise ValueError(f"{label}: SHA-256 của '{name or sha[:8]}' phải gồm 64 ký tự hex.")
                if grp and grp not in GROUPS:
                    raise ValueError(f"{label}: nhóm '{grp}' không hợp lệ.")
                out.append({k: v for k, v in (("name", name), ("sha256", sha), ("group", grp)) if v})
            return out
        new["apps"] = {"mode": mode, "blocked": entries(a.get("blocked", cur["apps"]["blocked"]), "Ứng dụng bị chặn"),
                       "allowed": entries(a.get("allowed", cur["apps"]["allowed"]), "Ứng dụng được phép")}
    if "domains" in data:
        d = data["domains"] or {}
        cats = d.get("blocked_categories", cur["domains"]["blocked_categories"])
        if any(c not in GROUPS for c in cats):
            raise ValueError("Nhóm tên miền không hợp lệ.")
        new["domains"] = {"blocked": _domains(d.get("blocked", cur["domains"]["blocked"]), "Tên miền chặn"),
                          "allowed": _domains(d.get("allowed", cur["domains"]["allowed"]), "Tên miền cho phép"),
                          "blocked_categories": list(dict.fromkeys(cats)),
                          "block_doh": bool(d.get("block_doh", cur["domains"]["block_doh"])),
                          "safesearch": bool(d.get("safesearch", cur["domains"]["safesearch"]))}
        if set(new["domains"]["blocked_categories"]) != set(cur["domains"]["blocked_categories"]):
            new["meta"]["system_categories"] = []  # phụ huynh đã tuỳ chỉnh -> không còn là mặc định hệ thống
    if "learn_to_play" in data:
        l = data["learn_to_play"] or {}
        try:
            ratio = float(l.get("earn_ratio", cur["learn_to_play"]["earn_ratio"]))
        except (TypeError, ValueError):
            raise ValueError("Tỉ lệ đổi giờ phải là số.")
        if not 0 <= ratio <= 2:
            raise ValueError("Tỉ lệ đổi giờ phải trong khoảng 0–2.")
        new["learn_to_play"] = {"enabled": bool(l.get("enabled", cur["learn_to_play"]["enabled"])), "earn_ratio": ratio,
                                "daily_cap_min": _int(l.get("daily_cap_min", cur["learn_to_play"]["daily_cap_min"]), 0, 600, "Trần đổi giờ")}
    if "failsafe" in data:
        new["failsafe"] = {"stale_after_days": _int((data["failsafe"] or {}).get("stale_after_days", cur["failsafe"]["stale_after_days"]), 1, 90, "Số ngày mất kết nối")}
    return new


def _flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        if k == "meta":
            continue  # siêu dữ liệu nội bộ, không đưa vào nhật ký kiểm toán
        if isinstance(v, dict):
            out.update(_flatten(v, f"{prefix}{k}."))
        else:
            out[f"{prefix}{k}"] = v
    return out


def _fmt(key, v):
    if key == "schedule":
        return " | ".join("".join(str(x) for x in r) for r in v)
    return json.dumps(v, ensure_ascii=False)


def get_policy_row(db: Session, child: Child) -> PolicyRow:
    row = db.get(PolicyRow, child.id)
    if row is None:
        row = PolicyRow(child_id=child.id, version=1, doc=json.dumps(default_policy(child)), updated_at=now(), updated_by="system")
        db.add(row)
        db.flush()
    return row


def save_policy(app, db: Session, child: Child, new_doc: dict, actor: str, ip: str) -> PolicyRow:
    row = get_policy_row(db, child)
    cur = json.loads(row.doc)
    old_f, new_f = _flatten(cur), _flatten(new_doc)
    changed = [k for k in sorted(set(old_f) | set(new_f)) if old_f.get(k) != new_f.get(k)]
    if not changed:
        return row
    for k in changed:
        audit(db, actor, ip, child.id, "policy_change", k, _fmt(k.split(".")[0], old_f.get(k)), _fmt(k.split(".")[0], new_f.get(k)))
    row.doc = json.dumps(new_doc)
    row.version += 1
    row.updated_at, row.updated_by = now(), actor
    db.flush()
    for dev in active_devices(db, child.id):  # hiệu lực sớm: đẩy tín hiệu qua WS; nếu không, heartbeat 60s sẽ kéo về
        create_command(app, db, dev, "POLICY_UPDATED", 0, actor)
    return row


def policy_envelope(app, db: Session, device: Device) -> dict:
    child = db.get(Child, device.child_id)
    row = get_policy_row(db, child)
    doc = json.loads(row.doc)
    cdb = app.state.categories
    doc.update(version=row.version, child_id=child.id, device_id=device.id, issued_at=now(),
               set_by=db.get(User, child.parent_id).display_name or "Phụ huynh",
               categories=cdb.map, doh_domains=cdb.doh, app_groups=DEFAULT_APP_GROUPS)
    return {"policy": doc, "sig": sign(doc, device.policy_key)}


# ---------------------------------------------------------------- kiểm toán / thiết bị / lệnh
def audit(db: Session, actor: str, ip: str, child_id, action: str, field: str = "", old: str = "", new: str = ""):
    db.add(AuditLog(ts=now(), actor=actor, actor_ip=ip, child_id=child_id, action=action, field=field, old_value=old, new_value=new))


def active_devices(db: Session, child_id: int) -> list[Device]:
    return list(db.scalars(select(Device).where(Device.child_id == child_id, Device.status == "active")))


def is_online(app, dev: Device) -> bool:
    return app.state.manager.is_connected(dev.id) or (now() - dev.last_seen) <= app.state.settings.online_window_sec


def create_command(app, db: Session, device: Device, action: str, minutes: int, actor: str) -> Command:
    assert action in COMMAND_ACTIONS
    cmd = Command(device_id=device.id, action=action, minutes=minutes, created_by=actor, created_at=now())
    db.add(cmd)
    db.flush()
    db.commit()  # phải commit trước khi đẩy: Agent có thể ack ngay
    if app.state.manager.push(device.id, command_frame(cmd)):
        cmd.status = "sent"
        db.commit()
    return cmd


def command_frame(cmd: Command) -> dict:
    return {"id": cmd.id, "action": cmd.action, "minutes": cmd.minutes}


def pending_commands(db: Session, device: Device) -> list[Command]:
    cutoff = now()
    rows = db.scalars(select(Command).where(Command.device_id == device.id, Command.status != "acked").order_by(Command.id))
    return [c for c in rows if cutoff - c.created_at <= CMD_TTL.get(c.action, 600)]


def wipe_child_data(app, db: Session, child: Child, actor: str, ip: str):
    db.execute(delete(Event).where(Event.child_id == child.id))
    db.execute(delete(DailyUsage).where(DailyUsage.child_id == child.id))
    db.execute(delete(ChildRequest).where(ChildRequest.child_id == child.id))
    audit(db, actor, ip, child.id, "wipe_child_data", "events,usage,requests", "", "deleted")
    db.commit()
    for dev in active_devices(db, child.id):
        create_command(app, db, dev, "WIPE_LOCAL", 0, actor)


# ---------------------------------------------------------------- báo cáo
def local_today(tz_min: int = 420) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=tz_min)


def usage_by_day(db: Session, child_id: int, days: int = 7, tz_min: int = 420) -> list[dict]:
    today = local_today(tz_min).date()
    want = [(today - timedelta(days=i)) for i in range(days - 1, -1, -1)]
    rows = db.execute(select(DailyUsage.day, func.sum(DailyUsage.seconds)).where(
        DailyUsage.child_id == child_id, DailyUsage.day >= want[0].isoformat()).group_by(DailyUsage.day)).all()
    m = {d: int(s or 0) for d, s in rows}
    return [{"day": d.isoformat(), "label": ["T2", "T3", "T4", "T5", "T6", "T7", "CN"][d.weekday()], "seconds": m.get(d.isoformat(), 0)} for d in want]


def top_subjects(db: Session, child_id: int, etype: str, days: int = 7, limit: int = 10) -> list[dict]:
    since = now() - days * 86400
    rows = db.execute(select(Event.subject, func.sum(Event.duration_sec), func.count()).where(
        Event.child_id == child_id, Event.type == etype, Event.ts >= since).group_by(Event.subject)
        .order_by(func.sum(Event.duration_sec).desc(), func.count().desc()).limit(limit)).all()
    return [{"subject": s, "seconds": int(d or 0), "count": c} for s, d, c in rows]


def blocked_summary(db: Session, child_id: int, days: int = 7) -> dict:
    since = now() - days * 86400
    out = {}
    for t in ("blocked_app", "blocked_domain"):
        out[t] = db.scalar(select(func.count()).select_from(Event).where(Event.child_id == child_id, Event.type == t, Event.ts >= since)) or 0
    out["top"] = top_subjects_blocked(db, child_id, since)
    return out


def top_subjects_blocked(db: Session, child_id: int, since: int, limit: int = 5):
    rows = db.execute(select(Event.subject, func.count()).where(Event.child_id == child_id, Event.type.in_(("blocked_app", "blocked_domain")),
                      Event.ts >= since).group_by(Event.subject).order_by(func.count().desc()).limit(limit)).all()
    return [{"subject": s, "count": c} for s, c in rows]


def recent_events(db: Session, child_id: int, limit: int = 20) -> list[Event]:
    types = ("quota_warning", "locked", "override_granted", "unlock_request")
    return list(db.scalars(select(Event).where(Event.child_id == child_id, Event.type.in_(types)).order_by(Event.ts.desc(), Event.id.desc()).limit(limit)))


def child_report(db: Session, child_id: int, days: int = 7) -> dict:
    return {"usage": usage_by_day(db, child_id, days), "apps": top_subjects(db, child_id, "app_stop", days),
            "domains": top_subjects(db, child_id, "domain_query", days), "blocked": blocked_summary(db, child_id, days)}


# ---------------------------------------------------------------- lưu trữ 90 ngày
def purge_old(db: Session, settings) -> dict:
    t = now()
    cut = t - settings.retention_days * 86400
    day_cut = (local_today().date() - timedelta(days=settings.retention_days)).isoformat()
    res = {
        "events": db.execute(delete(Event).where(Event.ts < cut)).rowcount,
        "usage": db.execute(delete(DailyUsage).where(DailyUsage.day < day_cut)).rowcount,
        "requests": db.execute(delete(ChildRequest).where(ChildRequest.created_at < cut)).rowcount,
        "audit": db.execute(delete(AuditLog).where(AuditLog.ts < t - settings.audit_retention_days * 86400)).rowcount,
        "sessions": db.execute(delete(WebSession).where(WebSession.expires_at < t)).rowcount,
        "codes": db.execute(delete(EnrollCode).where(EnrollCode.expires_at < t - 86400)).rowcount,
        "commands": db.execute(delete(Command).where(Command.created_at < cut)).rowcount,
    }
    db.commit()
    return res
