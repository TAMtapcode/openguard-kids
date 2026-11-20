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
from tests.conftest import PW, Parent, bearer

PW_VALID = "Thu-nghiem-10"


# ---------------------------------------------------------------- xác thực & phiên
def test_unauthenticated_api_401_and_spa_served(client):
    assert client.get("/api/parent/status").status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    for path in ("/", "/login", "/child/3"):
        r = client.get(path)
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert client.get("/api/khong-co").status_code == 404


def register(c, login, pw, **kw):
    return c.post("/api/auth/register", json={"login": login, "password": pw, **kw})


def login(c, login_, pw, **kw):
    return c.post("/api/auth/login", json={"login": login_, "password": pw, **kw})


def test_register_rejects_short_password(client):
    r = register(client, "a@b.co", "short9ch", name="A")
    assert r.status_code == 422 and "10 ký tự" in r.json()["detail"]


def test_register_with_short_username_and_no_name(client):
    r = register(client, "  Tester1 ", PW_VALID)
    assert r.status_code == 201 and r.json()["user"]["login"] == "tester1" and r.json()["user"]["display_name"] == "tester1"
    assert "token" not in r.json() and client.get("/api/auth/me").status_code == 200
    client.cookies.clear()
    assert login(client, "tester1", PW_VALID).status_code == 200


@pytest.mark.parametrize(
    "login_,pw,msg",
    [
        ("ab", PW_VALID, "không hợp lệ"),
        ("x y@z", PW_VALID, "không hợp lệ"),
        ("tester2", "1234567890", "dễ đoán"),
        ("tester3", "aaaaaaaaaa", "dễ đoán"),
    ],
)
def test_register_rejects_bad_login_or_weak_password(client, login_, pw, msg):
    r = register(client, login_, pw)
    assert r.status_code == 422 and msg in r.json()["detail"]


def test_password_is_argon2id(app, parent):
    with app.state.SessionLocal() as db:
        assert db.scalar(select(User.pw_hash)).startswith("$argon2id$")


def test_login_lockout_after_5_failures(app, parent):
    with TestClient(app) as c:
        for _ in range(5):
            assert login(c, parent.email, "sai-mat-khau-1").status_code == 401
        assert login(c, parent.email, PW).status_code == 401   # đúng mật khẩu nhưng đang bị khóa 15 phút


def test_auth_only_accepts_json_so_foreign_html_forms_cannot_post(app, parent):
    with TestClient(app) as c:
        r = c.post("/api/auth/login", data={"login": parent.email, "password": PW})
        assert r.status_code == 415 and "ogk_session" not in r.cookies


def test_session_cookie_flags_and_logout_revokes(app, parent):
    with TestClient(app) as c:
        r = login(c, parent.email, PW)
        sc = r.headers["set-cookie"].lower()
        assert "httponly" in sc and "samesite=lax" in sc
        tok, csrf = r.cookies.get("ogk_session"), r.json()["csrf"]
        assert c.post("/api/auth/logout").status_code == 403            # thiếu CSRF
        assert c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
        c.cookies.set("ogk_session", tok)  # phiên cũ phải vô hiệu phía máy chủ
        assert c.get("/api/parent/status").status_code == 401


def test_bearer_session_for_netlify_frontend(app, parent):
    with TestClient(app) as c:
        r = login(c, parent.email, PW, token=True)
        assert r.status_code == 200 and "set-cookie" not in r.headers
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        assert c.post("/api/parent/children", json={"name": "Na", "age": 8}, headers=h).status_code == 200   # không cần CSRF
        assert [k["name"] for k in c.get("/api/parent/status", headers=h).json()["children"]] == ["Na"]
        assert c.post("/api/auth/logout", headers=h).status_code == 200
        assert c.get("/api/parent/status", headers=h).status_code == 401
        assert c.get("/api/parent/status", headers={"Authorization": "Bearer sai"}).status_code == 401


def test_cors_allows_netlify_only_and_never_credentials(client):
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,authorization",
           "Access-Control-Request-Private-Network": "true"}
    r = client.options("/api/auth/login", headers={"Origin": "https://ogk-kids.netlify.app", **pre})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "https://ogk-kids.netlify.app"
    assert r.headers.get("access-control-allow-private-network") == "true" and "access-control-allow-credentials" not in r.headers
    r = client.options("/api/auth/login", headers={"Origin": "https://evil.example", **pre})
    assert "access-control-allow-origin" not in r.headers


