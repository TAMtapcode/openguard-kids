"""Nạp dữ liệu mẫu vào server đang chạy để xem thử dashboard (chỉ dùng miền công khai, an toàn).
   python scripts/seed_demo.py --server http://127.0.0.1:8000"""
import argparse
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

PW = "Mat-khau-demo-123"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    a = ap.parse_args()
    s, base = requests.Session(), a.server.rstrip("/")
    tok = re.search(r'name="csrf" value="([^"]+)"', s.get(f"{base}/register").text).group(1)
    r = s.post(f"{base}/register", data={"email": "demo@example.com", "name": "Mẹ của Bin", "password": PW, "csrf": tok})
    if "đã được đăng ký" in r.text:
        tok = re.search(r'name="csrf" value="([^"]+)"', s.get(f"{base}/login").text).group(1)
        r = s.post(f"{base}/login", data={"email": "demo@example.com", "password": PW, "csrf": tok})
    csrf = re.search(r'<meta name="csrf" content="([^"]*)"', r.text).group(1)
    h = {"X-CSRF-Token": csrf}
    cid = s.post(f"{base}/api/parent/children", json={"name": "Bin", "age": 9}, headers=h).json()["id"]
    code = s.post(f"{base}/api/parent/children/{cid}/enroll-code?consent=true", headers=h).json()["code"]
    d = requests.post(f"{base}/api/v1/enrollment", json={"code": code, "fingerprint": "demo", "device_name": "PC-phòng-học", "agent_version": "1.0.0"}).json()
    ah = {"Authorization": f"Bearer {d['access_token']}"}
    requests.post(f"{base}/api/v1/heartbeat", headers=ah, json={"policy_version": 1, "day": (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%Y-%m-%d"), "used_today_sec": 2820,
                                                                "remaining_sec": 2580, "state": "ok", "ui_ok": True})
    now = int(time.time())
    ev = []
    for i, (app, sec) in enumerate([("chrome.exe", 5400), ("scratch 3.exe", 2400), ("minecraftlauncher.exe", 1500), ("winword.exe", 900), ("spotify.exe", 600)]):
        ev.append({"idem_key": str(uuid.uuid4()), "ts": now - i * 3600, "type": "app_stop", "subject": app, "duration_sec": sec})
    for i, (dom, sec) in enumerate([("khanacademy.org", 3000), ("wikipedia.org", 1200), ("youtube.com", 1800), ("hocmai.vn", 900), ("vnexpress.net", 300)]):
        ev.append({"idem_key": str(uuid.uuid4()), "ts": now - i * 1800, "type": "domain_query", "subject": dom, "duration_sec": sec})
    ev += [{"idem_key": str(uuid.uuid4()), "ts": now - 600, "type": "blocked_domain", "subject": "tiktok.com"} for _ in range(3)]
    requests.post(f"{base}/api/v1/events/sync", headers=ah, json={"events": ev})
    requests.post(f"{base}/api/v1/requests/add-time", headers=ah, json={"minutes": 15, "message": "Con đang làm bài Scratch, cần thêm chút nữa ạ"})
    s.put(f"{base}/api/parent/children/{cid}/policy", headers=h, json={"quota": {"weekday_min": 90, "weekend_min": 120}})
    s.post(f"{base}/api/parent/children/{cid}/viewer", headers=h, json={"username": "bin", "password": PW})
    print(f"Xong. Đăng nhập phụ huynh: demo@example.com / {PW}   |   Tài khoản của con: bin / {PW}")


if __name__ == "__main__":
    main()
