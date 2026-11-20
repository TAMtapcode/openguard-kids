import os, sys
os.environ.setdefault("PYTHONUTF8", "1")
for _s in (sys.stdout, sys.stderr):
    if _s and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
"""python -m server --data-dir ./ogk-data            (khuyến nghị: tự tạo chứng chỉ, mở cổng 8443)
   python -m server [--host 0.0.0.0 --port 8443 --cert certs/server.pem --key certs/server.key | --insecure-http]
   python -m server create-admin EMAIL"""
import argparse
import getpass

import uvicorn

from .config import load_settings
from .main import create_app


def main():
    ap = argparse.ArgumentParser(prog="ogk-server")
    sub = ap.add_subparsers(dest="cmd")
    ca = sub.add_parser("create-admin", help="Tạo tài khoản quản trị")
    ca.add_argument("email")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--cert")
    ap.add_argument("--key")
    ap.add_argument("--insecure-http", action="store_true", help="Chỉ dùng khi phát triển trên máy local (không TLS)")
    ap.add_argument("--data-dir", help="Thư mục dữ liệu; tự tạo chứng chỉ TLS, khóa mã hóa và CSDL trong đó (cách dễ nhất)")
    a = ap.parse_args()
    if a.cmd == "create-admin":
        from .models import User
        from .security import hash_password, password_problem
        s = load_settings()
        app = create_app(s)
        pw = getpass.getpass("Mật khẩu (≥10 ký tự): ")
        if password_problem(pw):
            raise SystemExit(password_problem(pw))
        with app.state.SessionLocal() as db:
            db.add(User(email=a.email.lower(), display_name="Quản trị", pw_hash=hash_password(pw), role="admin"))
            db.commit()
        print("Đã tạo quản trị viên.")
        return
    if a.data_dir:
        from .runner import run
        run(a.data_dir, a.host, a.port, https=not a.insecure_http)
        return
    if not a.insecure_http and not (a.cert and a.key):
        raise SystemExit("Cần --cert và --key (TLS 1.2+). Tạo bằng: python scripts/gen_cert.py. Dev: thêm --insecure-http.")
    app = create_app(load_settings(cookie_secure=not a.insecure_http))
    kw = {} if a.insecure_http else {"ssl_certfile": a.cert, "ssl_keyfile": a.key}
    uvicorn.run(app, host=a.host, port=a.port, **kw)


if __name__ == "__main__":
    main()