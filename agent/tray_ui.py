"""Companion UI (quyền người dùng thường): biểu tượng khay luôn hiển thị, đếm ngược, cảnh báo, hộp thoại minh bạch,
Khu vườn số "Hôm nay em đã dùng gì", đổi giờ học lấy giờ chơi, nhật ký luật. Nói chuyện với Service qua IPC loopback + token."""
from __future__ import annotations

import json
import urllib.request

from .config import AgentConfig
from .winapi import get_platform

GREEN, SOIL, SKY, RED, SUN = "#2f8f63", "#8a5a3c", "#e4eef6", "#b83a4b", "#f0b43c"
ABOUT = ("Open Guardian Kids là công cụ ĐỒNG HÀNH, không theo dõi lén.\n\n"
         "• Chỉ ghi: thời lượng, tên ứng dụng, tên miền (không ghi địa chỉ trang đầy đủ).\n"
         "• Không chụp màn hình, không ghi phím gõ, không đọc tin nhắn.\n"
         "• Mọi lần chặn đều có giải thích. Em có thể xin thêm giờ hoặc báo chặn nhầm.\n"
         "• Em xem được đúng báo cáo mà bố mẹ xem và nhật ký đổi luật.\n"
         "• Cần giúp đỡ khẩn cấp: gọi Tổng đài quốc gia bảo vệ trẻ em 111.")


class Ipc:
    def __init__(self, cfg: AgentConfig):
        self.base = f"http://{cfg.ipc_host}:{cfg.ipc_port}"
        self.cfg = cfg

    def call(self, method, path, body=None, timeout=3):
        token = self.cfg.ipc_token_path.read_text().strip()
        req = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-OGK-Token": token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


def fmt_min(sec):
    return "không giới hạn" if sec is None else f"{max(0, int(sec)) // 60} phút"


