"""API JSON cho giao diện web React: đăng nhập/đăng ký, dữ liệu trang phụ huynh, trang của trẻ, quản trị.
Phiên = cookie HttpOnly (khi giao diện chạy cùng máy chủ) hoặc Bearer token (khi giao diện chạy trên Netlify)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.installcode import make_install_code
from common.schema import CATEGORIES

from .. import services
from ..deps import SESSION_COOKIE, client_ip, csrf_check, get_db, owned_child, require_role, web_session
from ..models import AuditLog, Child, ChildRequest, Device, EnrollCode, User, WebSession, now
from ..security import hash_password, new_enroll_code, login_problem, new_token, password_problem, sha256hex, verify_password

router = APIRouter()
ACTION_VI = {
    "policy_change": "Đổi quy định",
    "child_created": "Thêm hồ sơ của bé",
    "consent_and_enroll_code": "Xác nhận & tạo mã kết nối máy",
    "device_enrolled": "Đã kết nối máy của bé",
    "device_revoked": "Đã ngắt kết nối máy",
    "request_decided": "Trả lời lời nhắn của bé",
    "wipe_child_data": "Xóa nhật ký hoạt động",
    "viewer_created": "Tạo tài khoản xem cho bé",
    "child_deleted": "Xóa hồ sơ của bé",
    "command_LOCK_NOW": "Tạm khóa máy cho bé nghỉ",
    "command_UNLOCK": "Mở khóa cho bé dùng",
    "command_ADD_TIME": "Thưởng thêm giờ cho bé",
    "login_locked": "Khóa đăng nhập tạm thời (15 phút)",
}


def event_vi(e) -> str:
    sub = e.subject or ""
    if e.type == "quota_warning":
        if sub.startswith("clock_tamper"):
            return "Đồng hồ máy bị chỉnh lệch; máy vẫn tính đúng giờ thực tế"
        if sub.startswith("grace"):
            return "Hết giờ: máy cho 1 phút để bé lưu bài trước khi nghỉ"
        return f"Nhắc bé sắp hết giờ (còn khoảng {sub.replace('min', '')} phút)"
    if e.type == "locked":
        return "Máy tạm nghỉ: " + {
            "manual": "bố mẹ bấm tạm khóa",
            "quota": "hết giờ dùng trong ngày",
            "schedule": "ngoài khung giờ cho phép",
        }.get(sub, "tới giờ nghỉ")
    if e.type == "override_granted":
        mins = f" ({e.duration_sec // 60} phút)" if e.duration_sec else ""
        return {
            "parent_add_time": "Bố mẹ cho thêm giờ",
            "learn_to_play": "Bé đổi giờ chăm học lấy giờ chơi",
            "parent_unlock": "Bố mẹ mở khóa cho bé dùng thêm",
        }.get(sub, "Bố mẹ cho thêm giờ") + mins
    return "Bé gửi lời nhắn cho bố mẹ" + (f" ({sub})" if sub and sub not in ("add-time", "false-block") else "")


def _json_only(request: Request):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Cần gửi JSON.")


class AuthIn(BaseModel):
    login: str = Field(max_length=255)
    password: str = Field(max_length=200)
    name: str = Field(default="", max_length=100)
    token: bool = False


def user_json(u: User) -> dict:
    return {"id": u.id, "login": u.email, "display_name": u.display_name or u.email, "role": u.role, "child_id": u.child_id}


def _start_session(request: Request, db: Session, user: User, want_token: bool, status: int = 200):
    s = request.app.state.settings
    tok, csrf = new_token(32), new_token(24)
    db.add(WebSession(token_hash=sha256hex(tok), user_id=user.id, csrf=csrf, created_at=now(), expires_at=now() + s.session_hours * 3600))
    db.commit()
    body = {"user": user_json(user), "csrf": csrf}
    if want_token:
        body["token"] = tok
    resp = JSONResponse(body, status_code=status)
    if not want_token:
        resp.set_cookie(SESSION_COOKIE, tok, httponly=True, secure=s.cookie_secure, samesite="lax", max_age=s.session_hours * 3600)
    return resp


@router.post("/api/auth/login", dependencies=[Depends(_json_only)])
def login(body: AuthIn, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    generic = "Tài khoản hoặc mật khẩu chưa đúng, hoặc đang tạm khóa đăng nhập."
    if not request.app.state.rl_login.allow(ip):
        raise HTTPException(429, "Thử quá nhiều lần. Vui lòng đợi một phút rồi thử lại.")
    user = db.scalar(select(User).where(User.email == body.login.strip().lower()))
    locked = bool(user and user.locked_until > now())
    ok = verify_password(user.pw_hash if user else None, body.password) and user is not None and not locked and not user.disabled
    if not ok:
        if user and not locked:
            user.failed_attempts += 1
            if user.failed_attempts >= request.app.state.settings.lockout_attempts:
                user.locked_until = now() + request.app.state.settings.lockout_minutes * 60
                user.failed_attempts = 0
                services.audit(db, user.email, ip, None, "login_locked", "account", "", "khóa 15 phút")
            db.commit()
        raise HTTPException(401, generic)
    user.failed_attempts = 0
    return _start_session(request, db, user, body.token)


@router.post("/api/auth/register", dependencies=[Depends(_json_only)])
def register(body: AuthIn, request: Request, db: Session = Depends(get_db)):
    if not request.app.state.rl_login.allow(client_ip(request)):
        raise HTTPException(429, "Thử quá nhiều lần. Vui lòng đợi một phút rồi thử lại.")
    login_ = body.login.strip().lower()
    err = login_problem(login_) or password_problem(body.password)
    if not err and db.scalar(select(User.id).where(User.email == login_)):
        err = "Tài khoản này đã được đăng ký."
    if err:
        raise HTTPException(422, err)
    user = User(email=login_, display_name=(body.name.strip() or login_.split("@")[0])[:100], pw_hash=hash_password(body.password), role="parent")
    db.add(user)
    db.commit()
    return _start_session(request, db, user, body.token, status=201)


@router.get("/api/auth/me")
def me(request: Request, user: User = Depends(web_session)):
    return {"user": user_json(user), "csrf": request.state.session.csrf}


@router.post("/api/auth/logout", dependencies=[Depends(csrf_check)])
def logout(request: Request, user: User = Depends(web_session), db: Session = Depends(get_db)):
    s = db.get(WebSession, request.state.session.token_hash)
    if s:
        db.delete(s)
        db.commit()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


def _req_json(r: ChildRequest) -> dict:
    return {"id": r.id, "kind": r.kind, "subject": r.subject, "minutes": r.minutes, "message": r.message or "", "status": r.status,
            "response": r.response or "", "created_at": r.created_at}


def _audit_json(a: AuditLog, with_ip: bool) -> dict:
    d = {"id": a.id, "ts": a.ts, "actor": a.actor, "action": ACTION_VI.get(a.action, a.action), "field": a.field or "",
         "old": a.old_value or "", "new": a.new_value or ""}
    if with_ip:
        d["ip"] = a.actor_ip
    return d


@router.get("/api/parent/children/{cid}/overview")
def child_overview(cid: int, request: Request, user: User = Depends(require_role("parent")), db: Session = Depends(get_db)):
    child = owned_child(db, user, cid)
    row = services.get_policy_row(db, child)
    db.commit()
    reqs = db.scalars(select(ChildRequest).where(ChildRequest.child_id == child.id).order_by(ChildRequest.id.desc()).limit(30))
    audit = db.scalars(select(AuditLog).where(AuditLog.child_id == child.id).order_by(AuditLog.id.desc()).limit(100))
    devs = db.scalars(select(Device).where(Device.child_id == child.id))
    return {"child": {"id": child.id, "name": child.name, "age": child.age}, "policy": json.loads(row.doc), "version": row.version,
            "report": services.child_report(db, child.id), "requests": [_req_json(r) for r in reqs],
            "audit": [_audit_json(a, True) for a in audit],
            "devices": [{"id": d.id, "name": d.name, "status": d.status, "enrolled_at": d.enrolled_at} for d in devs],
            "recent": [{"ts": e.ts, "text": event_vi(e)} for e in services.recent_events(db, child.id)],
            "categories": CATEGORIES, "lan_url": request.app.state.settings.public_url or str(request.base_url).rstrip("/")}


@router.get("/api/kid/overview")
def kid_overview(user: User = Depends(require_role("child_view")), db: Session = Depends(get_db)):
    child = owned_child(db, user, user.child_id or -1)
    audit = db.scalars(select(AuditLog).where(AuditLog.child_id == child.id, AuditLog.action.in_(
        ("policy_change", "request_decided", "command_ADD_TIME", "command_LOCK_NOW", "command_UNLOCK"))).order_by(AuditLog.id.desc()).limit(50))
    reqs = db.scalars(select(ChildRequest).where(ChildRequest.child_id == child.id).order_by(ChildRequest.id.desc()).limit(10))
    return {"child": {"id": child.id, "name": child.name}, "report": services.child_report(db, child.id),
            "audit": [_audit_json(a, False) for a in audit], "requests": [_req_json(r) for r in reqs]}


@router.get("/api/admin/overview")
def admin_overview(user: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.id))
    return {"users": [{**user_json(u), "disabled": u.disabled} for u in users],
            "stats": {"children": db.scalar(select(func.count()).select_from(Child)), "devices": db.scalar(select(func.count()).select_from(Device))}}


@router.post("/api/admin/users/{uid}/toggle", dependencies=[Depends(csrf_check)])
def admin_toggle(uid: int, user: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    target = db.get(User, uid)
    if not target or target.id == user.id:
        raise HTTPException(404, "Không tìm thấy.")
    target.disabled = not target.disabled
    db.commit()
    return {"disabled": target.disabled}


class LocalSetupIn(BaseModel):
    parent_name: str = Field(default="", max_length=100)
    email: str = Field(max_length=255)
    password: str = Field(max_length=200)
    child_name: str = Field(min_length=1, max_length=100)
    child_age: int = Field(default=9, ge=3, le=17)
    consent: bool = False


@router.post("/api/setup/local")
def local_setup(body: LocalSetupIn, request: Request, db: Session = Depends(get_db)):
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, "Chức năng này chỉ dùng được trực tiếp trên máy chủ.")
    if db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(409, "Máy chủ này đã được thiết lập. Hãy đăng nhập vào bảng điều khiển.")
    email = body.email.strip().lower()
    if login_problem(email):
        raise HTTPException(422, login_problem(email))
    if password_problem(body.password):
        raise HTTPException(422, password_problem(body.password))
    if not body.consent:
        raise HTTPException(422, "Cần xác nhận bạn là cha/mẹ hoặc người giám hộ và đồng ý trước khi cài đặt.")
    parent = User(email=email, display_name=body.parent_name.strip() or email.split("@")[0], pw_hash=hash_password(body.password), role="parent")
    db.add(parent)
    db.flush()
    child = Child(parent_id=parent.id, name=body.child_name.strip(), age=body.child_age)
    db.add(child)
    db.flush()
    services.get_policy_row(db, child)
    code, t = new_enroll_code(), now()
    st = request.app.state.settings
    db.add(EnrollCode(code_hash=sha256hex(code), user_id=parent.id, child_id=child.id, expires_at=t + st.enroll_ttl_min * 60, consent_at=t))
    services.audit(db, email, host, child.id, "consent_and_enroll_code", "consent", "", "đã đồng ý (cài đặt trên cùng máy)")
    db.commit()
    return {"code": code, "install_code": make_install_code(code, st.cert_fingerprint), "child_id": child.id}


@router.get("/download/OGK-Setup.exe")
def download_setup(request: Request):
    d = request.app.state.settings.data_dir
    f = (Path(d) / "downloads" / "OGK-Setup.exe") if d else None
    if not f or not f.exists():
        raise HTTPException(404, "Máy chủ chưa có bộ cài để chia sẻ. Hãy chạy OGK-Setup.exe từ máy của con.")
    return FileResponse(f, filename="OGK-Setup.exe", media_type="application/octet-stream")


@router.websocket("/ws/events")
async def web_events_ws(ws: WebSocket):
    app = ws.app
    tok = ws.query_params.get("token") or ""
    if not tok:
        auth = ws.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            tok = auth[7:].strip()
    if not tok:
        tok = ws.cookies.get(SESSION_COOKIE) or ""

    def _auth_user():
        if not tok:
            return None
        with app.state.SessionLocal() as db:
            sess = db.get(WebSession, sha256hex(tok))
            if not sess or sess.expires_at < now():
                return None
            u = db.get(User, sess.user_id)
            if not u or u.disabled:
                return None
            return (u.id, u.role, u.child_id)

    info = await run_in_threadpool(_auth_user)
    if not info:
        await ws.close(code=4401)
        return
    uid, role, cid = info
    parent_id = uid if role == "parent" else None
    child_id = cid if role == "child_view" else None
    await ws.accept()
    hub = getattr(app.state, "parent_hub", None)
    if hub is not None:
        await hub.connect(ws, parent_id=parent_id, child_id=child_id)
    try:
        await ws.send_json({"type": "connected", "ts": now()})
        while True:
            msg = await asyncio.wait_for(ws.receive_json(), timeout=75)
            if isinstance(msg, dict) and msg.get("ping"):
                await ws.send_json({"type": "pong", "ts": now()})
    except (WebSocketDisconnect, RuntimeError, ValueError, asyncio.TimeoutError):
        pass
    finally:
        if hub is not None:
            hub.disconnect(ws, parent_id=parent_id, child_id=child_id)
