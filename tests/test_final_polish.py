from datetime import datetime
from sqlalchemy import select

from agent.config import AgentConfig
from agent.engine import Engine
from agent.policy_engine import Policy
from agent.store import AgentStore
from server.models import ChildTask
from tests.conftest import bearer
from tests.helpers import FakeClock, FakePlatform, make_doc


def test_delete_child_cleans_tasks_and_group_exchange_scoped(app, parent, device_client, tmp_path):
    cid = parent.add_child("Bé Sóc", age=9)
    d = parent.enroll(cid, device_client)
    h_dev = bearer(d["access_token"])

    r = parent.post(f"/api/parent/children/{cid}/tasks", json={"title": "Dọn bàn học", "reward_minutes": 10})
    assert r.status_code == 200
    assert device_client.get("/api/v1/tasks", headers=h_dev).status_code == 200

    assert parent.delete(f"/api/parent/children/{cid}").json() == {"ok": True}
    with app.state.SessionLocal() as db:
        assert list(db.scalars(select(ChildTask).where(ChildTask.child_id == cid))) == []

    # Kiểm tra đổi giờ học chỉ cộng bù đúng nhóm bị hết ngân sách, không cộng tràn sang nhóm khác
    clk = FakeClock(wall=datetime(2026, 11, 18, 10, 0, 0).timestamp())
    eng = Engine(AgentConfig(home=tmp_path, device_id="dev-2", require_tray=False, enable_dns=False),
                 store=AgentStore(tmp_path / "b.db"), platform=FakePlatform(), mono=clk.mono)
    eng.clock._anchor_epoch = clk.wall()
    eng.clock._anchor_mono = clk.mono()
    eng.policy = Policy(make_doc(group_budgets_min={"game": 30, "entertainment": 30}))

    day = "2026-11-18"
    eng.acc.add(day, "g:game", 1800)
    eng.acc.add(day, "g:entertainment", 1800)
    eng.acc.add(day, "g:education", 3600)
    assert eng.group_exhausted("game") is True
    assert eng.group_exhausted("entertainment") is True

    code, res = eng.exchange(10, target_group="game")
    assert code == 200 and res["groups"] == ["game"]
    assert eng.group_exhausted("game") is False
    assert eng.group_exhausted("entertainment") is True
