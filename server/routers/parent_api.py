"""API JSON cho bảng điều khiển phụ huynh. Mọi truy vấn đều lọc theo quyền sở hữu (chống IDOR)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import services
from ..deps import client_ip, csrf_check, get_db, owned_child, owned_device, require_role
from ..models import Child, ChildRequest, ChildTask, Device, EnrollCode, User, now
from common.installcode import make_install_code
from ..security import hash_password, new_enroll_code, password_problem, sha256hex

router = APIRouter(prefix="/api/parent")
parent_only = Depends(require_role("parent"))


class ChildIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    age: int = Field(default=9, ge=3, le=17)


class CommandIn(BaseModel):
    action: str
    minutes: int = Field(default=15, ge=1, le=240)
    device_id: str | None = None


class DecideIn(BaseModel):
    approve: bool
    minutes: int | None = Field(default=None, ge=1, le=240)
    response: str = Field(default="", max_length=300)


class ViewerIn(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    reward_minutes: int = Field(default=15, ge=1, le=180)


class TaskDecideIn(BaseModel):
    approve: bool


def _emit(request: Request, parent_id: int | None, child_id: int | None, event_type: str, **extra):
    hub = getattr(request.app.state, "parent_hub", None)
    if hub is not None:
        hub.notify({"type": event_type, "child_id": child_id, "ts": now(), **extra}, parent_id=parent_id, child_id=child_id)


def _child_status(request: Request, db: Session, c: Child) -> dict:
    devs = []
    pol_ver = services.get_policy_row(db, c).version
    for d in db.scalars(select(Device).where(Device.child_id == c.id, Device.status == "active")):
        devs.append({"id": d.id, "name": d.name, "online": services.is_online(request.app, d), "state": d.state, "ui_ok": d.ui_ok,
                     "used_today_sec": d.used_today_sec, "remaining_sec": d.remaining_sec, "last_seen": d.last_seen,
                     "policy_ok": d.policy_applied == pol_ver})
    pend_rows = list(db.scalars(select(ChildRequest).where(
        ChildRequest.child_id == c.id, ChildRequest.status == "pending").order_by(ChildRequest.id.desc()).limit(10)))
    pend = db.scalar(select(func.count()).select_from(ChildRequest).where(
        ChildRequest.child_id == c.id, ChildRequest.status == "pending")) or 0
    return {
        "id": c.id, "name": c.name, "age": c.age, "devices": devs, "pending_requests": pend,
        "pending_items": [{"id": r.id, "kind": r.kind, "subject": r.subject, "minutes": r.minutes,
                           "message": r.message or "", "created_at": r.created_at} for r in pend_rows],
    }


@router.get("/status")
def status(request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    kids = db.scalars(select(Child).where(Child.parent_id == user.id).order_by(Child.id))
    return {"children": [_child_status(request, db, c) for c in kids], "server_time": now()}


@router.post("/children", dependencies=[Depends(csrf_check)])
def add_child(body: ChildIn, request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    c = Child(parent_id=user.id, name=body.name.strip(), age=body.age)
    db.add(c)
    db.flush()
    services.get_policy_row(db, c)
    services.audit(db, user.email, client_ip(request), c.id, "child_created", "child", "", c.name)
    db.commit()
    _emit(request, user.id, c.id, "child_created")
    return {"id": c.id}


@router.post("/children/{cid}/enroll-code", dependencies=[Depends(csrf_check)])
def enroll_code(cid: int, request: Request, consent: bool = False, user: User = parent_only, db: Session = Depends(get_db)):
    """Ghi nhận sự đồng ý của phụ huynh (Nghị định 13/2023) tại thời điểm tạo mã."""
    child = owned_child(db, user, cid)
    if not consent:
        raise HTTPException(422, "Cần xác nhận đồng ý trước khi tạo mã ghép đôi.")
    code = new_enroll_code()
    t = now()
    db.add(EnrollCode(code_hash=sha256hex(code), user_id=user.id, child_id=child.id,
                      expires_at=t + request.app.state.settings.enroll_ttl_min * 60, consent_at=t))
    services.audit(db, user.email, client_ip(request), child.id, "consent_and_enroll_code", "consent", "", "đã đồng ý")
    db.commit()
    return {"code": code, "install_code": make_install_code(code, request.app.state.settings.cert_fingerprint),
            "expires_at": t + request.app.state.settings.enroll_ttl_min * 60}


def _cmd_minutes(body: CommandIn) -> int:
    if body.action == "LOCK_NOW":
        return body.minutes if "minutes" in body.model_fields_set else 60
    if body.action == "UNLOCK":
        return body.minutes if "minutes" in body.model_fields_set else 15
    return body.minutes if body.action == "ADD_TIME" else 0


@router.post("/children/{cid}/command", dependencies=[Depends(csrf_check)])
def command(cid: int, body: CommandIn, request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    if body.action not in ("LOCK_NOW", "UNLOCK", "ADD_TIME"):
        raise HTTPException(422, "Lệnh không hợp lệ.")
    child = owned_child(db, user, cid)
    devs = services.active_devices(db, child.id)
    if body.device_id:
        devs = [d for d in devs if d.id == body.device_id]
    if not devs:
        raise HTTPException(404, "Chưa có thiết bị nào được ghép đôi.")
    sent = 0
    for d in devs:
        cmd = services.create_command(request.app, db, d, body.action, _cmd_minutes(body), user.email)
        sent += bool(getattr(cmd, "delivered", False))
    services.audit(db, user.email, client_ip(request), child.id, f"command_{body.action}", "", "", str(body.minutes))
    db.commit()
    _emit(request, user.id, child.id, "command_sent", action=body.action)
    return {"devices": len(devs), "delivered_now": sent}


@router.put("/children/{cid}/policy", dependencies=[Depends(csrf_check)])
def put_policy(cid: int, data: dict, request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    child = owned_child(db, user, cid)
    row = services.get_policy_row(db, child)
    try:
        new = services.validate_policy_update(json.loads(row.doc), data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    row = services.save_policy(request.app, db, child, new, user.email, client_ip(request))
    db.commit()
    _emit(request, user.id, child.id, "policy_updated", version=row.version)
    return {"version": row.version}


@router.get("/children/{cid}/policy")
def get_policy(cid: int, user: User = parent_only, db: Session = Depends(get_db)):
    child = owned_child(db, user, cid)
    row = services.get_policy_row(db, child)
    db.commit()
    return {"version": row.version, "policy": json.loads(row.doc)}


@router.get("/children/{cid}/report")
def report(cid: int, days: int = 7, user: User = parent_only, db: Session = Depends(get_db)):
    child = owned_child(db, user, cid)
    return services.child_report(db, child.id, max(1, min(days, 90)))


@router.post("/requests/{rid}/decide", dependencies=[Depends(csrf_check)])
def decide(rid: int, body: DecideIn, request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    r = db.get(ChildRequest, rid)
    if not r:
        raise HTTPException(404, "Không tìm thấy.")
    child = owned_child(db, user, r.child_id)
    if r.status != "pending":
        raise HTTPException(409, "Yêu cầu này đã được xử lý.")
    ip = client_ip(request)
    r.status = "approved" if body.approve else "denied"
    r.response, r.decided_at, r.decided_by = body.response or None, now(), user.email
    if body.approve and r.kind == "add-time":
        mins = body.minutes or r.minutes or 15
        for d in services.active_devices(db, child.id):
            services.create_command(request.app, db, d, "ADD_TIME", mins, user.email)
    if body.approve and r.kind == "false-block" and r.subject:
        row = services.get_policy_row(db, child)
        cur = json.loads(row.doc)
        new = json.loads(row.doc)
        if r.subject not in new["domains"]["allowed"]:
            new["domains"]["allowed"].append(r.subject)
        new = services.validate_policy_update(cur, {"domains": new["domains"]})
        services.save_policy(request.app, db, child, new, user.email, ip)
    services.audit(db, user.email, ip, child.id, "request_decided", r.kind, "pending", r.status)
    db.commit()
    _emit(request, user.id, child.id, "request_decided", request_id=r.id, status=r.status)
    return {"status": r.status}


@router.get("/children/{cid}/requests")
def list_requests(cid: int, user: User = parent_only, db: Session = Depends(get_db)):
    child = owned_child(db, user, cid)
    rows = db.scalars(select(ChildRequest).where(ChildRequest.child_id == child.id).order_by(ChildRequest.id.desc()).limit(50))
    return [{"id": r.id, "kind": r.kind, "subject": r.subject, "minutes": r.minutes, "message": r.message,
             "status": r.status, "created_at": r.created_at} for r in rows]


# ---- Nhiệm vụ thưởng giờ (F1 - CÓ THÌ TỐT)
@router.post("/children/{cid}/tasks", dependencies=[Depends(csrf_check)])
def create_task(cid: int, body: TaskIn, request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    child = owned_child(db, user, cid)
    t = ChildTask(child_id=child.id, title=body.title.strip(), reward_minutes=body.reward_minutes, created_by=user.email)
    db.add(t)
    db.flush()
    services.audit(db, user.email, client_ip(request), child.id, "task_created", "task", "", f"{t.title} (+{t.reward_minutes}p)")
    db.commit()
    _emit(request, user.id, child.id, "task_updated", task_id=t.id)
    return {"id": t.id, "title": t.title, "reward_minutes": t.reward_minutes, "status": t.status}


@router.get("/children/{cid}/tasks")
def list_tasks(cid: int, user: User = parent_only, db: Session = Depends(get_db)):
    child = owned_child(db, user, cid)
    rows = db.scalars(select(ChildTask).where(ChildTask.child_id == child.id).order_by(ChildTask.id.desc()).limit(50))
    return [{"id": t.id, "title": t.title, "reward_minutes": t.reward_minutes, "status": t.status,
             "child_note": t.child_note, "created_at": t.created_at} for t in rows]


@router.post("/tasks/{tid}/decide", dependencies=[Depends(csrf_check)])
def decide_task(tid: int, body: TaskDecideIn, request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    t = db.get(ChildTask, tid)
    if not t:
        raise HTTPException(404, "Không tìm thấy nhiệm vụ.")
    child = owned_child(db, user, t.child_id)
    if t.status in ("approved", "rejected"):
        raise HTTPException(409, "Nhiệm vụ này đã được chốt kết quả.")
    t.status = "approved" if body.approve else "rejected"
    t.decided_at = now()
    if body.approve:
        for d in services.active_devices(db, child.id):
            services.create_command(request.app, db, d, "ADD_TIME", t.reward_minutes, user.email)
    services.audit(db, user.email, client_ip(request), child.id, "task_decided", t.title, "submitted", f"{t.status} (+{t.reward_minutes}p)")
    db.commit()
    _emit(request, user.id, child.id, "task_updated", task_id=t.id, status=t.status)
    return {"status": t.status, "reward_minutes": t.reward_minutes if body.approve else 0}


@router.delete("/children/{cid}/data", dependencies=[Depends(csrf_check)])
def delete_data(cid: int, request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    child = owned_child(db, user, cid)
    services.wipe_child_data(request.app, db, child, user.email, client_ip(request))
    _emit(request, user.id, child.id, "data_wiped")
    return {"ok": True}


@router.delete("/children/{cid}", dependencies=[Depends(csrf_check)])
def delete_child(cid: int, request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    """Xóa hẳn hồ sơ của con (khác với 'xóa dữ liệu' chỉ dọn hoạt động nhưng giữ hồ sơ)."""
    child = owned_child(db, user, cid)
    services.delete_child(request.app, db, child, user.email, client_ip(request))
    _emit(request, user.id, cid, "child_deleted")
    return {"ok": True}


@router.post("/devices/{did}/revoke", dependencies=[Depends(csrf_check)])
def revoke(did: str, request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    dev = owned_device(db, user, did)
    dev.status, dev.refresh_hash = "revoked", ""
    services.audit(db, user.email, client_ip(request), dev.child_id, "device_revoked", "device", dev.id, "revoked")
    db.commit()
    request.app.state.manager.kick(dev.id)
    _emit(request, user.id, dev.child_id, "device_revoked", device_id=dev.id)
    return {"ok": True}


@router.post("/children/{cid}/viewer", dependencies=[Depends(csrf_check)])
def create_viewer(cid: int, body: ViewerIn, request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    """Tạo tài khoản child_view để trẻ xem cùng bộ báo cáo và nhật ký luật (NT5)."""
    child = owned_child(db, user, cid)
    prob = password_problem(body.password)
    if prob:
        raise HTTPException(422, prob)
    email = body.username.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(409, "Tên đăng nhập đã tồn tại.")
    db.add(User(email=email, display_name=child.name, pw_hash=hash_password(body.password), role="child_view",
                child_id=child.id, owner_id=user.id))
    services.audit(db, user.email, client_ip(request), child.id, "viewer_created", "viewer", "", email)
    db.commit()
    return {"ok": True}


@router.post("/server/stop", dependencies=[Depends(csrf_check)])
def stop_server(request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    """Dừng dịch vụ máy chủ bảng điều khiển trên máy bố mẹ (chỉ cho phép khi đang truy cập từ chính máy chủ đó)."""
    host = (request.client.host if request.client else "").strip().lower()
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "Chỉ có thể tắt dịch vụ máy chủ trực tiếp trên máy tính của bố mẹ (localhost).")
    services.audit(db, user.email, client_ip(request), None, "server_stop_requested", "server", "running", "stopping")
    db.commit()
    _emit(request, user.id, None, "server_stopping")

    import os, signal, subprocess, sys, threading
    def _do_stop():
        import time
        time.sleep(0.4)
        if sys.platform == "win32":
            try:
                subprocess.run(["sc.exe", "stop", "OpenGuardKidsServer"], capture_output=True, creationflags=0x08000000, timeout=10)
            except Exception:
                pass
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            os._exit(0)

    threading.Thread(target=_do_stop, daemon=True).start()
    return {"ok": True, "message": "Máy chủ bảng điều khiển đang dừng lại. Bấm vào lối tắt ngoài Desktop để bật lại khi cần."}