class TrayApp:
    def __init__(self, cfg: AgentConfig):
        self.cfg, self.ipc, self.platform = cfg, Ipc(cfg), get_platform()
        self.last_note, self.state, self.win, self.icon = 0, {}, None, None
        self.grace_win = None
        self.lock_win = None

    # ------------------------------------------------------------------ khởi động
    def run(self):
        import tkinter as tk
        self.tk = tk
        self.root = tk.Tk()
        self.root.withdraw()
        self._start_icon()
        self.root.after(500, self.poll)
        self.root.after(1000, self.telemetry)
        self.root.mainloop()

    def _image(self, color):
        from PIL import Image, ImageDraw
        im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((4, 4, 60, 60), fill=color)
        d.polygon([(32, 14), (46, 30), (32, 50), (18, 30)], fill="white")
        return im

    def _start_icon(self):
        import pystray
        menu = pystray.Menu(
            pystray.MenuItem("Mở Open Guardian Kids", lambda: self.root.after(0, self.open_window), default=True),
            pystray.MenuItem("Xin thêm giờ", lambda: self.root.after(0, lambda: self.open_window(1))),
            pystray.MenuItem("Open Guardian Kids làm gì?", lambda: self.root.after(0, self.show_about)))
        self.icon = pystray.Icon("OGK", self._image(GREEN), "Open Guardian Kids", menu)  # không có mục "Thoát": luôn hiện diện (NT1)
        self.icon.run_detached()
        self.icon.notify("Open Guardian Kids đang chạy để giúp em dùng máy lành mạnh.", "Xin chào!")

    # ------------------------------------------------------------------ vòng lặp
    def telemetry(self):
        try:
            self.ipc.call("POST", "/v1/telemetry", {"idle": self.platform.idle_seconds(), "locked": self.platform.session_locked(),
                                                   "fg": self.platform.foreground_process()})
        except Exception:
            pass
        self.root.after(2000, self.telemetry)

    def poll(self):
        try:
            _, self.state = self.ipc.call("GET", "/v1/state")
            _, notes = self.ipc.call("GET", f"/v1/notifications?after={self.last_note}")
            for n in notes.get("items", []):
                self.last_note = max(self.last_note, n["id"])
                self.show_note(n)
            self.refresh()
        except Exception:
            pass
        self.root.after(1000, self.poll)

    def refresh(self):
        s = self.state
        if not s:
            return
        color = RED if s.get("state") in ("locked", "suspended") else SUN if (s.get("remaining_sec") or 9999) < 600 else GREEN
        if self.icon:
            rem = s.get("remaining_sec")
            self.icon.icon = self._image(color)
            self.icon.title = "Open Guardian Kids · " + ("tài khoản không bị giới hạn" if s.get("applies") is False else
                                                         (f"còn {fmt_min(rem)}" if rem is not None else "không giới hạn"))
        if self.win and self.win.winfo_exists():
            self.update_window()
        self.sync_lock_screen(s)
        if self.grace_win and self.grace_win.winfo_exists():
            left = s.get("grace_left")
            if s.get("state") != "grace" or left is None:
                self.grace_win.destroy()
            else:
                self.grace_lbl.config(text=f"Máy sẽ khóa sau {left} giây. Hãy lưu bài ngay!")

    # ------------------------------------------------------------------ thông báo
    def show_note(self, n):
        k = n["kind"]
        if k in ("blocked",):
            return self.show_block(n)
        if k == "grace":
            return self.show_grace(n)
        if k == "locked":
            return self.show_locked(n)
        if k in ("warning", "eye_break", "bonus", "policy", "clock", "resumed", "unenrolled", "wipe", "integrity", "error"):
            if self.icon:
                self.icon.notify(n["body"], n["title"])
            if k in ("warning", "eye_break"):
                self.popup(n["title"], n["body"])

    def popup(self, title, body, buttons=None):
        tk = self.tk
        w = tk.Toplevel(self.root)
        w.title(title)
        w.attributes("-topmost", True)
        w.configure(padx=22, pady=18)
        tk.Label(w, text=title, font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(w, text=body, justify="left", wraplength=420, font=("Segoe UI", 11)).pack(anchor="w", pady=(8, 12))
        row = tk.Frame(w)
        row.pack(anchor="e")
        for text, cmd in (buttons or []) + [("Đóng", None)]:
            tk.Button(row, text=text, command=(lambda c=cmd, ww=w: (c() if c else None, ww.destroy())), padx=10).pack(side="left", padx=4)
        return w

    def show_block(self, n):
        """Hộp thoại minh bạch lý do chặn (NT4): tên mục, nhóm, ai đặt luật."""
        what = "Ứng dụng" if n.get("kind_of") == "app" else "Trang web"
        body = f"{what} bị chặn: {n.get('subject')}\nNhóm: {n.get('category')}\nLuật do: {n.get('by')}\n\n{n['body']}"
        self.popup(n["title"], body, [("Xin thêm giờ", lambda: self.open_window(1)),
                                      ("Báo cáo chặn nhầm", lambda: self.open_window(1, n.get("subject")))])

    def show_grace(self, n):
        self.grace_win = self.popup(n["title"], n["body"], [("Xin thêm giờ", lambda: self.open_window(1))])
        self.grace_lbl = self.tk.Label(self.grace_win, text="", fg=RED, font=("Segoe UI", 11, "bold"))
        self.grace_lbl.pack()

    def show_locked(self, n):
        self.sync_lock_screen(self.state or {"state": "locked", "lock_text": n["body"]})

    # ------------------------------------------------------------------ màn hình khóa (mềm, an toàn)
    def sync_lock_screen(self, s):
        """Màn hình khóa toàn màn hình trong phiên của trẻ. KHÔNG khóa/ngắt phiên Windows: luôn có nút Tắt máy/Đăng xuất,
        tự đóng ngay khi hết khóa (bố mẹ mở khóa, cộng giờ, hết thời hạn khóa tay hoặc sang khung giờ được dùng)."""
        locked = s.get("state") == "locked" and s.get("applies") is not False
        alive = self.lock_win is not None and self.lock_win.winfo_exists()
        if not locked:
            if alive:
                self.lock_win.destroy()
            self.lock_win = None
            return
        if not alive:
            self._build_lock_screen()
        self.lock_msg.config(text=s.get("lock_text") or "Máy đang tạm khóa.")
        until = s.get("manual_until")
        if s.get("reason") == "manual" and until:
            import time as _t
            self.lock_sub.config(text=f"Tự mở khóa sau khoảng {max(1, int(until - _t.time()) // 60 + 1)} phút, hoặc khi bố mẹ mở khóa.")
        else:
            self.lock_sub.config(text="Máy sẽ tự mở khi bố mẹ cộng thêm giờ hoặc tới khung giờ được dùng.")
        self.lock_win.lift()
        self.lock_win.attributes("-topmost", True)

    def _build_lock_screen(self):
        tk, bg = self.tk, "#16332d"
        w = self.lock_win = tk.Toplevel(self.root)
        w.title("Open Guardian Kids · Máy đang tạm khóa")
        w.configure(bg=bg)
        w.attributes("-fullscreen", True)
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", lambda: None)
        box = tk.Frame(w, bg=bg)
        box.place(relx=0.5, rely=0.45, anchor="center")
        tk.Label(box, text="🌿", bg=bg, fg="white", font=("Segoe UI Emoji", 48)).pack()
        tk.Label(box, text="Máy đang tạm khóa", bg=bg, fg="white", font=("Segoe UI", 28, "bold")).pack(pady=(6, 8))
        self.lock_msg = tk.Label(box, bg=bg, fg="#d7f0e3", font=("Segoe UI", 14), wraplength=640, justify="center")
        self.lock_msg.pack()
        self.lock_sub = tk.Label(box, bg=bg, fg="#9fc4b6", font=("Segoe UI", 11), wraplength=640, justify="center")
        self.lock_sub.pack(pady=(6, 18))
        tk.Label(box, text="Nhắn cho bố mẹ (không bắt buộc):", bg=bg, fg="white", font=("Segoe UI", 11)).pack()
        self.lock_note = tk.Entry(box, width=50, font=("Segoe UI", 11))
        self.lock_note.pack(ipady=4, pady=6)
        row = tk.Frame(box, bg=bg)
        row.pack(pady=8)
        btn = dict(font=("Segoe UI", 11, "bold"), padx=14, pady=6, relief="flat", cursor="hand2")
        tk.Button(row, text="Xin thêm 15 phút", bg="#2f8f63", fg="white", command=self._lock_request, **btn).pack(side="left", padx=6)
        tk.Button(row, text="Đăng xuất", bg="white", command=lambda: self.platform_power("logoff"), **btn).pack(side="left", padx=6)
        tk.Button(row, text="Tắt máy", bg="white", command=lambda: self.platform_power("shutdown"), **btn).pack(side="left", padx=6)
        self.lock_status = tk.Label(box, bg=bg, fg="#f0b43c", font=("Segoe UI", 11))
        self.lock_status.pack(pady=4)
        tk.Label(box, text="Cần giúp đỡ khẩn cấp? Gọi 111 (Tổng đài quốc gia bảo vệ trẻ em) hoặc nhờ người lớn.", bg=bg, fg="#9fc4b6",
                 font=("Segoe UI", 10)).pack(pady=(14, 0))

    def _lock_request(self):
        try:
            code, res = self.ipc.call("POST", "/v1/request", {"kind": "add-time", "subject": "", "minutes": 15,
                                                             "message": self.lock_note.get().strip()}, timeout=10)
            msg = res.get("message") or ("Đã gửi cho bố mẹ. Máy sẽ tự mở nếu bố mẹ đồng ý." if code == 200 else res.get("error", "Có lỗi."))
        except Exception:
            msg = "Chưa gửi được, em thử lại sau nhé."
        self.lock_status.config(text=msg)

    def platform_power(self, what):
        import subprocess
        args = ["shutdown", "/l"] if what == "logoff" else ["shutdown", "/s", "/t", "0"]
        try:
            subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            pass

    def show_about(self):
        self.popup("Open Guardian Kids làm gì?", ABOUT)

    # ------------------------------------------------------------------ cửa sổ chính
    def open_window(self, tab=0, subject=None):
        from tkinter import ttk
        tk = self.tk
        if not (self.win and self.win.winfo_exists()):
            self.win = tk.Toplevel(self.root)
            self.win.title("Open Guardian Kids")
            self.win.geometry("520x620")
            self.nb = ttk.Notebook(self.win)
            self.nb.pack(fill="both", expand=True)
            self.tabs = [tk.Frame(self.nb, padx=14, pady=12) for _ in range(4)]
            for f, t in zip(self.tabs, ("Hôm nay em đã dùng gì", "Xin giúp đỡ", "Đổi giờ", "Nhật ký luật")):
                self.nb.add(f, text=t)
            self.build_today(self.tabs[0])
            self.build_help(self.tabs[1])
            self.build_exchange(self.tabs[2])
            self.build_audit(self.tabs[3])
        self.nb.select(tab)
        if subject:
            self.kind.set("false-block")
            self.subject_var.set(subject)
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.after(300, lambda: self.win.attributes("-topmost", False))
        self.update_window()

    def build_today(self, f):
        tk = self.tk
        self.canvas = tk.Canvas(f, width=200, height=170, bg=SKY, highlightthickness=0)
        self.canvas.pack()
        self.lbl_time = tk.Label(f, font=("Segoe UI", 18, "bold"))
        self.lbl_time.pack(pady=(8, 0))
        self.lbl_sub = tk.Label(f, justify="center", wraplength=440)
        self.lbl_sub.pack()
        self.lst = tk.Text(f, height=12, width=58, state="disabled", relief="flat", bg="#f6faf7")
        self.lst.pack(pady=8, fill="both", expand=True)

    def draw_plant(self, mood):
        c = self.canvas
        c.delete("all")
        c.create_rectangle(70, 130, 130, 165, fill=SOIL, outline="")
        c.create_line(100, 130, 100, 70 if mood != "sleepy" else 90, width=6, fill=GREEN)
        leaf = GREEN if mood != "sleepy" else "#7a9c86"
        c.create_oval(60, 60, 100, 90, fill=leaf, outline="")
        c.create_oval(100, 50, 145, 85, fill=leaf, outline="")
        face = {"happy": "◠‿◠", "ok": "•‿•", "sleepy": "-﹏-  z z"}[mood if mood in ("happy", "ok", "sleepy") else "ok"]
        c.create_text(100, 105, text=face, font=("Segoe UI", 12), fill="white" if mood != "sleepy" else "#334")

    def update_window(self):
        s = self.state or {}
        if s.get("applies") is False:
            self.draw_plant("ok")
            self.lbl_time.config(text="Tài khoản này không bị giới hạn")
            self.lbl_sub.config(text="Open Guardian Kids chỉ áp dụng cho tài khoản Windows của con.")
            return
        self.draw_plant(s.get("garden", {}).get("mood", "ok"))
        rem = s.get("remaining_sec")
        self.lbl_time.config(text="Còn " + fmt_min(rem) if rem is not None else "Hôm nay không giới hạn")
        msg = {"sleepy": "Cây đang ngáp ngủ. Em dùng máy hơi lâu rồi, nghỉ mắt một chút nhé!", "happy": "Cây rất vui vì em đã học nhiều hôm nay!",
               "ok": "Cây đang lớn lên cùng em."}[s.get("garden", {}).get("mood", "ok")]
        extra = "\n⚠ Máy chưa kết nối máy chủ lâu ngày, một số nhóm tạm khóa." if s.get("stale") else ""
        self.lbl_sub.config(text=f"Đã dùng {fmt_min(s.get('used_sec', 0))} · {msg}{extra}")
        try:
            _, t = self.ipc.call("GET", "/v1/today")
            lines = ["Ứng dụng:"] + [f"   {n}: {fmt_min(v)}" for n, v in t["apps"]] + ["", "Trang web:"] + [f"   {n}: {fmt_min(v)}" for n, v in t["domains"]]
            for g in (s.get("groups") or {}).values():
                lines.append(f"\nNgân sách {g['label']}: {fmt_min(g['used_sec'])} / {fmt_min(g['budget_sec'])}")
            self.lst.config(state="normal")
            self.lst.delete("1.0", "end")
            self.lst.insert("1.0", "\n".join(lines))
            self.lst.config(state="disabled")
        except Exception:
            pass
        l = s.get("ltp", {})
        self.ltp_lbl.config(text=f"Em đã tích lũy {l.get('available_min', 0)} phút giờ chơi từ việc học." if l.get("enabled") else "Bố mẹ chưa bật tính năng này.")

    def build_help(self, f):
        tk = self.tk
        self.kind = tk.StringVar(value="add-time")
        tk.Radiobutton(f, text="Con muốn xin thêm giờ", variable=self.kind, value="add-time").pack(anchor="w")
        tk.Radiobutton(f, text="Con nghĩ trang/ứng dụng này bị chặn nhầm", variable=self.kind, value="false-block").pack(anchor="w")
        tk.Label(f, text="Trang web hoặc tên ứng dụng (nếu báo chặn nhầm):").pack(anchor="w", pady=(10, 0))
        self.subject_var = tk.StringVar()
        tk.Entry(f, textvariable=self.subject_var, width=50).pack(anchor="w")
        tk.Label(f, text="Xin thêm bao nhiêu phút?").pack(anchor="w", pady=(10, 0))
        self.min_var = tk.IntVar(value=15)
        tk.Spinbox(f, from_=5, to=120, increment=5, textvariable=self.min_var, width=6).pack(anchor="w")
        tk.Label(f, text="Em muốn nhắn gì với bố mẹ?").pack(anchor="w", pady=(10, 0))
        self.msg = tk.Text(f, height=4, width=50)
        self.msg.pack(anchor="w")
        self.help_status = tk.Label(f, text="", wraplength=440, justify="left")
        tk.Button(f, text="Gửi cho bố mẹ", command=self.send_request, padx=12).pack(anchor="w", pady=10)
        self.help_status.pack(anchor="w")
        tk.Label(f, text="Cần giúp đỡ khẩn cấp? Gọi 111 (Tổng đài quốc gia bảo vệ trẻ em).", fg=RED).pack(anchor="w", pady=8)

    def send_request(self):
        code, res = self.ipc.call("POST", "/v1/request", {"kind": self.kind.get(), "subject": self.subject_var.get(), "minutes": self.min_var.get(),
                                                         "message": self.msg.get("1.0", "end").strip()}, timeout=10)
        self.help_status.config(text=res.get("message") or ("Đã gửi cho bố mẹ. Em sẽ thấy phản hồi ở đây." if code == 200 else res.get("error", "Có lỗi.")))

    def build_exchange(self, f):
        tk = self.tk
        tk.Label(f, text="Học xong được thưởng giờ chơi!", font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(f, text="Thời gian em học trên các trang giáo dục (Scratch, Khan Academy…) được tích lũy.", wraplength=440, justify="left").pack(anchor="w", pady=6)
        self.ltp_lbl = tk.Label(f, font=("Segoe UI", 11, "bold"))
        self.ltp_lbl.pack(anchor="w")
        self.ex_var = tk.IntVar(value=10)
        tk.Spinbox(f, from_=5, to=60, increment=5, textvariable=self.ex_var, width=6).pack(anchor="w", pady=6)
        self.ex_status = tk.Label(f, text="", wraplength=440, justify="left")
        tk.Button(f, text="Đổi giờ", command=self.exchange, padx=12).pack(anchor="w")
        self.ex_status.pack(anchor="w", pady=6)

    def exchange(self):
        code, res = self.ipc.call("POST", "/v1/exchange", {"minutes": self.ex_var.get()})
        self.ex_status.config(text=f"Đã đổi {res['minutes']} phút giờ chơi!" if code == 200 else res.get("error", "Có lỗi."))

    def build_audit(self, f):
        tk = self.tk
        tk.Label(f, text="Ai đã đổi luật, lúc nào? (con xem được)", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.audit_txt = tk.Text(f, height=24, width=60, state="disabled")
        self.audit_txt.pack(fill="both", expand=True, pady=6)
        tk.Button(f, text="Làm mới", command=self.load_audit).pack(anchor="w")
        self.load_audit()

    def load_audit(self):
        import datetime
        code, res = self.ipc.call("GET", "/v1/audit", timeout=8)
        rows = res.get("items", []) if code == 200 else []
        text = "\n".join(f"{datetime.datetime.fromtimestamp(a['ts']):%d/%m %H:%M} · {a['actor']}: {a['field'] or a['action']} {a['old']} → {a['new']}" for a in rows) \
            or res.get("error", "Chưa có thay đổi nào.")
        self.audit_txt.config(state="normal")
        self.audit_txt.delete("1.0", "end")
        self.audit_txt.insert("1.0", text)
        self.audit_txt.config(state="disabled")


def main():
    TrayApp(AgentConfig.load()).run()


if __name__ == "__main__":
    main()
