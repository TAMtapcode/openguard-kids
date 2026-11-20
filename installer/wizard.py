"""OGK-Setup: trình cài đặt có giao diện, lời lẽ cho người dùng phổ thông. Mọi thao tác thật nằm ở installer/core.py."""
from __future__ import annotations

import argparse
import queue
import re
import sys
import tempfile
import threading
import traceback
from pathlib import Path

from agent.config import VERSION
from common.installcode import InstallCodeError, parse_install_code

from . import core
from .core import InstallError, Paths
from .system import System

GREEN, DARK, BG, CARD, RED, MUTED, LINE = "#2f8f63", "#16332d", "#eef3f0", "#ffffff", "#b83a4b", "#5b736c", "#d3dfd8"
FONT = ("Segoe UI", 11)
PRIVACY = "Open Guardian Kids KHÔNG chụp màn hình, KHÔNG ghi phím gõ, KHÔNG đọc tin nhắn. Biểu tượng luôn hiển thị."
CONSENT = "Tôi là cha/mẹ hoặc người giám hộ hợp pháp, đã giải thích cho con và đồng ý cài đặt."
BROWSER_NOTE = ("Lần đầu mở, trình duyệt sẽ báo “không bảo mật”. Đó là bình thường vì chứng chỉ do chính máy bạn tạo ra: "
                "chọn “Nâng cao” rồi “Tiếp tục”.")


