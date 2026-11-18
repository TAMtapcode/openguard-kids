"""Trang web: đăng nhập/đăng ký, dashboard phụ huynh, trang xem của trẻ, quản trị."""
from __future__ import annotations

import json
import secrets
import hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.installcode import make_install_code
from common.schema import CATEGORIES

from .. import charts, services
from ..deps import SESSION_COOKIE, client_ip, csrf_check, get_db, owned_child, require_role, web_session
from ..models import AuditLog, Child, ChildRequest, Device, EnrollCode, User, WebSession, now
from ..security import hash_password, new_enroll_code, login_problem, new_token, password_problem, sha256hex, verify_password
from .agent_api import services_audit

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
VN = timezone(timedelta(hours=7))
templates.env.filters["dur"] = charts.fmt_duration
templates.env.filters["dt"] = lambda ts: datetime.fromtimestamp(ts, VN).strftime("%d/%m/%Y %H:%M") if ts else "—"
ACTION_VI = {"policy_change": "Đổi luật", "child_created": "Thêm hồ sơ con", "consent_and_enroll_code": "Đồng ý & tạo mã ghép đôi",
             "device_enrolled": "Ghép đôi thiết bị", "device_revoked": "Gỡ thiết bị", "request_decided": "Phản hồi yêu cầu của con",
             "wipe_child_data": "Xóa toàn bộ dữ liệu hoạt động", "viewer_created": "Tạo tài khoản xem cho con",
             "command_LOCK_NOW": "Khóa máy ngay", "command_UNLOCK": "Mở khóa", "command_ADD_TIME": "Cộng thêm giờ", "login_locked": "Khóa tài khoản tạm thời"}
templates.env.filters["action_vi"] = lambda a: ACTION_VI.get(a, a)
templates.env.globals["CATEGORIES"] = CATEGORIES


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


templates.env.filters["event_vi"] = event_vi


def render(request: Request, name: str, status: int = 200, **ctx):
    sess = getattr(request.state, "session", None)
    return templates.TemplateResponse(request, name, {"csrf": sess.csrf if sess else "", **ctx}, status_code=status)


def _pre_csrf_cookie(resp, request: Request, token: str):
    resp.set_cookie("ogk_pre", token, httponly=True, samesite="lax", secure=request.app.state.settings.cookie_secure, max_age=1800)


def _auth_form(request: Request, name: str, **ctx):
    token = secrets.token_urlsafe(24)
    resp = render(request, name, pre=token, **ctx)
    _pre_csrf_cookie(resp, request, token)
    return resp


def _check_pre(request: Request, csrf: str):
    if not hmac.compare_digest(request.cookies.get("ogk_pre", "x"), csrf or "y"):
        raise HTTPException(403, "Phiên biểu mẫu hết hạn, hãy tải lại trang.")


def _home_for(user: User) -> str:
    return {"parent": "/", "child_view": "/kid", "admin": "/admin"}[user.role]


def _start_session(request: Request, db: Session, user: User, target: str):
    s = request.app.state.settings
    tok = new_token(32)
    db.add(WebSession(token_hash=sha256hex(tok), user_id=user.id, csrf=new_token(24), created_at=now(),
                      expires_at=now() + s.session_hours * 3600))
    db.commit()
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(SESSION_COOKIE, tok, httponly=True, secure=s.cookie_secure, samesite="lax", max_age=s.session_hours * 3600)
    resp.delete_cookie("ogk_pre")
    return resp


@router.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return render(request, "privacy.html", user=None)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _auth_form(request, "login.html", error=None)


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), csrf: str = Form(""), db: Session = Depends(get_db)):
    _check_pre(request, csrf)
    ip = client_ip(request)
    generic = "Email hoặc mật khẩu không đúng, hoặc tài khoản đang bị khóa tạm thời."
    if not request.app.state.rl_login.allow(ip):
        return _auth_form(request, "login.html", status=429, error="Quá nhiều lần thử từ địa chỉ này. Vui lòng đợi một phút.")
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    locked = bool(user and user.locked_until > now())
    ok = verify_password(user.pw_hash if user else None, password) and user is not None and not locked and not user.disabled
    if not ok:
        if user and not locked:
            user.failed_attempts += 1
            if user.failed_attempts >= request.app.state.settings.lockout_attempts:
                user.locked_until = now() + request.app.state.settings.lockout_minutes * 60
                user.failed_attempts = 0
                services.audit(db, user.email, ip, None, "login_locked", "account", "", "khóa 15 phút")
            db.commit()
        return _auth_form(request, "login.html", status=401, error=generic)
    user.failed_attempts = 0
    return _start_session(request, db, user, _home_for(user))


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return _auth_form(request, "register.html", error=None)


