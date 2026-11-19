from server.models import Event, now
from tests.conftest import bearer


def test_friendly_labels_in_report_and_recent_events(app, parent, device_client):
    cid = parent.add_child("Bé An", age=8)
    d = parent.enroll(cid, device_client)
    ev = [
        {"idem_key": "ux-1", "ts": now(), "type": "app_stop", "subject": "winword.exe", "duration_sec": 1200},
        {"idem_key": "ux-2", "ts": now(), "type": "domain_query", "subject": "khanacademy.org", "duration_sec": 1800},
        {"idem_key": "ux-3", "ts": now(), "type": "quota_warning", "subject": "clock_tamper:-3600s"},
    ]
    assert device_client.post("/api/v1/events/sync", json={"events": ev}, headers=bearer(d["access_token"])).status_code == 200
    ov = parent.c.get(f"/api/parent/children/{cid}/overview").json()
    assert ov["report"]["apps"][0]["subject"] == "Microsoft Word"
    assert "Khan Academy" in ov["report"]["domains"][0]["subject"]
    assert any("đồng hồ máy bị chỉnh lệch" in item["text"].lower() for item in ov["recent"])
