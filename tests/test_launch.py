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