@router.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...), name: str = Form(""), csrf: str = Form(""),
             db: Session = Depends(get_db)):
    _check_pre(request, csrf)
    email = email.strip().lower()
    err = login_problem(email) or password_problem(password)
    if not err and db.scalar(select(User.id).where(User.email == email)):
        err = "Tài khoản này đã được đăng ký."
    if err:
        return _auth_form(request, "register.html", status=422, error=err, email=email, display_name=name)
    user = User(email=email, display_name=(name.strip() or email.split("@")[0])[:100], pw_hash=hash_password(password), role="parent")
    db.add(user)
    db.commit()
    return _start_session(request, db, user, "/")


@router.post("/logout", dependencies=[Depends(csrf_check)])
def logout(request: Request, db: Session = Depends(get_db)):
    tok = request.cookies.get(SESSION_COOKIE)
    if tok:
        s = db.get(WebSession, sha256hex(tok))
        if s:
            db.delete(s)  # hủy phiên phía máy chủ
            db.commit()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(web_session)):
    if user.role != "parent":
        return RedirectResponse(_home_for(user), status_code=303)
    return render(request, "dashboard.html", user=user)


@router.get("/child/{cid}", response_class=HTMLResponse)
def child_page(cid: int, request: Request, user: User = Depends(require_role("parent")), db: Session = Depends(get_db)):
    child = owned_child(db, user, cid)
    row = services.get_policy_row(db, child)
    db.commit()
    rep = services.child_report(db, child.id)
    reqs = list(db.scalars(select(ChildRequest).where(ChildRequest.child_id == child.id).order_by(ChildRequest.id.desc()).limit(30)))
    audit = list(db.scalars(select(AuditLog).where(AuditLog.child_id == child.id).order_by(AuditLog.id.desc()).limit(100)))
    devs = list(db.scalars(select(Device).where(Device.child_id == child.id)))
    return render(request, "child.html", user=user, child=child, policy=json.loads(row.doc), version=row.version, report=rep,
                  chart=charts.bar_chart(rep["usage"]), requests=reqs, audit=audit, devices=devs, days=services.DAYS_VI,
                  recent=services.recent_events(db, child.id), lan_url=request.app.state.settings.public_url or str(request.base_url).rstrip("/"))


@router.get("/kid", response_class=HTMLResponse)
def kid_page(request: Request, user: User = Depends(require_role("child_view")), db: Session = Depends(get_db)):
    child = owned_child(db, user, user.child_id or -1)
    rep = services.child_report(db, child.id)
    audit = list(db.scalars(select(AuditLog).where(AuditLog.child_id == child.id, AuditLog.action.in_(
        ("policy_change", "request_decided", "command_ADD_TIME", "command_LOCK_NOW", "command_UNLOCK"))).order_by(AuditLog.id.desc()).limit(50)))
    reqs = list(db.scalars(select(ChildRequest).where(ChildRequest.child_id == child.id).order_by(ChildRequest.id.desc()).limit(10)))
    return render(request, "kid.html", user=user, child=child, report=rep, chart=charts.bar_chart(rep["usage"]), audit=audit,
                  requests=reqs, today=rep["usage"][-1]["seconds"])


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, user: User = Depends(require_role("admin")), db: Session = Depends(get_db)):
    users = list(db.scalars(select(User).order_by(User.id)))
    stats = {"children": db.scalar(select(func.count()).select_from(Child)), "devices": db.scalar(select(func.count()).select_from(Device))}
    return render(request, "admin.html", user=user, users=users, stats=stats)


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
