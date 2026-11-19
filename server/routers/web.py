"""API JSON cho giao diện web React: đăng nhập/đăng ký, dữ liệu trang phụ huynh, trang của trẻ, quản trị.
Phiên = cookie HttpOnly (khi giao diện chạy cùng máy chủ) hoặc Bearer token (khi giao diện chạy trên Netlify)."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
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
ACTION_VI = {"policy_change": "Đổi luật", "child_created": "Thêm hồ sơ con", "consent_and_enroll_code": "Đồng ý & tạo mã ghép đôi",
             "device_enrolled": "Ghép đôi thiết bị", "device_revoked": "Gỡ thiết bị", "request_decided": "Phản hồi yêu cầu của con",
             "wipe_child_data": "Xóa toàn bộ dữ liệu hoạt động", "viewer_created": "Tạo tài khoản xem cho con",
             "child_deleted": "Xóa hồ sơ con", "command_LOCK_NOW": "Khóa máy ngay", "command_UNLOCK": "Mở khóa", "command_ADD_TIME": "Cộng thêm giờ", "login_locked": "Khóa tài khoản tạm thời"}


def event_vi(e) -> str:
    sub = e.subject or ""
    if e.type == "quota_warning":
        if sub.startswith("clock_tamper"):
            return f"Đồng hồ máy bị chỉnh lệch ({sub.split(':', 1)[-1]}); giờ dùng vẫn tính theo đồng hồ phần cứng"
        if sub.startswith("grace"):
            return "Hết giờ: máy cho 60 giây ân hạn để lưu bài"
        return f"Cảnh báo còn khoảng {sub.replace('min', '')} phút"
    if e.type == "locked":
        return "Máy bị khóa: " + {"manual": "phụ huynh bấm khóa", "quota": "hết hạn mức", "schedule": "ngoài khung giờ"}.get(sub, sub)
    if e.type == "override_granted":
        return {"parent_add_time": "Phụ huynh cộng thêm giờ", "learn_to_play": "Con đổi giờ học lấy giờ chơi"}.get(sub, sub) + (f" ({e.duration_sec // 60} phút)" if e.duration_sec else "")
    return "Con gửi yêu cầu" + (f": {sub}" if sub else "")



def _json_only(request: Request):
    """Chỉ nhận JSON: biểu mẫu HTML của trang lạ không gửi được, còn fetch khác nguồn phải qua CORS preflight."""
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Cần gửi JSON.")


class AuthIn(BaseModel):
    login: str = Field(max_length=255)
    password: str = Field(max_length=200)
    name: str = Field(default="", max_length=100)
    token: bool = False      # True: trả Bearer token (giao diện ở tên miền khác, ví dụ Netlify) thay cho cookie


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
    generic = "Tài khoản hoặc mật khẩu không đúng, hoặc tài khoản đang bị khóa tạm thời."
    if not request.app.state.rl_login.allow(ip):
        raise HTTPException(429, "Quá nhiều lần thử từ địa chỉ này. Vui lòng đợi một phút.")
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
        raise HTTPException(429, "Quá nhiều lần thử từ địa chỉ này. Vui lòng đợi một phút.")
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
        db.delete(s)  # hủy phiên phía máy chủ
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
    """Khởi tạo một lần cho trình cài đặt 'cả hai trên một máy': chỉ nhận từ chính máy này và chỉ khi chưa có tài khoản nào."""
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
    """Phụ huynh tải bộ cài ngay từ máy chủ để cài lên máy của con (khỏi cần USB hay tìm trên mạng)."""
    d = request.app.state.settings.data_dir
    f = (Path(d) / "downloads" / "OGK-Setup.exe") if d else None
    if not f or not f.exists():
        raise HTTPException(404, "Máy chủ chưa có bộ cài để chia sẻ. Hãy chạy OGK-Setup.exe từ máy của con.")
    return FileResponse(f, filename="OGK-Setup.exe", media_type="application/octet-stream")
