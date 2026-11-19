from tests.conftest import bearer


def test_parent_task_reward_flow_end_to_end(app, parent, device_client):
    cid = parent.add_child("Bé Minh", age=10)
    d = parent.enroll(cid, device_client)
    h_dev = bearer(d["access_token"])

    # 1. Phụ huynh giao nhiệm vụ thưởng 20 phút
    r_create = parent.post(f"/api/parent/children/{cid}/tasks", json={"title": "Làm xong 2 bài toán nâng cao", "reward_minutes": 20})
    assert r_create.status_code == 200
    tid = r_create.json()["id"]
    assert r_create.json()["status"] == "assigned"

    # 2. Máy con xem danh sách nhiệm vụ và báo hoàn thành
    tasks = device_client.get("/api/v1/tasks", headers=h_dev).json()
    assert len(tasks) == 1 and tasks[0]["title"] == "Làm xong 2 bài toán nâng cao"

    r_sub = device_client.post(f"/api/v1/tasks/{tid}/submit", json={"note": "Con làm xong rồi ạ!"}, headers=h_dev)
    assert r_sub.status_code == 200 and r_sub.json()["status"] == "submitted"

    # 3. Phụ huynh duyệt nhiệm vụ -> tự động đẩy lệnh ADD_TIME +20 phút tới thiết bị
    r_dec = parent.post(f"/api/parent/tasks/{tid}/decide", json={"approve": True})
    assert r_dec.status_code == 200 and r_dec.json()["reward_minutes"] == 20

    hb = device_client.post("/api/v1/heartbeat", json={"policy_version": 0, "day": "2026-11-19", "used_today_sec": 0}, headers=h_dev).json()
    add_cmds = [c for c in hb["commands"] if c["action"] == "ADD_TIME"]
    assert len(add_cmds) == 1 and add_cmds[0]["minutes"] == 20
