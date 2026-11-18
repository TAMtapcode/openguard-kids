"""API cho Agent trên máy trẻ: ghép đôi, token, heartbeat, chính sách, sự kiện, yêu cầu, WebSocket."""
from __future__ import annotations

import base64
import json
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from common.schema import EVENT_TYPES, REQUEST_KINDS, is_valid_subject

from .. import services
from ..deps import client_ip, current_device, get_db
from ..models import (AuditLog, Child, ChildRequest, Command, DailyUsage, Device, EnrollCode, Event, now)
from ..security import decode_access_token, make_access_token, new_token, sha256hex

router = APIRouter()


class EnrollIn(BaseModel):
    code: str = Field(min_length=8, max_length=8)
    fingerprint: str = Field(max_length=200)
    device_name: str = Field(default="", max_length=100)
    agent_version: str = Field(default="", max_length=20)


class RefreshIn(BaseModel):
    device_id: str
    refresh_token: str


class HeartbeatIn(BaseModel):
    policy_version: int = 0
    day: str = Field(max_length=10)
    used_today_sec: int = Field(ge=0, le=86400 * 2)
    remaining_sec: int = Field(default=0, ge=-86400, le=86400 * 400)
    group_usage: dict[str, int] = {}
    state: str = Field(default="ok", max_length=12)
    ui_ok: bool = True
    agent_version: str = Field(default="", max_length=20)


class EventIn(BaseModel):
    idem_key: str = Field(min_length=8, max_length=36)
    ts: int
    type: str
    subject: str = Field(default="", max_length=120)
    duration_sec: int = Field(default=0, ge=0, le=86400)
    policy_id: int = 0


class EventsIn(BaseModel):
    events: list[EventIn] = Field(max_length=500)


class RequestIn(BaseModel):
    subject: str = Field(default="", max_length=120)
    minutes: int = Field(default=0, ge=0, le=240)
    message: str = Field(default="", max_length=500)


def _issue_tokens(request: Request, dev: Device) -> dict:
    s = request.app.state.settings
    refresh = new_token(32)
    dev.refresh_hash = sha256hex(refresh)
    return {"access_token": make_access_token(dev.id, s.jwt_secret, s.access_ttl_min), "refresh_token": refresh,
            "expires_in": s.access_ttl_min * 60}