def test_idor_between_parents(app, parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    rid = device_client.post("/api/v1/requests/add-time", json={"minutes": 10}, headers=bearer(d["access_token"])).json()["id"]
    other = Parent(app, "other@example.com")
    try:
        assert other.c.get(f"/api/parent/children/{cid}/policy").status_code == 404
        assert other.c.get(f"/api/parent/children/{cid}/report").status_code == 404
        assert other.c.get(f"/api/parent/children/{cid}/overview").status_code == 404
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
        r = login(kid, "bin-xem", PW)
        assert r.status_code == 200 and r.json()["user"]["role"] == "child_view"
        assert kid.get("/api/kid/overview").json()["child"]["name"] == "Bin"
        assert kid.get("/api/parent/status").status_code == 403
        assert kid.get(f"/api/parent/children/{c1}/overview").status_code == 403
        assert kid.get("/api/admin/overview").status_code == 403


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


def test_child_overview_api(parent, device_client):
    cid = parent.add_child()
    parent.enroll(cid, device_client)
    o = parent.c.get(f"/api/parent/children/{cid}/overview").json()
    assert o["child"]["name"] == "Bin" and len(o["policy"]["schedule"]) == 7 and len(o["report"]["usage"]) == 7
    assert o["devices"][0]["status"] == "active" and "social" in o["categories"]


def test_child_page_shows_recent_security_events(parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    ev = [{"idem_key": "key-rec-0001", "ts": now(), "type": "quota_warning", "subject": "clock_tamper:-7200s"},
          {"idem_key": "key-rec-0002", "ts": now(), "type": "locked", "subject": "manual"},
          {"idem_key": "key-rec-0003", "ts": now(), "type": "override_granted", "subject": "learn_to_play", "duration_sec": 300}]
    assert device_client.post("/api/v1/events/sync", json={"events": ev}, headers=bearer(d["access_token"])).json()["accepted"] == 3
    html = " ".join(e["text"] for e in parent.c.get(f"/api/parent/children/{cid}/overview").json()["recent"])
    assert "Đồng hồ máy bị chỉnh lệch" in html and "phụ huynh bấm khóa" in html and "đổi giờ học lấy giờ chơi" in html


def test_privacy_page_is_public(client):
    assert client.get("/privacy").status_code == 200


# ---------------------------------------------------------------- thứ tự lệnh & xóa hồ sơ
def test_newer_lock_unlock_supersedes_older_pending_commands(app, parent, device_client):
    cid = parent.add_child()
    d = parent.enroll(cid, device_client)
    parent.post(f"/api/parent/children/{cid}/command", json={"action": "LOCK_NOW"})
    parent.post(f"/api/parent/children/{cid}/command", json={"action": "UNLOCK"})
    hb = device_client.post("/api/v1/heartbeat", headers=bearer(d["access_token"]),
                            json={"policy_version": 0, "day": "2026-01-01", "used_today_sec": 0}).json()
    assert [c["action"] for c in hb["commands"]] == ["UNLOCK"]        # LOCK cũ không bị phát lại sau UNLOCK


def test_delete_child_removes_profile_and_everything_linked(app, parent, device_client):
    cid = parent.add_child()
    other = parent.add_child()
    d = parent.enroll(cid, device_client)
    parent.post(f"/api/parent/children/{cid}/viewer", json={"username": "beyeu", "password": PW_VALID})
    parent.post(f"/api/parent/children/{cid}/command", json={"action": "LOCK_NOW"})
    assert parent.delete(f"/api/parent/children/{cid}").json() == {"ok": True}
    kids = [c["id"] for c in parent.c.get("/api/parent/status").json()["children"]]
    assert cid not in kids and other in kids
    assert parent.c.get(f"/api/parent/children/{cid}/policy").status_code == 404
    assert device_client.post("/api/v1/heartbeat", headers=bearer(d["access_token"]),
                              json={"policy_version": 0, "day": "2026-01-01", "used_today_sec": 0}).status_code == 401
    with app.state.SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == "beyeu")) is None
        assert db.scalar(select(Device).where(Device.child_id == cid)) is None
        assert db.scalar(select(Command)) is None
