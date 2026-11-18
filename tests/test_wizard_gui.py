"""Kiểm thử giao diện trình cài đặt bằng tkinter thật. Cần màn hình: chạy `xvfb-run -a pytest tests/test_wizard_gui.py` trên Linux."""
import gc
import os

import pytest

tk = pytest.importorskip("tkinter")

from installer.wizard import Wizard  # noqa: E402
from tests.fake_system import FakeSystem  # noqa: E402
from tests.test_installer_core import child_code, parent_pc  # noqa: E402,F401  (dùng lại fixture máy bố mẹ chạy server thật)


@pytest.fixture
def root():
    """Tkinter không an toàn với đa luồng: nếu rác (biến/cửa sổ Tk) bị thu gom từ luồng của server trong test thì Tcl abort().
    Nên tắt GC khi cửa sổ còn sống và dọn ở luồng chính. (Ngoài đời thật server chạy ở tiến trình khác.)"""
    gc.disable()
    try:
        r = tk.Tk()
    except tk.TclError:
        gc.enable()
        pytest.skip("không có màn hình (DISPLAY)")
    yield r
    r.destroy()
    gc.enable()
    gc.collect()


def widgets(w, kind):
    out, stack = [], list(w.body.winfo_children()) + list(w.footer.winfo_children())
    while stack:
        x = stack.pop()
        if x.winfo_class() == kind:
            out.append(x)
        stack.extend(x.winfo_children())
    return out


def texts(w):
    return " | ".join(x.cget("text") for x in widgets(w, "Label") + widgets(w, "Button") + widgets(w, "Checkbutton"))


def button(w, label):
    return next(b for b in widgets(w, "Button") if label in b.cget("text"))


def shot(root, name):
    if not os.environ.get("OGK_SHOTS"):
        return
    from PIL import ImageGrab
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(f"{os.environ['OGK_SHOTS']}/{name}.png")


def test_welcome_offers_three_plain_language_choices(root, tmp_path):
    w = Wizard(root, FakeSystem(tmp_path), sync=True)
    t = texts(w)
    assert "Máy của bố mẹ" in t and "Máy của con" in t and "Cả nhà chỉ có một máy tính" in t and "KHÔNG chụp màn hình" in t
    shot(root, "1_welcome")


def test_child_page_validates_code_and_consent_before_enabling_install(root, tmp_path):
    w = Wizard(root, FakeSystem(tmp_path), sync=True)
    w.show_child()
    assert str(w.nextbtn["state"]) == "disabled"
    w.code_var.set("ABC")
    assert "16 ký tự" in w.code_msg.cget("text") and str(w.nextbtn["state"]) == "disabled"
    w.code_var.set("ABCD-2345-0O1I-XXXX")
    assert "không hợp lệ" in w.code_msg.cget("text")
    w.code_var.set("ABCD-2345-XYZ2-3456")
    assert "✓" in w.code_msg.cget("text") and str(w.nextbtn["state"]) == "disabled"      # chưa tích đồng ý
    w.ok_var.set(True); w.refresh_child()
    assert str(w.nextbtn["state"]) == "normal"
    w.toggle_advanced()
    assert "Địa chỉ máy của bố mẹ" in texts(w) and "Chỉ tài khoản" in "".join(x.cget("text") for x in widgets(w, "Radiobutton"))
    shot(root, "2_child")


def test_child_flow_installs_with_one_code(root, parent_pc, tmp_path):
    ps, info, _ = parent_pc
    cs = FakeSystem(tmp_path / "child")
    cs.paths.discovery_port = ps.paths.discovery_port
    w = Wizard(root, cs, cs.paths, sync=True)
    w.show_child()
    w.code_var.set(child_code(ps)); w.ok_var.set(True); w.refresh_child()
    button(w, "Cài đặt").invoke()
    assert "Đã cài xong cho Bin" in texts(w) and "chiếc lá xanh" in texts(w)
    assert cs.has("sc.exe", "start", "OpenGuardKids")
    shot(root, "5_done_child")


def test_child_flow_shows_friendly_error_for_forged_server(root, parent_pc, tmp_path):
    ps, *_ = parent_pc
    cs = FakeSystem(tmp_path / "child")
    w = Wizard(root, cs, cs.paths, sync=True)
    w.show_child()
    w.code_var.set("ABCD-2345-AAAA-AAAA"); w.ok_var.set(True); w.server_var.set(f"127.0.0.1:{ps.paths.port}"); w.refresh_child()
    button(w, "Cài đặt").invoke()
    assert "Chưa cài được" in texts(w) and "không khớp" in texts(w) and "hoàn tác" in texts(w)
    assert not cs.paths.install_dir.exists()
    shot(root, "6_error")
    button(w, "Thử lại").invoke()                              # quay lại trang nhập mã
    assert "Mã cài đặt" in texts(w)


def test_parent_flow_shows_address_to_open_on_phone(root, tmp_path, monkeypatch):
    monkeypatch.delenv("OGK_HOME", raising=False)
    s = FakeSystem(tmp_path, with_own_exe=True)
    try:
        w = Wizard(root, s, s.paths, sync=True)
        w.show_parent()
        assert "tự tạo chứng chỉ" in texts(w).lower() or "chứng chỉ" in texts(w)
        shot(root, "3_parent")
        button(w, "Cài đặt").invoke()
        t = texts(w)
        assert "Bảng điều khiển đã sẵn sàng" in t and f":{s.paths.port}" in t and "Nâng cao" in t and "Tạo mã cài đặt" in t
        shot(root, "4_done_parent")
    finally:
        s.shutdown()


def test_uninstall_page_runs_uninstall(root, tmp_path):
    s = FakeSystem(tmp_path)
    s.paths.install_dir.mkdir(parents=True)
    w = Wizard(root, s, s.paths, sync=True)
    w.show_uninstall()
    assert "Trả lại cài đặt mạng" in texts(w)
    button(w, "Gỡ cài đặt").invoke()
    assert "Đã gỡ Open Guardian Kids" in texts(w) and s.has("sc.exe", "delete", "OpenGuardKids")
