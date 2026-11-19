from server.tray_icon import _make_icon
from scripts.launch_ogk import resolve


def test_parent_tray_icon_and_route():
    img = _make_icon()
    assert img.size == (64, 64)
    assert resolve(["server-tray"]) == ("server-tray", [])
