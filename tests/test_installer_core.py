import pytest
import requests

from agent.config import AgentConfig
from common.installcode import fingerprint_check, parse_install_code
from installer.core import (RUN_KEY, SERVICE_AGENT, SERVICE_SERVER, UNINSTALL_KEY, InstallError, copy_program, install_child, install_server,
                            public_networks, uninstall)
from tests.fake_system import FakeSystem

CHILD = {"parent_name": "Mẹ Bin", "email": "me@example.com", "password": "Mat-khau-du-manh-1", "child_name": "Bin", "child_age": 8, "consent": True}


@pytest.fixture
def parent_pc(tmp_path, monkeypatch):
    monkeypatch.delenv("OGK_HOME", raising=False)
    s = FakeSystem(tmp_path / "parent", with_own_exe=True)
    msgs = []
    info = install_server(s, s.paths, lambda pct, t: msgs.append((pct, t)))
    yield s, info, msgs
    s.shutdown()


def child_code(s):
    r = requests.post(f"https://127.0.0.1:{s.paths.port}/api/setup/local", json=CHILD, verify=False, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["install_code"]


def test_install_server_does_everything_automatically(parent_pc):
    s, info, msgs = parent_pc
    p = s.paths
    assert msgs[0][0] <= 10 and msgs[-1] == (100, "Xong!") and [m[0] for m in msgs] == sorted(m[0] for m in msgs)   # tiến độ tăng dần
    assert (p.install_dir / "ogk.exe").exists() and (p.install_dir / "_internal" / "lib.dll").exists()
    assert s.has("netsh", "advfirewall", "firewall", "add", "rule", "name=OGK Server (TCP)", "dir=in", "action=allow", "protocol=TCP", f"localport={p.port}", "profile=private")
    assert any("profile=private" in c and "protocol=UDP" in c for c in s.commands)                  # chỉ mở trên mạng nhà
    assert s.has("certutil", "-addstore", "-f", "Root")
    create = next(c for c in s.commands if c[:2] == ["sc.exe", "create"])
    assert create[2] == SERVICE_SERVER and create[4].endswith('ogk.exe" server-service') and "start=" in create
    assert any(c[:3] == ["sc.exe", "failure", SERVICE_SERVER] and "restart/5000" in c[-1] for c in s.commands)
    assert (p.downloads / "OGK-Setup.exe").read_bytes() == b"MZ-setup"                              # chia sẻ bộ cài cho máy con
    assert "https://localhost" in (p.public_desktop / "Bảng điều khiển OGK.url").read_text(encoding="utf-8")
    assert s.regs[(UNINSTALL_KEY, "UninstallString")].endswith('ogk.exe" uninstall') and (UNINSTALL_KEY, "DisplayName") in s.regs   # có trong Settings → Apps
    assert (p.server_data, False) in s.secured                                                       # thư mục dữ liệu bị khóa quyền
    assert info["local_url"] == f"https://localhost:{p.port}" and len(info["fingerprint"]) == 64
    assert requests.get(f"https://127.0.0.1:{p.port}/healthz", verify=False, timeout=5).json() == {"ok": True}


def test_install_requires_admin_and_changes_nothing(tmp_path):
    s = FakeSystem(tmp_path, admin=False)
    for fn in (lambda: install_server(s, s.paths, lambda *_: None),
               lambda: install_child(s, s.paths, lambda *_: None, "ABCD-2345-AAAA-AAAA", accept=True)):
        with pytest.raises(InstallError, match="quyền quản trị"):
            fn()
    assert not s.paths.install_dir.exists() and s.commands == []


def test_install_server_rolls_back_when_service_cannot_be_created(tmp_path):
    s = FakeSystem(tmp_path, fail_create=True)
    with pytest.raises(InstallError, match="Windows không cho tạo dịch vụ"):
        install_server(s, s.paths, lambda *_: None)
    assert not s.paths.install_dir.exists()                                # không để lại tệp dở dang
    assert s.has("sc.exe", "delete", SERVICE_SERVER)


def test_child_finds_parent_pc_automatically_and_pairs_with_one_code(parent_pc, tmp_path):
    ps, info, _ = parent_pc
    code = child_code(ps)
    cs = FakeSystem(tmp_path / "child")
    cs.paths.discovery_port = ps.paths.discovery_port                          # cùng "mạng LAN" với máy bố mẹ
    msgs = []
    res = install_child(cs, cs.paths, lambda pct, t: msgs.append(t), code, accept=True, allow_insecure=False)
    assert res["child"] == "Bin" and res["server"] == f"https://127.0.0.1:{ps.paths.port}"
    assert any("✓" in m for m in msgs)                                         # báo "đã xác nhận đúng máy của bố mẹ"
    cfg = AgentConfig.load(cs.paths.agent_home)
    assert cfg.device_id and cfg.pin_sha256 == info["fingerprint"] and cfg.monitored_users == []   # tự ghim chứng chỉ đúng
    assert cs.paths.agent_home.joinpath("secrets", "device.bin").exists()
    create = next(c for c in cs.commands if c[:2] == ["sc.exe", "create"])
    assert create[2] == SERVICE_AGENT and create[4].endswith("agent-service")
    assert cs.regs[(RUN_KEY, "OpenGuardKidsTray")].endswith('ogk.exe" tray')   # biểu tượng khay chạy khi đăng nhập
    assert cs.has("sc.exe", "start", SERVICE_AGENT)


def test_wrong_code_is_rejected_before_touching_the_machine(parent_pc, tmp_path):
    ps, info, _ = parent_pc
    code = child_code(ps)
    enroll, check = parse_install_code(code)
    forged = f"{enroll}{fingerprint_check('00' * 32)}"                         # máy giả mạo: mã xác thực chứng chỉ sai
    cs = FakeSystem(tmp_path / "child")
    with pytest.raises(InstallError, match="không khớp"):
        install_child(cs, cs.paths, lambda *_: None, forged, server=f"https://127.0.0.1:{ps.paths.port}", accept=True)
    assert not cs.paths.install_dir.exists() and not any(c[:2] == ["sc.exe", "create"] for c in cs.commands)
    with pytest.raises(InstallError, match="16 ký tự"):
        install_child(cs, cs.paths, lambda *_: None, "ABCD-23", accept=True)
    with pytest.raises(InstallError, match="xác nhận"):
        install_child(cs, cs.paths, lambda *_: None, code, accept=False)
    with pytest.raises(InstallError, match="Không tìm thấy máy của bố mẹ"):
        cs.paths.discovery_port = 1   # không ai trả lời
        install_child(cs, cs.paths, lambda *_: None, code, accept=True)


def test_used_code_gives_friendly_error_and_rolls_back(parent_pc, tmp_path):
    ps, *_ = parent_pc
    code = child_code(ps)
    c1 = FakeSystem(tmp_path / "c1")
    install_child(c1, c1.paths, lambda *_: None, code, server=f"https://127.0.0.1:{ps.paths.port}", accept=True)
    c2 = FakeSystem(tmp_path / "c2")
    with pytest.raises(InstallError, match="đã hết hạn.*tạo mã mới"):
        install_child(c2, c2.paths, lambda *_: None, code, server=f"https://127.0.0.1:{ps.paths.port}", accept=True)
    assert not c2.paths.install_dir.exists()                                    # hoàn tác sạch


def test_uninstall_removes_everything_and_restores_network(tmp_path, monkeypatch):
    monkeypatch.delenv("OGK_HOME", raising=False)
    s = FakeSystem(tmp_path, with_own_exe=True)
    try:
        install_server(s, s.paths, lambda *_: None)
        install_child(s, s.paths, lambda *_: None, child_code(s), server=f"https://127.0.0.1:{s.paths.port}", accept=True, copy_files=False)
    finally:
        s.shutdown()
    s.commands.clear()
    steps = []
    uninstall(s, s.paths, lambda pct, t: steps.append(pct))
    assert steps[-1] == 100
    assert s.has("sc.exe", "delete", SERVICE_AGENT) and s.has("sc.exe", "delete", SERVICE_SERVER)
    assert any(c[1:3] == ["agent", "restore"] for c in s.commands)                   # trả lại DNS gốc trước khi xóa chương trình
    assert s.has("netsh", "advfirewall", "firewall", "delete", "rule", "name=OGK Server (TCP)") and s.has("certutil", "-delstore", "Root")
    assert UNINSTALL_KEY in s.deleted_keys and (RUN_KEY, "OpenGuardKidsTray") in s.deleted_values
    assert not s.paths.agent_home.exists() and not s.paths.server_data.exists()
    assert not (s.paths.public_desktop / "Bảng điều khiển OGK.url").exists() and s.delayed_delete == [s.paths.install_dir]


def test_uninstall_can_keep_data(tmp_path):
    s = FakeSystem(tmp_path)
    s.paths.agent_home.mkdir(parents=True)
    s.paths.server_data.mkdir(parents=True)
    (s.paths.install_dir).mkdir(parents=True)
    (s.paths.install_dir / "ogk.exe").write_text("x")
    uninstall(s, s.paths, lambda *_: None, keep_data=True)
    assert s.paths.agent_home.exists() and s.paths.server_data.exists()


def test_reinstall_stops_old_processes_before_copying_and_retries_locked_files(tmp_path):
    s = FakeSystem(tmp_path)
    s.paths.install_dir.mkdir(parents=True)
    fails = [OSError("[WinError 32] The process cannot access the file because it is being used by another process")]
    real_copy = s.copy_tree

    def flaky_copy(src, dst):
        s.commands.append(["copy"])
        if fails:
            raise fails.pop()
        real_copy(src, dst)
    s.copy_tree = flaky_copy
    copy_program(s, s.paths)
    first_copy = s.commands.index(["copy"])
    before = s.commands[:first_copy]
    assert ["sc.exe", "stop", SERVICE_SERVER] in before and ["sc.exe", "stop", SERVICE_AGENT] in before
    assert any(c[:2] == ["taskkill", "/F"] and "ogk.exe" in c for c in before)
    assert s.commands.count(["copy"]) == 2 and (s.paths.install_dir / "ogk.exe").exists()


def test_copy_gives_friendly_error_when_files_stay_locked(tmp_path):
    s = FakeSystem(tmp_path)
    s.copy_tree = lambda src, dst: (_ for _ in ()).throw(OSError("WinError 32 " + "x" * 5000))
    with pytest.raises(InstallError, match="khởi động lại máy") as e:
        copy_program(s, s.paths, attempts=2)
    assert len(str(e.value)) < 400


def test_reinstalling_child_role_restarts_the_dashboard_on_a_dual_role_pc(tmp_path):
    s = FakeSystem(tmp_path)
    s.paths.install_dir.mkdir(parents=True)
    s.running = {SERVICE_AGENT}
    s.wait_https = lambda url, timeout: True
    copy_program(s, s.paths, SERVICE_SERVER)                                   # cài lại bảng điều khiển: agent của máy con phải chạy lại
    assert SERVICE_AGENT in s.running and SERVICE_SERVER not in s.running
    s.running = {SERVICE_SERVER}
    copy_program(s, s.paths, SERVICE_AGENT)
    assert s.running == {SERVICE_SERVER}


def test_public_network_on_parent_pc_is_reported(tmp_path):
    s = FakeSystem(tmp_path)
    assert public_networks(s) == []
    s.run = lambda args, timeout=90: (0, "Wi-Fi Nha\r\n\r\n")
    assert public_networks(s) == ["Wi-Fi Nha"]
    s.run = lambda args, timeout=90: (1, "Get-NetConnectionProfile: not found")
    assert public_networks(s) == []
