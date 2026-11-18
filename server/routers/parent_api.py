"""API JSON cho bảng điều khiển phụ huynh. Mọi truy vấn đều lọc theo quyền sở hữu (chống IDOR)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import services
from ..deps import client_ip, csrf_check, get_db, owned_child, owned_device, require_role
from ..models import Child, ChildRequest, Device, EnrollCode, User, now
from common.installcode import make_install_code
from ..security import hash_password, new_enroll_code, password_problem, sha256hex

router = APIRouter(prefix="/api/parent")
parent_only = Depends(require_role("parent"))


class ChildIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    age: int = Field(default=9, ge=3, le=17)


class CommandIn(BaseModel):
    action: str
    minutes: int = Field(default=15, ge=1, le=240)   # ADD_TIME: số phút cộng thêm; LOCK_NOW: khóa trong bao lâu (tự mở)
    device_id: str | None = None


class DecideIn(BaseModel):
    approve: bool
    minutes: int | None = Field(default=None, ge=1, le=240)
    response: str = Field(default="", max_length=300)


class ViewerIn(BaseModel):
    username: str = Field(min_length=3, max_length=60)
    password: str


def _child_status(request: Request, db: Session, c: Child) -> dict:
    devs = []
    for d in db.scalars(select(Device).where(Device.child_id == c.id, Device.status == "active")):
        devs.append({"id": d.id, "name": d.name, "online": services.is_online(request.app, d), "state": d.state, "ui_ok": d.ui_ok,
                     "used_today_sec": d.used_today_sec, "remaining_sec": d.remaining_sec, "last_seen": d.last_seen,
                     "policy_ok": d.policy_applied == services.get_policy_row(db, c).version})
    pend = db.scalar(select(func.count()).select_from(ChildRequest).where(
        ChildRequest.child_id == c.id, ChildRequest.status == "pending")) or 0
    return {"id": c.id, "name": c.name, "age": c.age, "devices": devs, "pending_requests": pend}


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
        sent += cmd.status == "sent"
    services.audit(db, user.email, client_ip(request), child.id, f"command_{body.action}", "", "", str(body.minutes))
    db.commit()
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
    child = owned_child(db, user, r.child_id)  # IDOR: yêu cầu phải thuộc con của phụ huynh này
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
    return {"status": r.status}


@router.get("/children/{cid}/requests")
def list_requests(cid: int, user: User = parent_only, db: Session = Depends(get_db)):
    child = owned_child(db, user, cid)
    rows = db.scalars(select(ChildRequest).where(ChildRequest.child_id == child.id).order_by(ChildRequest.id.desc()).limit(50))
    return [{"id": r.id, "kind": r.kind, "subject": r.subject, "minutes": r.minutes, "message": r.message,
             "status": r.status, "created_at": r.created_at} for r in rows]


@router.delete("/children/{cid}/data", dependencies=[Depends(csrf_check)])
def delete_data(cid: int, request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    child = owned_child(db, user, cid)
    services.wipe_child_data(request.app, db, child, user.email, client_ip(request))
    return {"ok": True}


@router.post("/devices/{did}/revoke", dependencies=[Depends(csrf_check)])
def revoke(did: str, request: Request, user: User = parent_only, db: Session = Depends(get_db)):
    dev = owned_device(db, user, did)
    dev.status, dev.refresh_hash = "revoked", ""
    services.audit(db, user.email, client_ip(request), dev.child_id, "device_revoked", "device", dev.id, "revoked")
    db.commit()
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
