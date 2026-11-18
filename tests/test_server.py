import base64
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from common.schema import is_valid_subject
from common.signing import verify
from server import services
from server.models import ChildRequest, Command, DailyUsage, Device, Event, User, now
from tests.conftest import PW, Parent, bearer, page_csrf, pre_token


# ---------------------------------------------------------------- xác thực & phiên
def test_unauthenticated_redirect_and_api_401(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/api/parent/status").status_code == 401


def test_register_rejects_short_password(client):
    html = client.get("/register").text
    r = client.post("/register", data={"email": "a@b.co", "name": "A", "password": "short", "csrf": pre_token(html)})
    assert r.status_code == 422 and "10 ký tự" in r.text


def test_password_is_argon2id(app, parent):
    with app.state.SessionLocal() as db:
        assert db.scalar(select(User.pw_hash)).startswith("$argon2id$")


def test_login_lockout_after_5_failures(app, parent):
    with TestClient(app) as c:
        for _ in range(5):
            html = c.get("/login").text
            r = c.post("/login", data={"email": parent.email, "password": "sai-mat-khau-1", "csrf": pre_token(html)})
            assert r.status_code == 401
        html = c.get("/login").text  # đúng mật khẩu nhưng đang bị khóa 15 phút
        r = c.post("/login", data={"email": parent.email, "password": PW, "csrf": pre_token(html)})
        assert r.status_code == 401


def test_login_requires_form_csrf(app, parent):
    with TestClient(app) as c:
        c.get("/login")
        r = c.post("/login", data={"email": parent.email, "password": PW, "csrf": "bogus"})
        assert r.status_code == 403


def test_session_cookie_flags_and_logout_revokes(app, parent):
    with TestClient(app) as c:
        html = c.get("/login").text
        r = c.post("/login", data={"email": parent.email, "password": PW, "csrf": pre_token(html)}, follow_redirects=False)
        sc = r.headers["set-cookie"].lower()
        assert "httponly" in sc and "samesite=lax" in sc
        tok = r.cookies.get("ogk_session")
        csrf = page_csrf(c.get("/").text)
        assert c.post("/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
        c.cookies.set("ogk_session", tok)  # phiên cũ phải vô hiệu phía máy chủ
        assert c.get("/api/parent/status").status_code == 401


def test_csrf_required_for_writes(parent):
    r = parent.c.post("/api/parent/children", json={"name": "X", "age": 8})
    assert r.status_code == 403
    assert parent.post("/api/parent/children", json={"name": "X", "age": 8}).status_code == 200


def test_security_headers(client):
    r = client.get("/login")
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]


# ---------------------------------------------------------------- ghép đôi, chính sách có chữ ký
def test_enroll_requires_consent(parent):
    cid = parent.add_child()
    assert parent.post(f"/api/parent/children/{cid}/enroll-code").status_code == 422


def test_enroll_code_single_use_and_expiry(app, parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    assert d["device_id"] and d["access_token"] and d["refresh_token"] and len(base64.b64decode(d["policy_key"])) == 32
    code = parent.post(f"/api/parent/children/{cid}/enroll-code?consent=true").json()["code"]
    ok = device_client.post("/api/v1/enrollment", json={"code": code, "fingerprint": "f", "device_name": "x"})
    assert ok.status_code == 200
    again = device_client.post("/api/v1/enrollment", json={"code": code, "fingerprint": "f", "device_name": "x"})
    assert again.status_code == 400
    code2 = parent.post(f"/api/parent/children/{cid}/enroll-code?consent=true").json()["code"]
    with app.state.SessionLocal() as db:
        db.execute(text("UPDATE enroll_codes SET expires_at = :t"), {"t": now() - 1})
        db.commit()
    assert device_client.post("/api/v1/enrollment", json={"code": code2, "fingerprint": "f", "device_name": "x"}).status_code == 400


def test_policy_signature_and_default_6_11(parent, device_client):
    cid = parent.add_child(age=8)
    d = parent.enroll(cid, device_client)
    env = device_client.get("/api/v1/policy", headers=bearer(d["access_token"])).json()
    key = base64.b64decode(d["policy_key"])
    assert verify(env["policy"], env["sig"], key)
    p = env["policy"]
    assert p["domains"]["blocked_categories"] == ["social", "inappropriate"] and p["group_budgets_min"]["game"] == 45
    assert len(p["schedule"]) == 7 and all(len(r) == 48 for r in p["schedule"])
    assert len(p["categories"]) >= 200
    p["quota"]["weekday_min"] = 9999  # sửa đổi -> chữ ký không còn khớp
    assert not verify(p, env["sig"], key)


def test_token_refresh_rotation_and_revocation(parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    r = device_client.post("/api/v1/token/refresh", json={"device_id": d["device_id"], "refresh_token": d["refresh_token"]})
    assert r.status_code == 200
    reuse = device_client.post("/api/v1/token/refresh", json={"device_id": d["device_id"], "refresh_token": d["refresh_token"]})
    assert reuse.status_code == 401  # refresh token cũ đã bị xoay vòng
    assert parent.post(f"/api/parent/devices/{d['device_id']}/revoke").status_code == 200
    assert device_client.get("/api/v1/policy", headers=bearer(r.json()["access_token"])).status_code == 401


# ---------------------------------------------------------------- heartbeat, sự kiện
def test_heartbeat_updates_status_and_returns_version(parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    hb = {"policy_version": 0, "day": "2026-11-16", "used_today_sec": 1200, "remaining_sec": 4200, "state": "ok", "ui_ok": True}
    r = device_client.post("/api/v1/heartbeat", json=hb, headers=bearer(d["access_token"])).json()
    assert r["policy_version"] >= 1 and abs(r["server_time"] - now()) < 5
    st = parent.c.get("/api/parent/status").json()["children"][0]["devices"][0]
    assert st["online"] and st["used_today_sec"] == 1200 and st["remaining_sec"] == 4200


def test_events_idempotent_and_privacy_filter(app, parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    h = bearer(d["access_token"])
    ev = [{"idem_key": "key-0001-aaaa", "ts": now(), "type": "app_stop", "subject": "notepad.exe", "duration_sec": 60},
          {"idem_key": "key-0002-bbbb", "ts": now(), "type": "domain_query", "subject": "khanacademy.org", "duration_sec": 30}]
    assert device_client.post("/api/v1/events/sync", json={"events": ev}, headers=h).json() == {"accepted": 2, "duplicates": 0, "rejected": 0}
    assert device_client.post("/api/v1/events/sync", json={"events": ev}, headers=h).json() == {"accepted": 0, "duplicates": 2, "rejected": 0}
    bad = [{"idem_key": "key-0003-cccc", "ts": now(), "type": "domain_query", "subject": "https://x.com/path?q=1"},
           {"idem_key": "key-0004-dddd", "ts": now(), "type": "keylog", "subject": "x.com"}]
    assert device_client.post("/api/v1/events/sync", json={"events": bad}, headers=h).json()["rejected"] == 2
    with app.state.SessionLocal() as db:
        assert len(list(db.scalars(select(Event)))) == 2
    rep = parent.c.get(f"/api/parent/children/{cid}/report").json()
    assert rep["apps"][0]["subject"] == "notepad.exe" and rep["domains"][0]["subject"] == "khanacademy.org"


def test_subject_validation():
    assert is_valid_subject("roblox.com") and is_valid_subject("game.exe")
    assert not is_valid_subject("https://a.com/x") and not is_valid_subject("a.com/path") and not is_valid_subject("a.com?q=1")


# ---------------------------------------------------------------- yêu cầu của trẻ & phản hồi
def test_add_time_request_flow_creates_command(app, parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    h = bearer(d["access_token"])
    r = device_client.post("/api/v1/requests/add-time", json={"minutes": 15, "message": "Em làm xong bài rồi"}, headers=h)
    assert r.status_code == 200
    assert device_client.post("/api/v1/requests/add-time", json={"minutes": 15}, headers=h).status_code == 429  # chống spam
    rid = r.json()["id"]
    assert parent.post(f"/api/parent/requests/{rid}/decide", json={"approve": True, "minutes": 20}).json()["status"] == "approved"
    assert parent.post(f"/api/parent/requests/{rid}/decide", json={"approve": False}).status_code == 409
    with app.state.SessionLocal() as db:
        c = db.scalar(select(Command).where(Command.action == "ADD_TIME"))
        assert c.minutes == 20 and c.status == "pending"
    mine = device_client.get("/api/v1/requests", headers=h).json()
    assert mine[0]["status"] == "approved"  # trẻ thấy được phản hồi (NT3)


def test_false_block_approval_allowlists_domain(parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    h = bearer(d["access_token"])
    rid = device_client.post("/api/v1/requests/false-block", json={"subject": "hocvui.example.org", "message": "Trang học"}, headers=h).json()["id"]
    v0 = parent.c.get(f"/api/parent/children/{cid}/policy").json()["version"]
    parent.post(f"/api/parent/requests/{rid}/decide", json={"approve": True})
    pol = parent.c.get(f"/api/parent/children/{cid}/policy").json()
    assert "hocvui.example.org" in pol["policy"]["domains"]["allowed"] and pol["version"] > v0


def test_request_message_encrypted_at_rest(app, parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    device_client.post("/api/v1/requests/add-time", json={"minutes": 10, "message": "bí-mật-của-con"}, headers=bearer(d["access_token"]))
    with app.state.SessionLocal() as db:
        raw = db.execute(text("SELECT message FROM child_requests")).scalar()
        assert b"bi-mat" not in raw and "bí-mật".encode() not in raw
        assert db.scalar(select(ChildRequest.message)) == "bí-mật-của-con"
        assert isinstance(db.execute(text("SELECT policy_key FROM devices")).scalar(), bytes)


# ---------------------------------------------------------------- phân quyền / IDOR
def test_idor_between_parents(app, parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    rid = device_client.post("/api/v1/requests/add-time", json={"minutes": 10}, headers=bearer(d["access_token"])).json()["id"]
    other = Parent(app, "other@example.com")
    try:
        assert other.c.get(f"/api/parent/children/{cid}/policy").status_code == 404
        assert other.c.get(f"/api/parent/children/{cid}/report").status_code == 404
        assert other.c.get(f"/child/{cid}").status_code == 404
        assert other.put(f"/api/parent/children/{cid}/policy", json={"quota": {"weekday_min": 1}}).status_code == 404
        assert other.post(f"/api/parent/children/{cid}/command", json={"action": "LOCK_NOW"}).status_code == 404
        assert other.post(f"/api/parent/requests/{rid}/decide", json={"approve": True}).status_code == 404
        assert other.post(f"/api/parent/devices/{d['device_id']}/revoke").status_code == 404
        assert other.delete(f"/api/parent/children/{cid}/data").status_code == 404
        assert other.post(f"/api/parent/children/{cid}/enroll-code?consent=true").status_code == 404
        assert other.c.get("/api/parent/status").json()["children"] == []
    finally:
        other.c.__exit__(None, None, None)


def test_child_view_role_is_read_only_and_scoped(app, parent):
    c1, c2 = parent.add_child("Bin"), parent.add_child("Bon")
    assert parent.post(f"/api/parent/children/{c1}/viewer", json={"username": "bin-xem", "password": PW}).status_code == 200
    with TestClient(app) as kid:
        html = kid.get("/login").text
        r = kid.post("/login", data={"email": "bin-xem", "password": PW, "csrf": pre_token(html)})
        assert r.status_code == 200 and "Hôm nay em đã dùng gì" in r.text
        assert kid.get("/api/parent/status").status_code == 403
        assert kid.get(f"/child/{c1}").status_code == 403
        assert kid.get("/admin").status_code == 403


# ---------------------------------------------------------------- nhật ký kiểm toán (NT5)
def test_audit_records_who_when_ip_old_new_and_child_can_read(app, parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    assert parent.put(f"/api/parent/children/{cid}/policy", json={"quota": {"weekday_min": 45, "weekend_min": 120}}).status_code == 200
    log = device_client.get("/api/v1/audit", headers=bearer(d["access_token"])).json()
    row = next(a for a in log if a["field"] == "quota.weekday_min")
    assert row["actor"] == parent.email and row["old"] == "90" and row["new"] == "45" and row["ip"] and row["ts"] > 0


def test_policy_validation_rejects_bad_input(parent):
    cid = parent.add_child()
    for bad in ({"domains": {"blocked": ["https://x.com/a"]}}, {"quota": {"weekday_min": 99999}}, {"schedule": [[1] * 48]},
                {"apps": {"blocked": [{"name": "a.exe", "sha256": "xyz"}]}}, {"group_budgets_min": {"khong-co": 5}}):
        assert parent.put(f"/api/parent/children/{cid}/policy", json=bad).status_code == 422


def test_policy_version_bumps_only_on_change(parent):
    cid = parent.add_child()
    v1 = parent.c.get(f"/api/parent/children/{cid}/policy").json()["version"]
    same = parent.c.get(f"/api/parent/children/{cid}/policy").json()["policy"]
    assert parent.put(f"/api/parent/children/{cid}/policy", json={"quota": same["quota"]}).json()["version"] == v1
    assert parent.put(f"/api/parent/children/{cid}/policy", json={"quota": {"weekday_min": 3, "weekend_min": 3}}).json()["version"] == v1 + 1


# ---------------------------------------------------------------- WebSocket lệnh khẩn
def test_websocket_command_push_and_ack(app, parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    with pytest.raises(Exception):
        with device_client.websocket_connect(f"/ws/agent/{d['device_id']}", headers={"Authorization": "Bearer bad"}):
            pass
    with device_client.websocket_connect(f"/ws/agent/{d['device_id']}", headers=bearer(d["access_token"])) as ws:
        t0 = time.monotonic()
        r = parent.post(f"/api/parent/children/{cid}/command", json={"action": "LOCK_NOW"})
        assert r.json()["delivered_now"] == 1
        frame = ws.receive_json()
        assert frame["action"] == "LOCK_NOW" and time.monotonic() - t0 < 5
        ws.send_json({"ack": frame["id"]})
        time.sleep(0.3)
    with app.state.SessionLocal() as db:
        assert db.get(Command, frame["id"]).status == "acked"


def test_pending_commands_delivered_by_heartbeat_when_offline(parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    parent.post(f"/api/parent/children/{cid}/command", json={"action": "ADD_TIME", "minutes": 15})
    hb = {"policy_version": 0, "day": "2026-11-16", "used_today_sec": 0, "remaining_sec": 0}
    cmds = device_client.post("/api/v1/heartbeat", json=hb, headers=bearer(d["access_token"])).json()["commands"]
    assert [c["action"] for c in cmds if c["action"] == "ADD_TIME"] == ["ADD_TIME"]


# ---------------------------------------------------------------- lưu trữ & xóa dữ liệu
def test_retention_purges_after_90_days(app, parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    with app.state.SessionLocal() as db:
        db.add(Event(idem_key="old-event-0001", ts=now() - 91 * 86400, device_id=d["device_id"], child_id=cid, type="app_stop", subject="a.exe"))
        db.add(Event(idem_key="new-event-0001", ts=now() - 5 * 86400, device_id=d["device_id"], child_id=cid, type="app_stop", subject="b.exe"))
        db.commit()
        res = services.purge_old(db, app.state.settings)
        assert res["events"] == 1 and [e.subject for e in db.scalars(select(Event))] == ["b.exe"]


def test_wipe_child_data_server_and_queue_local_wipe(app, parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    h = bearer(d["access_token"])
    device_client.post("/api/v1/events/sync", json={"events": [{"idem_key": "key-wipe-0001", "ts": now(), "type": "app_stop", "subject": "a.exe", "duration_sec": 5}]}, headers=h)
    device_client.post("/api/v1/heartbeat", json={"policy_version": 1, "day": "2026-11-16", "used_today_sec": 10, "remaining_sec": 1}, headers=h)
    assert parent.delete(f"/api/parent/children/{cid}/data").status_code == 200
    with app.state.SessionLocal() as db:
        assert not list(db.scalars(select(Event))) and not list(db.scalars(select(DailyUsage)))
        assert db.scalar(select(Command).where(Command.action == "WIPE_LOCAL")) is not None


def test_dashboard_pages_render(parent, device_client):
    cid = parent.add_child()
    parent.enroll(cid, device_client)
    assert "Hôm nay con đang thế nào" in parent.c.get("/").text
    html = parent.c.get(f"/child/{cid}").text
    assert "Lịch tuần" in html and "policy-data" in html and "Xóa toàn bộ dữ liệu của con tôi" in html and "<svg" in html


def test_child_page_shows_recent_security_events(parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    ev = [{"idem_key": "key-rec-0001", "ts": now(), "type": "quota_warning", "subject": "clock_tamper:-7200s"},
          {"idem_key": "key-rec-0002", "ts": now(), "type": "locked", "subject": "manual"},
          {"idem_key": "key-rec-0003", "ts": now(), "type": "override_granted", "subject": "learn_to_play", "duration_sec": 300}]
    assert device_client.post("/api/v1/events/sync", json={"events": ev}, headers=bearer(d["access_token"])).json()["accepted"] == 3
    html = parent.c.get(f"/child/{cid}").text
    assert "Đồng hồ máy bị chỉnh lệch" in html and "phụ huynh bấm khóa" in html and "đổi giờ học lấy giờ chơi" in html


def test_privacy_page_is_public_and_states_the_promise(client):
    r = client.get("/privacy")
    assert r.status_code == 200 and "Không bao giờ ghi" in r.text and "Ảnh chụp màn hình" in r.text and "111" in r.text