class Wizard:
    def __init__(self, root, system: System | None = None, paths: Paths | None = None, sync: bool = False):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root, self.s, self.p, self.sync = root, system or System(), paths or Paths.from_env(), sync
        self.log_path = Path(tempfile.gettempdir()) / "ogk-setup.log"
        self.q: queue.Queue = queue.Queue()
        self._keep: list = []   # giữ tham chiếu mọi biến Tk: nếu bị thu gom rác ở luồng nền, Tcl sẽ abort() cả chương trình
        root.title("Open Guardian Kids — Cài đặt")
        root.geometry("740x640")
        root.minsize(700, 600)
        root.configure(bg=BG)
        head = tk.Frame(root, bg=GREEN, height=64)
        head.pack(fill="x")
        tk.Label(head, text="🌿  Open Guardian Kids", bg=GREEN, fg="white", font=("Segoe UI", 17, "bold")).pack(side="left", padx=24, pady=14)
        tk.Label(head, text=f"Cài đặt · v{VERSION}", bg=GREEN, fg="#d7f0e3", font=("Segoe UI", 10)).pack(side="right", padx=24)
        self.body = tk.Frame(root, bg=BG)
        self.body.pack(fill="both", expand=True, padx=32, pady=(20, 8))
        self.footer = tk.Frame(root, bg=BG)
        self.footer.pack(fill="x", padx=32, pady=(0, 20))
        self.show_welcome()

    # ------------------------------------------------------------- tiện ích giao diện
    def sv(self, value=""):
        v = self.tk.StringVar(value=value)
        self._keep.append(v)
        return v

    def bv(self, value=False):
        v = self.tk.BooleanVar(value=value)
        self._keep.append(v)
        return v

    def clear(self):
        for f in (self.body, self.footer):
            for w in f.winfo_children():
                w.destroy()

    def label(self, parent, text, size=11, bold=False, color=DARK, wrap=640, **kw):
        return self.tk.Label(parent, text=text, bg=kw.pop("bg", BG), fg=color, font=("Segoe UI", size, "bold" if bold else "normal"),
                             wraplength=wrap, justify="left", anchor="w", **kw)

    def heading(self, title, sub=None):
        self.label(self.body, title, 20, True).pack(anchor="w")
        if sub:
            self.label(self.body, sub, 11, color=MUTED).pack(anchor="w", pady=(4, 14))

    def button(self, parent, text, cmd, primary=True, state="normal"):
        return self.tk.Button(parent, text=text, command=cmd, state=state, font=("Segoe UI", 11, "bold"), relief="flat", cursor="hand2",
                              bg=GREEN if primary else CARD, fg="white" if primary else DARK, activebackground="#1f6e4a" if primary else LINE,
                              activeforeground="white" if primary else DARK, padx=22, pady=9, bd=0, highlightthickness=0 if primary else 1,
                              highlightbackground=LINE, disabledforeground="#c9d8d0")

    def nav(self, back=None, next_text=None, next_cmd=None, next_state="normal"):
        if back:
            self.button(self.footer, "‹ Quay lại", back, primary=False).pack(side="left")
        self.nextbtn = None
        if next_text:
            self.nextbtn = self.button(self.footer, next_text, next_cmd, state=next_state)
            self.nextbtn.pack(side="right")

    def card(self, icon, title, desc, cmd):
        tk = self.tk
        f = tk.Frame(self.body, bg=CARD, highlightthickness=1, highlightbackground=LINE, cursor="hand2")
        f.pack(fill="x", pady=6)
        inner = tk.Frame(f, bg=CARD)
        inner.pack(fill="x", padx=18, pady=14)
        tk.Label(inner, text=icon, bg=CARD, font=("Segoe UI Emoji", 26)).pack(side="left", padx=(0, 16))
        txt = tk.Frame(inner, bg=CARD)
        txt.pack(side="left", fill="x", expand=True)
        self.label(txt, title, 13, True, bg=CARD).pack(anchor="w")
        self.label(txt, desc, 10, color=MUTED, bg=CARD, wrap=500).pack(anchor="w")
        tk.Label(inner, text="›", bg=CARD, fg=GREEN, font=("Segoe UI", 24, "bold")).pack(side="right")
        for w in (f, inner, txt, *inner.winfo_children(), *txt.winfo_children()):
            w.bind("<Button-1>", lambda e, c=cmd: c())

    def field(self, parent, text, var, show=None, width=34):
        self.label(parent, text, 10, color=MUTED).pack(anchor="w", pady=(8, 2))
        e = self.tk.Entry(parent, textvariable=var, font=FONT, width=width, show=show or "", relief="solid", bd=1)
        e.pack(anchor="w", ipady=5)
        return e

    def check(self, text, var, cmd=None):
        return self.tk.Checkbutton(self.body, text=text, variable=var, command=cmd, bg=BG, font=("Segoe UI", 10), wraplength=640, justify="left",
                                   anchor="w", activebackground=BG, selectcolor=CARD, bd=0, highlightthickness=0)

    # ------------------------------------------------------------- các trang
    def show_welcome(self):
        self.clear()
        self.heading("Chào mừng!", "Công cụ giúp con dùng máy tính lành mạnh: minh bạch, không theo dõi lén. Máy này là…")
        self.card("🏠", "Máy của bố mẹ", "Cài bảng điều khiển để xem và quản lý các máy của con. Làm bước này TRƯỚC.", self.show_parent)
        self.card("🧒", "Máy của con", "Cài ứng dụng đồng hành lên máy tính của con. Cần mã cài đặt từ bảng điều khiển của bố mẹ.", self.show_child)
        self.label(self.body, "🔒  " + PRIVACY, 10, color=MUTED, wrap=650).pack(anchor="w", pady=(16, 0))

    def show_parent(self):
        self.clear()
        self.heading("Cài bảng điều khiển cho bố mẹ", "Chỉ mất khoảng một phút, không cần làm gì thêm.")
        for t in ("Bảng điều khiển chạy nền và tự khởi động cùng Windows",
                  "Tự tạo chứng chỉ bảo mật cho mạng gia đình (bạn không cần biết chứng chỉ là gì)",
                  "Cho phép máy của con trong cùng Wi-Fi/mạng dây kết nối (chỉ mạng ở nhà)",
                  "Thêm lối tắt “Bảng điều khiển OGK” ra màn hình nền"):
            self.label(self.body, "✓  " + t, 11, wrap=650).pack(anchor="w", pady=3)
        self.label(self.body, "Sau khi cài, mở bảng điều khiển, tạo tài khoản, thêm con và bấm “Tạo mã cài đặt”.", 10, color=MUTED, wrap=650).pack(anchor="w", pady=(18, 0))
        self.nav(self.show_welcome, "Cài đặt", lambda: self.run_task("Đang cài bảng điều khiển…", lambda pr: core.install_server(self.s, self.p, pr), self.done_parent))

    def show_child(self):
        tk = self.tk
        self.clear()
        self.heading("Cài đặt trên máy của con", "Nhập mã cài đặt từ bảng điều khiển của bố mẹ (mục “Thêm máy tính của con”).")
        self.code_var, self.pin_var, self.ok_var, self.server_var, self.scope_var, self.user_var = self.sv(), self.sv(), self.bv(), self.sv(), self.sv("all"), self.sv()
        e = self.field(self.body, "Mã cài đặt (16 ký tự)", self.code_var, width=28)
        e.configure(font=("Consolas", 16), width=24)
        e.focus_set()
        self.code_msg = self.label(self.body, "", 10, color=MUTED)
        self.code_msg.pack(anchor="w", pady=(4, 6))
        self.field(self.body, "Mã PIN / mật khẩu tắt nhanh của bố mẹ (dùng khi muốn tắt trên máy con kể cả lúc mất mạng)", self.pin_var, show="•", width=26)
        self.check(CONSENT, self.ok_var, self.refresh_child).pack(anchor="w", pady=(8, 4))
        self.adv_open = False
        self.adv_link = self.label(self.body, "▸ Nâng cao", 10, color=GREEN)
        self.adv_link.pack(anchor="w", pady=(10, 0))
        self.adv_link.bind("<Button-1>", lambda e: self.toggle_advanced())
        self.adv = tk.Frame(self.body, bg=BG)
        self.field(self.adv, "Địa chỉ máy của bố mẹ (để trống: tự tìm trong mạng)", self.server_var, width=34)
        self.label(self.adv, "Áp dụng giới hạn cho", 10, color=MUTED).pack(anchor="w", pady=(10, 2))
        tk.Radiobutton(self.adv, text="Mọi tài khoản trên máy này (khuyến nghị)", variable=self.scope_var, value="all", bg=BG, font=("Segoe UI", 10),
                       command=self.refresh_child, activebackground=BG, selectcolor=CARD, bd=0, highlightthickness=0).pack(anchor="w")
        row = tk.Frame(self.adv, bg=BG)
        row.pack(anchor="w")
        tk.Radiobutton(row, text="Chỉ tài khoản:", variable=self.scope_var, value="one", bg=BG, font=("Segoe UI", 10), command=self.refresh_child,
                       activebackground=BG, selectcolor=CARD, bd=0, highlightthickness=0).pack(side="left")
        self.ttk.Combobox(row, textvariable=self.user_var, values=self.s.local_users(), width=22).pack(side="left", padx=6)
        self.code_var.trace_add("write", lambda *_: self.refresh_child())
        try:  # nếu bố mẹ đã sao chép mã, tự điền sẵn
            clip = self.root.clipboard_get().strip()
            if re.fullmatch(r"[A-Za-z2-9\- ]{16,24}", clip) and len(parse_install_code(clip)[1] or "") == 8:
                self.code_var.set(clip)
        except Exception:
            pass
        self.nav(self.show_welcome, "Cài đặt", self.start_child, "disabled")
        self.refresh_child()

    def toggle_advanced(self):
        self.adv_open = not self.adv_open
        self.adv_link.config(text=("▾ Nâng cao" if self.adv_open else "▸ Nâng cao"))
        (self.adv.pack(anchor="w", fill="x") if self.adv_open else self.adv.pack_forget())

    def refresh_child(self):
        ok_code = False
        try:
            _, check = parse_install_code(self.code_var.get())
            ok_code = True
            self.code_msg.config(text="✓ Mã đúng định dạng" if check else "⚠ Mã chỉ có 8 ký tự: nên dùng đủ 16 ký tự để xác thực máy bố mẹ", fg=GREEN if check else "#9a6a00")
        except InstallCodeError as e:
            self.code_msg.config(text="" if not self.code_var.get().strip() else str(e), fg=RED if self.code_var.get().strip() else MUTED)
        if self.nextbtn:
            self.nextbtn.config(state="normal" if (ok_code and self.ok_var.get()) else "disabled")

    def start_child(self):
        users = [self.user_var.get().strip()] if self.scope_var.get() == "one" and self.user_var.get().strip() else []
        server, code, pin, ok = self.server_var.get().strip() or None, self.code_var.get(), self.pin_var.get().strip(), self.ok_var.get()
        self.run_task("Đang cài đặt trên máy của con…",
                      lambda pr: core.install_child(self.s, self.p, pr, code, server, users, ok, parent_pin=pin), self.done_child, retry=self.show_child)

    # ------------------------------------------------------------- chạy tác vụ + tiến độ
    def run_task(self, title, fn, on_done, retry=None):
        tk = self.tk
        self.clear()
        self.heading(title, "Vui lòng đợi, đừng tắt máy.")
        self.bar = self.ttk.Progressbar(self.body, length=640, maximum=100, mode="determinate")
        self.bar.pack(anchor="w", pady=10)
        self.status = self.label(self.body, "Bắt đầu…", 11, wrap=650)
        self.status.pack(anchor="w")

        def progress(pct, text):
            self.q.put(("p", pct, text))

        def work():
            try:
                self.q.put(("ok", fn(progress)))
            except InstallError as e:
                self.q.put(("err", str(e)))
            except Exception as e:  # lỗi không lường trước: ghi nhật ký, báo dễ hiểu
                self.log_path.write_text(traceback.format_exc(), encoding="utf-8")
                self.q.put(("err", f"Có lỗi không mong muốn ({type(e).__name__}). Chi tiết đã lưu ở {self.log_path}. Hãy gửi tệp này cho người hỗ trợ."))

        if self.sync:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()
        self.poll(on_done, retry)

    def poll(self, on_done, retry):
        try:
            while True:
                kind, *rest = self.q.get_nowait()
                if kind == "p":
                    self.bar["value"] = rest[0]
                    self.status.config(text=rest[1])
                elif kind == "ok":
                    return on_done(rest[0])
                else:
                    return self.show_error(rest[0], retry)
        except queue.Empty:
            pass
        self.root.after(100, lambda: self.poll(on_done, retry))

    # ------------------------------------------------------------- kết quả
    def show_error(self, msg, retry=None):
        self.clear()
        self.heading("Chưa cài được")
        self.tk.Label(self.body, text=msg, bg="#fbe7ea", fg="#7a1f2c", font=("Segoe UI", 12), wraplength=620, justify="left", padx=16, pady=14).pack(anchor="w", fill="x")
        self.label(self.body, "Các thay đổi dở dang (nếu có) đã được hoàn tác, máy của bạn vẫn như cũ.", 10, color=MUTED).pack(anchor="w", pady=10)
        self.nav(self.show_welcome, "Thử lại", retry or self.show_welcome)

    def _done(self, title, lines, extra=None):
        self.clear()
        self.label(self.body, "✅", 40, wrap=80).pack(anchor="w")
        self.heading(title)
        for l in lines:
            self.label(self.body, l, 11, wrap=650).pack(anchor="w", pady=3)
        if extra:
            extra(self.body)
        self.button(self.footer, "Đóng", self.root.destroy).pack(side="right")

    def done_parent(self, info):
        def extra(b):
            self.label(b, "Địa chỉ cho điện thoại / máy khác trong nhà:", 10, color=MUTED).pack(anchor="w", pady=(14, 2))
            self.tk.Label(b, text=info["lan_url"], bg=CARD, font=("Consolas", 14, "bold"), padx=12, pady=8, relief="solid", bd=1).pack(anchor="w")
            self.label(b, BROWSER_NOTE, 9, color=MUTED, wrap=650).pack(anchor="w", pady=(6, 0))
            if info.get("public_networks"):
                self.label(b, f"⚠ Mạng “{', '.join(info['public_networks'])}” đang ở chế độ Công cộng (Public) nên máy của con sẽ không tìm thấy "
                              "máy này. Vào Settings → Network & internet → Wi-Fi/Ethernet → chọn “Private network”.", 10, color=RED, wrap=650
                           ).pack(anchor="w", pady=(10, 0))
            row = self.tk.Frame(b, bg=BG)
            row.pack(anchor="w", pady=14)
            self.button(row, "Mở bảng điều khiển", lambda: self.s.open_url(info["local_url"])).pack(side="left")
            self.button(row, "Sao chép địa chỉ", lambda: (self.root.clipboard_clear(), self.root.clipboard_append(info["lan_url"])), primary=False).pack(side="left", padx=10)
        self._done("Bảng điều khiển đã sẵn sàng", ["Bước tiếp theo: mở bảng điều khiển → tạo tài khoản → thêm con → “Tạo mã cài đặt”.",
                   "Rồi chạy OGK-Setup.exe trên máy của con và chọn “Máy của con”."], extra)

    def done_child(self, info):
        self._done(f"Đã cài xong cho {info['child']}", ["Biểu tượng chiếc lá xanh sẽ hiện ở góc phải thanh taskbar khi con đăng nhập.",
                   "Từ giờ bố mẹ có thể xem và đặt giới hạn từ bảng điều khiển.",
                   "Gỡ cài đặt bất cứ lúc nào ở Settings → Apps → Open Guardian Kids (cần quyền quản trị)."])

    # ------------------------------------------------------------- gỡ cài đặt
    def show_uninstall(self):
        self.clear()
        self.heading("Gỡ Open Guardian Kids?", "Việc này sẽ:")
        for t in ("Dừng và xóa các dịch vụ của Open Guardian Kids", "Trả lại cài đặt mạng (DNS) như ban đầu", "Xóa chương trình và lối tắt"):
            self.label(self.body, "•  " + t, 11).pack(anchor="w", pady=2)
        self.keep = self.bv()
        self.check("Giữ lại dữ liệu (báo cáo, cài đặt) để dùng lại sau", self.keep).pack(anchor="w", pady=14)
        self.nav(self.root.destroy, "Gỡ cài đặt", lambda: self.run_task("Đang gỡ cài đặt…", lambda pr: core.uninstall(self.s, self.p, pr, self.keep.get()),
                                                                          lambda _: self._done("Đã gỡ Open Guardian Kids", ["Cảm ơn bạn đã dùng."])))