@router.post("/api/v1/enrollment")
def enroll(body: EnrollIn, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if not request.app.state.rl_enroll.allow(ip):
        raise HTTPException(429, "Thử quá nhiều lần. Vui lòng đợi một phút.")
    code = db.get(EnrollCode, sha256hex(body.code.strip().upper()))
    if not code or code.used or code.expires_at < now():
        raise HTTPException(400, "Mã ghép đôi không đúng hoặc đã hết hạn (hiệu lực 10 phút).")
    code.used = True
    dev = Device(id=str(uuid.uuid4()), child_id=code.child_id, name=body.device_name or "Máy của con",
                 fingerprint_hash=sha256hex(body.fingerprint), policy_key=os.urandom(32),
                 consent_by=code.user_id, consent_at=code.consent_at, agent_version=body.agent_version, last_seen=now())
    db.add(dev)
    tokens = _issue_tokens(request, dev)
    child = db.get(Child, code.child_id)
    services.get_policy_row(db, child)
    services.audit(db, f"thiết bị {dev.name}", ip, child.id, "device_enrolled", "device", "", dev.id)
    db.commit()
    return {"device_id": dev.id, "child_name": child.name, "child_id": child.id, "server_time": now(),
            "policy_key": base64.b64encode(dev.policy_key).decode(), **tokens}


@router.post("/api/v1/token/refresh")
def refresh(body: RefreshIn, request: Request, db: Session = Depends(get_db)):
    dev = db.get(Device, body.device_id)
    if not dev or dev.status != "active" or not dev.refresh_hash or dev.refresh_hash != sha256hex(body.refresh_token):
        raise HTTPException(401, "Refresh token không hợp lệ hoặc thiết bị đã bị gỡ.")
    tokens = _issue_tokens(request, dev)  # xoay vòng refresh token
    db.commit()
    return tokens


@router.post("/api/v1/heartbeat")
def heartbeat(body: HeartbeatIn, request: Request, dev: Device = Depends(current_device), db: Session = Depends(get_db)):
    dev.last_seen = now()
    dev.state, dev.ui_ok = body.state, body.ui_ok
    dev.used_today_sec, dev.remaining_sec = body.used_today_sec, body.remaining_sec
    dev.policy_applied = body.policy_version
    if body.agent_version:
        dev.agent_version = body.agent_version
    row = services.get_policy_row(db, db.get(Child, dev.child_id))
    du = db.get(DailyUsage, (dev.id, body.day))
    if du is None:
        du = DailyUsage(device_id=dev.id, day=body.day, child_id=dev.child_id, seconds=0)
        db.add(du)
    du.seconds = max(du.seconds or 0, body.used_today_sec)
    du.groups = json.dumps(body.group_usage)
    cmds = [services.command_frame(c) for c in services.pending_commands(db, dev)]
    db.commit()
    return {"server_time": now(), "policy_version": row.version, "commands": cmds}


@router.get("/api/v1/policy")
def get_policy(request: Request, dev: Device = Depends(current_device), db: Session = Depends(get_db)):
    return services.policy_envelope(request.app, db, dev)


@router.post("/api/v1/events/sync")
def events_sync(body: EventsIn, dev: Device = Depends(current_device), db: Session = Depends(get_db)):
    accepted = dup = rejected = 0
    for e in body.events:
        if e.type not in EVENT_TYPES or (e.subject and not is_valid_subject(e.subject)):
            rejected += 1  # NT2: không nhận URL đầy đủ / loại sự kiện ngoài lược đồ
            continue
        try:
            with db.begin_nested():
                db.add(Event(idem_key=e.idem_key, ts=e.ts, device_id=dev.id, child_id=dev.child_id, type=e.type,
                             subject=e.subject, duration_sec=e.duration_sec, policy_id=e.policy_id))
            accepted += 1
        except IntegrityError:
            dup += 1  # khử trùng bằng idempotency_key
    db.commit()
    return {"accepted": accepted, "duplicates": dup, "rejected": rejected}


@router.post("/api/v1/requests/{kind}")
def add_request(kind: str, body: RequestIn, dev: Device = Depends(current_device), db: Session = Depends(get_db)):
    if kind not in REQUEST_KINDS:
        raise HTTPException(404, "Loại yêu cầu không tồn tại.")
    if body.subject and not is_valid_subject(body.subject):
        raise HTTPException(422, "Chỉ nhập tên miền hoặc tên ứng dụng, không nhập URL đầy đủ.")
    if kind == "false-block" and not body.subject:
        raise HTTPException(422, "Cần cho biết mục nào bị chặn nhầm.")
    open_n = db.scalar(select(ChildRequest.id).where(ChildRequest.device_id == dev.id, ChildRequest.status == "pending",
                                                     ChildRequest.created_at > now() - 60))
    if open_n:
        raise HTTPException(429, "Con vừa gửi yêu cầu xong, hãy đợi bố mẹ phản hồi nhé.")
    r = ChildRequest(child_id=dev.child_id, device_id=dev.id, kind=kind, subject=body.subject,
                     minutes=body.minutes or (15 if kind == "add-time" else 0), message=body.message or None)
    db.add(r)
    db.add(Event(idem_key=str(uuid.uuid4()), ts=now(), device_id=dev.id, child_id=dev.child_id, type="unlock_request",
                 subject=body.subject or kind, duration_sec=0, policy_id=dev.policy_applied))
    db.commit()
    return {"id": r.id, "status": r.status}


@router.get("/api/v1/requests")
def my_requests(dev: Device = Depends(current_device), db: Session = Depends(get_db)):
    rows = db.scalars(select(ChildRequest).where(ChildRequest.child_id == dev.child_id).order_by(ChildRequest.id.desc()).limit(20))
    return [{"id": r.id, "kind": r.kind, "subject": r.subject, "minutes": r.minutes, "status": r.status,
             "response": r.response, "created_at": r.created_at, "decided_at": r.decided_at} for r in rows]


@router.get("/api/v1/audit")
def my_audit(dev: Device = Depends(current_device), db: Session = Depends(get_db)):
    """NT5 - trẻ xem được nhật ký thay đổi luật của chính mình."""
    return services_audit(db, dev.child_id)


def services_audit(db: Session, child_id: int, limit: int = 100):
    rows = db.scalars(select(AuditLog).where(AuditLog.child_id == child_id).order_by(AuditLog.id.desc()).limit(limit))
    return [{"ts": a.ts, "actor": a.actor, "ip": a.actor_ip, "action": a.action, "field": a.field,
             "old": a.old_value, "new": a.new_value} for a in rows]


@router.post("/api/v1/commands/{cid}/ack")
def ack(cid: int, dev: Device = Depends(current_device), db: Session = Depends(get_db)):
    c = db.get(Command, cid)
    if not c or c.device_id != dev.id:
        raise HTTPException(404, "Không tìm thấy lệnh.")
    c.status, c.acked_at = "acked", now()
    db.commit()
    return {"ok": True}


@router.websocket("/ws/agent/{device_id}")
async def agent_ws(ws: WebSocket, device_id: str):
    app = ws.app
    auth = ws.headers.get("authorization", "")
    dev_id = decode_access_token(auth[7:], app.state.settings.jwt_secret) if auth.lower().startswith("bearer ") else None
    if dev_id != device_id:
        await ws.close(code=4401)
        return

    def _check_and_pending():
        with app.state.SessionLocal() as db:
            dev = db.get(Device, device_id)
            if not dev or dev.status != "active":
                return None
            dev.last_seen = now()
            frames = [services.command_frame(c) for c in services.pending_commands(db, dev)]
            db.commit()
            return frames

    frames = await run_in_threadpool(_check_and_pending)
    if frames is None:
        await ws.close(code=4401)
        return
    await ws.accept()
    await app.state.manager.connect(device_id, ws)
    try:
        for f in frames:  # đẩy ngay các lệnh còn tồn đọng khi vừa kết nối
            await ws.send_json(f)
        while True:
            msg = await ws.receive_json()
            if "ack" in msg:
                def _ack(cid=int(msg["ack"])):
                    with app.state.SessionLocal() as db:
                        c = db.get(Command, cid)
                        if c and c.device_id == device_id:
                            c.status, c.acked_at = "acked", now()
                            db.commit()
                await run_in_threadpool(_ack)
            elif msg.get("ping"):
                await ws.send_json({"pong": now()})
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        app.state.manager.disconnect(device_id, ws)
