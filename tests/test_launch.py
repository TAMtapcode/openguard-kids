import sys

from scripts.launch_ogk import main, resolve


def test_resolve_routes_commands_and_defaults_to_setup():
    assert resolve([]) == ("setup", [])
    assert resolve(["--silent", "--mode", "child"]) == ("setup", ["--silent", "--mode", "child"])
    assert resolve(["uninstall"]) == ("uninstall", [])
    assert resolve(["agent", "hash", "x.exe"]) == ("agent", ["hash", "x.exe"])
    assert resolve(["server-service"]) == ("server-service", [])


def test_agent_hash_command_works_through_single_entry_point(tmp_path, capsys):
    f = tmp_path / "a.exe"
    f.write_bytes(b"abc")
    assert main(["agent", "hash", str(f)]) == 0
    assert capsys.readouterr().out.strip() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert main(["khong-co-lenh"]) == 2


def test_silent_mode_reports_friendly_errors_and_exit_codes(capsys, monkeypatch):
    from installer import wizard
    from installer.system import System
    monkeypatch.setattr(System, "is_admin", lambda self: False)
    assert main(["--silent", "--mode", "child", "--code", "ABCD-2345-AAAA-AAAA", "--accept"]) == 1
    assert "quyền quản trị" in capsys.readouterr().err
    assert main(["--silent"]) == 2


def test_server_runner_starts_without_console(tmp_path, monkeypatch):
    """Windows Service và bản --windowed có sys.stdout = None; uvicorn không được sập vì thế."""
    import socket
    import threading
    import time

    import requests

    from server.runner import run
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    stop, errors = threading.Event(), []

    def target():
        try:
            run(tmp_path, host="127.0.0.1", port=port, https=False, stop_event=stop, discovery_port=0)
        except BaseException as e:
            errors.append(e)
    t = threading.Thread(target=target, daemon=True)
    t.start()
    ok = False
    for _ in range(100):
        if errors:
            break
        try:
            ok = requests.get(f"http://127.0.0.1:{port}/healthz", timeout=1).status_code == 200
            if ok:
                break
        except requests.RequestException:
            time.sleep(0.1)
    stop.set()
    t.join(10)
    assert not errors, errors
    assert ok