# ====================================================================== dòng lệnh / cài im lặng
def silent(args) -> int:
    s, p = System(), Paths.from_env()
    pr = lambda pct, text: print(f"[{pct:3d}%] {text}", flush=True)
    try:
        if args.mode == "parent":
            info = core.install_server(s, p, pr)
            print("Bảng điều khiển:", info["lan_url"])
        elif args.mode == "child":
            info = core.install_child(s, p, pr, args.code, args.server, [u for u in (args.users or "").split(",") if u], accept=args.accept, parent_pin=args.parent_pin or "")
            print("Đã cài cho", info["child"])
        elif args.mode == "uninstall":
            core.uninstall(s, p, pr, args.keep_data)
        else:
            print("Cài im lặng hỗ trợ: --mode parent | child | uninstall")
            return 2
        return 0
    except InstallError as e:
        print("LỖI:", e, file=sys.stderr)
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="OGK-Setup", description="Trình cài đặt Open Guardian Kids")
    ap.add_argument("--silent", action="store_true", help="Cài không giao diện (dành cho IT)")
    ap.add_argument("--mode", choices=["parent", "child", "uninstall"])
    ap.add_argument("--code", default="", help="Mã cài đặt 16 ký tự")
    ap.add_argument("--parent-pin", default="", help="Mã PIN phụ huynh để tắt nhanh trên máy con")
    ap.add_argument("--server", help="Địa chỉ máy bố mẹ (mặc định: tự tìm)")
    ap.add_argument("--users", help="Chỉ giới hạn các tài khoản Windows này (phân cách bằng dấu phẩy)")
    ap.add_argument("--accept", action="store_true", help="Xác nhận bạn là cha/mẹ/người giám hộ và đồng ý")
    ap.add_argument("--keep-data", action="store_true")
    ap.add_argument("--uninstall", action="store_true", help="Mở trình gỡ cài đặt")
    args = ap.parse_args(argv)
    if args.silent:
        return silent(args)
    import tkinter as tk
    root = tk.Tk()
    w = Wizard(root)
    if args.uninstall:
        w.show_uninstall()
    root.mainloop()
    return 0
