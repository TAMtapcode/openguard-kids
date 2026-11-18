"""Phụ thuộc dùng chung: DB, phiên đăng nhập, CSRF, kiểm tra quyền sở hữu (chống IDOR)."""
from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Child, Device, User, WebSession, now
from .security import decode_access_token, sha256hex

SESSION_COOKIE = "ogk_session"


class AuthRedirect(Exception):
    pass


def get_db(request: Request):
    db = request.app.state.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def client_ip(request: Request) -> str:
    if request.app.state.settings.trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _session(request: Request, db: Session):
    tok = request.cookies.get(SESSION_COOKIE)
    if not tok:
        return None
    sess = db.get(WebSession, sha256hex(tok))
    if not sess or sess.expires_at < now():
        return None
    user = db.get(User, sess.user_id)
    if not user or user.disabled:
        return None
    return user, sess


def web_session(request: Request, db: Session = Depends(get_db)):
    s = _session(request, db)
    if not s:
        raise AuthRedirect()
    request.state.session = s[1]
    return s[0]


def csrf_check(request: Request, db: Session = Depends(get_db)):
    """Mọi thao tác ghi phải mang X-CSRF-Token (hoặc field csrf) khớp với phiên."""
    s = _session(request, db)
    if not s:
        raise AuthRedirect()
    sent = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(sent, s[1].csrf):
        raise HTTPException(403, "CSRF token không hợp lệ.")


def require_role(*roles):
    def dep(user: User = Depends(web_session)):
        if user.role not in roles:
            raise HTTPException(403, "Bạn không có quyền thực hiện thao tác này.")
        return user
    return dep


def owned_child(db: Session, user: User, child_id: int) -> Child:
    """Kiểm tra quyền sở hữu ở tầng truy vấn (không chỉ ẩn nút trên giao diện)."""
    q = select(Child).where(Child.id == child_id)
    if user.role == "parent":
        q = q.where(Child.parent_id == user.id)
    elif user.role == "child_view":
        q = q.where(Child.id == (user.child_id or -1))
    else:
        raise HTTPException(404, "Không tìm thấy.")
    child = db.scalar(q)
    if not child:
        raise HTTPException(404, "Không tìm thấy.")
    return child


def owned_device(db: Session, user: User, device_id: str) -> Device:
    dev = db.scalar(select(Device).join(Child, Child.id == Device.child_id).where(Device.id == device_id, Child.parent_id == user.id))
    if not dev:
        raise HTTPException(404, "Không tìm thấy.")
    return dev


def current_device(request: Request, db: Session = Depends(get_db)) -> Device:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "Thiếu token.")
    device_id = decode_access_token(auth[7:], request.app.state.settings.jwt_secret)
    dev = db.get(Device, device_id) if device_id else None
    if not dev or dev.status != "active":  # danh sách thu hồi: thiết bị bị gỡ -> 401 ngay
        raise HTTPException(401, "Token không hợp lệ hoặc thiết bị đã bị gỡ.")
    return dev
