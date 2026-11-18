import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.config import load_settings  # noqa: E402
from server.main import create_app  # noqa: E402

PW = "Mat-khau-du-manh-1"


@pytest.fixture
def app(tmp_path):
    s = load_settings(db_url=f"sqlite:///{tmp_path / 't.db'}", master_key=b"k" * 32, cookie_secure=False)
    return create_app(s)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


class Parent:
    """Trợ giúp thao tác như một phụ huynh trên một TestClient riêng."""

    def __init__(self, app, email="me@example.com"):
        self.c = TestClient(app)
        self.c.__enter__()
        r = self.c.post("/api/auth/register", json={"login": email, "name": "Mẹ Bin", "password": PW})
        assert r.status_code == 201, r.text[:300]
        self.csrf = r.json()["csrf"]
        self.email = email

    def h(self):
        return {"X-CSRF-Token": self.csrf}

    def post(self, url, **kw):
        return self.c.post(url, headers=self.h(), **kw)

    def put(self, url, **kw):
        return self.c.put(url, headers=self.h(), **kw)

    def delete(self, url, **kw):
        return self.c.delete(url, headers=self.h(), **kw)

    def add_child(self, name="Bin", age=9):
        return self.post("/api/parent/children", json={"name": name, "age": age}).json()["id"]

    def enroll(self, cid, tc):
        code = self.post(f"/api/parent/children/{cid}/enroll-code?consent=true").json()["code"]
        r = tc.post("/api/v1/enrollment", json={"code": code, "fingerprint": "fp-1", "device_name": "PC-Bin", "agent_version": "1.0"})
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def parent(app):
    p = Parent(app)
    yield p
    p.c.__exit__(None, None, None)


@pytest.fixture
def device_client(app):
    with TestClient(app) as c:
        yield c


def bearer(tok):
    return {"Authorization": f"Bearer {tok}"}
