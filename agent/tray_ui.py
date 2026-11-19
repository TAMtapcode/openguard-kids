"""Companion UI thân thiện cho trẻ (quyền người dùng thường):
- Biểu tượng chiếc lá xanh luôn hiển thị ở khay hệ thống (NT1).
- Giao diện "Góc nhỏ của bé" chữ to, ít chữ, dễ dùng: Khu vườn số, đồng hồ giờ còn lại, 2 nút to (Xin thêm giờ / Đổi điểm học).
- Hộp thoại giải thích minh bạch khi bị chặn (NT4) và lịch sử đổi quy định dễ hiểu (NT5).
- Màn hình khóa mềm bám sát mọi Virtual Desktop của Windows (chống trick Win+Ctrl+D / chuyển Desktop, không khóa cứng lúc khởi động).
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import time as _t
import urllib.error
import urllib.request

from .config import AgentConfig
from .winapi import get_platform

BG = "#f4faf6"
CARD = "#ffffff"
GREEN = "#25855a"
GREEN_SOFT = "#e3f4eb"
SOIL = "#8a5a3c"
SKY = "#e6f3fa"
RED = "#c53b4d"
RED_SOFT = "#fdebed"
SUN = "#e8a224"
SUN_SOFT = "#fef5df"
TEXT = "#15332b"
MUTED = "#557068"
LINE = "#d4e3db"

ABOUT = (
    "Open Guardian Kids là người bạn ĐỒNG HÀNH giúp bé dùng máy tính vui và khỏe mạnh.\n\n"
    "• Chỉ ghi: thời gian dùng máy, tên ứng dụng, tên trang web (không ghi nội dung bên trong).\n"
    "• Không chụp màn hình, không ghi phím gõ, không đọc tin nhắn của bé.\n"
    "• Khi ứng dụng hay trang web tạm khóa, máy luôn giải thích rõ lý do.\n"
    "• Bé luôn có thể bấm Xin thêm giờ, Đổi điểm học hoặc Báo chặn nhầm.\n"
    "• Cần giúp đỡ khẩn cấp: gọi Tổng đài quốc gia bảo vệ trẻ em 111 (miễn phí)."
)

QUICK_REASONS = (
    "Con đang làm bài tập",
    "Con sắp xong ván rồi",
    "Con xem nốt bài học",
    "Cho con thêm chút nữa nha",
)

AUDIT_FIELD_VI = {
    "quota.weekday_min": "Giờ dùng ngày thường (phút)",
    "quota.weekend_min": "Giờ dùng cuối tuần (phút)",
    "schedule": "Lịch giờ được dùng trong tuần",
    "domains.blocked": "Danh sách trang web bị chặn",
    "domains.allowed": "Danh sách trang web được phép",
    "domains.blocked_categories": "Nhóm trang web bị chặn",
    "domains.safesearch": "Tìm kiếm an toàn (SafeSearch)",
    "apps.blocked": "Ứng dụng bị chặn",
    "apps.allowed": "Ứng dụng được phép",
    "apps.mode": "Chế độ lọc ứng dụng",
    "learn_to_play.enabled": "Đổi giờ học lấy giờ chơi",
    "learn_to_play.earn_ratio": "Tỉ lệ thưởng khi học",
    "learn_to_play.daily_cap_min": "Số phút đổi tối đa/ngày",
}

AUDIT_ACTION_VI = {
    "policy_change": "Đổi quy định",
    "request_decided": "Trả lời yêu cầu của bé",
    "command_ADD_TIME": "Cộng thêm giờ cho bé",
    "command_LOCK_NOW": "Tạm khóa máy",
    "command_UNLOCK": "Mở khóa máy",
    "device_enrolled": "Kết nối máy của bé",
}


class Ipc:
    def __init__(self, cfg: AgentConfig):
        self.base = f"http://{cfg.ipc_host}:{cfg.ipc_port}"
        self.cfg = cfg

    def call(self, method, path, body=None, timeout=3):
        token = self.cfg.ipc_token_path.read_text().strip()
        req = urllib.request.Request(
            self.base + path,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"X-OGK-Token": token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


def fmt_min(sec):
    if sec is None:
        return "không giới hạn"
    m = max(0, int(sec)) // 60
    h, rm = divmod(m, 60)
    if h:
        return f"{h} giờ {rm:02d} phút"
    return f"{m} phút"


def _humanize_audit(a: dict) -> str:
    ts = datetime.datetime.fromtimestamp(a.get("ts", 0)).strftime("%d/%m %H:%M")
    act_raw = a.get("action", "")
    fld_raw = a.get("field", "")
    act = AUDIT_ACTION_VI.get(act_raw, act_raw)
    fld = AUDIT_FIELD_VI.get(fld_raw, fld_raw)
    old_v = str(a.get("old") or "").strip()
    new_v = str(a.get("new") or "").strip()
    if fld_raw == "schedule":
        return f"• {ts} — Bố mẹ đã cập nhật lịch giờ dùng máy trong tuần."
    if fld:
        if old_v and new_v and len(old_v) < 35 and len(new_v) < 35:
            return f"• {ts} — {fld}: {old_v} → {new_v}"
        return f"• {ts} — Đã cập nhật: {fld}"
    if act_raw == "command_ADD_TIME" and new_v:
        return f"• {ts} — Bố mẹ cộng thêm {new_v} phút cho bé 🎉"
    if new_v and len(new_v) < 40:
        return f"• {ts} — {act} ({new_v})"
    return f"• {ts} — {act}"


class TrayApp:
    def __init__(self, cfg: AgentConfig):
        self.cfg, self.ipc, self.platform = cfg, Ipc(cfg), get_platform()
        self.last_note, self.state, self.win, self.icon = 0, {}, None, None
        self.grace_win = None
        self.lock_win = None
        self._lock_guard_id = None
        self._lock_note_draft = ""
        self._exchange_win = None
        self._dev_bypass = False

    def run(self):
        import tkinter as tk
        self.tk = tk
        self.root = tk.Tk()
        self.root.withdraw()
        self._start_icon()
        self.root.after(400, self.poll)
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
            pystray.MenuItem("Mở góc nhỏ của bé", lambda: self.root.after(0, self.open_window), default=True),
            pystray.MenuItem("Xin thêm giờ", lambda: self.root.after(0, lambda: self.open_window(1))),
            pystray.MenuItem("Đổi điểm học lấy giờ chơi", lambda: self.root.after(0, self.open_exchange_popup)),
            pystray.MenuItem("Open Guardian Kids làm gì?", lambda: self.root.after(0, self.show_about)),
        )
        self.icon = pystray.Icon("OGK", self._image(GREEN), "Open Guardian Kids", menu)
        self.icon.run_detached()
        self.icon.notify("Open Guardian Kids đang đồng hành cùng bé nhé!", "Chào bé 🌱")

    def telemetry(self):
        try:
            self.ipc.call(
                "POST",
                "/v1/telemetry",
                {
                    "idle": self.platform.idle_seconds(),
                    "locked": self.platform.session_locked(),
                    "fg": self.platform.foreground_process(),
                },
            )
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
        if s.get("state") != "locked":
            self._dev_bypass = False
        color = RED if s.get("state") in ("locked", "suspended") else SUN if (s.get("remaining_sec") or 9999) < 600 else GREEN
        if self.icon:
            rem = s.get("remaining_sec")
            self.icon.icon = self._image(color)
            self.icon.title = "Open Guardian Kids · " + (
                "tài khoản không bị giới hạn"
                if s.get("applies") is False
                else (f"còn {fmt_min(rem)}" if rem is not None else "không giới hạn")
            )
        if self.win and self.win.winfo_exists():
            self.update_window()
        self.sync_lock_screen(s)
        if self.grace_win and self.grace_win.winfo_exists():
            left = s.get("grace_left")
            if s.get("state") != "grace" or left is None:
                self.grace_win.destroy()
            else:
                self.grace_lbl.config(text=f"⏳ Máy sẽ tạm nghỉ sau {left} giây. Bé lưu bài ngay nhé!")

    def show_note(self, n):
        k = n["kind"]
        if k == "blocked":
            return self.show_block(n)
        if k == "grace":
            return self.show_grace(n)
        if k == "locked":
            return self.show_locked(n)
        if k in ("warning", "eye_break", "bonus", "policy", "clock", "resumed", "unenrolled", "wipe", "integrity", "error"):
            if self.icon:
                self.icon.notify(n["body"], n["title"])
            if k in ("warning", "eye_break", "bonus"):
                self.popup(n["title"], n["body"])

    def popup(self, title, body, buttons=None, accent=GREEN):
        tk = self.tk
        w = tk.Toplevel(self.root)
        w.title(title)
        w.attributes("-topmost", True)
        w.configure(bg=BG, padx=24, pady=20)
        tk.Label(w, text=title, bg=BG, fg=accent, font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(w, text=body, bg=BG, fg=TEXT, justify="left", wraplength=440, font=("Segoe UI", 11)).pack(anchor="w", pady=(10, 16))
        row = tk.Frame(w, bg=BG)
        row.pack(anchor="e")
        for idx, (text, cmd) in enumerate((buttons or []) + [("Đóng", None)]):
            is_primary = idx == 0 and buttons
            tk.Button(
                row,
                text=text,
                command=(lambda c=cmd, ww=w: (c() if c else None, ww.destroy())),
                bg=GREEN if is_primary else CARD,
                fg="white" if is_primary else TEXT,
                font=("Segoe UI", 10, "bold"),
                relief="flat",
                padx=14,
                pady=7,
                cursor="hand2",
            ).pack(side="left", padx=5)
        return w

    def show_block(self, n):
        what = "Ứng dụng" if n.get("kind_of") == "app" else "Trang web"
        body = (
            f"• {what}: {n.get('subject')}\n"
            f"• Nhóm: {n.get('category')}\n"
            f"• Quy định do: {n.get('by')}\n\n"
            f"{n['body']}"
        )
        self.popup(
            "Ồ, tạm thời chưa mở được nhé!",
            body,
            [
                ("🙋 Xin thêm giờ", lambda: self.open_window(1)),
                ("🔓 Báo chặn nhầm", lambda: self.open_window(1, n.get("subject"))),
            ],
            accent=SUN,
        )

    def show_grace(self, n):
        self.grace_win = self.popup(
            "⏰ Sắp hết giờ rồi bé ơi!",
            n["body"],
            [("🙋 Xin thêm giờ", lambda: self.open_window(1))],
            accent=SUN,
        )
        self.grace_lbl = self.tk.Label(self.grace_win, text="", bg=BG, fg=RED, font=("Segoe UI", 12, "bold"))
        self.grace_lbl.pack(pady=(6, 0))

    def show_locked(self, n):
        self.sync_lock_screen(self.state or {"state": "locked", "lock_text": n["body"]})

    # ------------------------------------------------------------------ màn hình khóa mềm bám mọi Virtual Desktop
    def _destroy_lock_screen(self):
        if self._lock_guard_id is not None:
            try:
                self.root.after_cancel(self._lock_guard_id)
            except Exception:
                pass
            self._lock_guard_id = None
        if self.lock_win is not None:
            try:
                if self.lock_win.winfo_exists():
                    self.lock_win.destroy()
            except Exception:
                pass
            self.lock_win = None

    def sync_lock_screen(self, s):
        """Màn hình khóa mềm toàn màn hình trong phiên của trẻ:
        - KHÔNG gọi LockWorkStation (tránh kẹt máy lúc khởi động).
        - Tự theo dõi Virtual Desktop (chống trick Win+Ctrl+D / chuyển Desktop).
        - Tự mở ngay khi bố mẹ bấm Mở khóa, cộng thêm giờ hoặc hết thời gian khóa.
        """
        locked = s.get("state") == "locked" and s.get("applies") is not False and not self._dev_bypass
        alive = self.lock_win is not None and self.lock_win.winfo_exists()
        if not locked:
            self._lock_note_draft = ""
            self._destroy_lock_screen()
            return
        if not alive:
            self._build_lock_screen()
            self._schedule_lock_guard()
        self._update_lock_labels(s)

    def _update_lock_labels(self, s):
        if not (self.lock_win and self.lock_win.winfo_exists()):
            return
        self.lock_msg.config(text=s.get("lock_text") or "Bố mẹ đang tạm khóa máy để bé nghỉ ngơi nhé.")
        until = s.get("manual_until")
        if s.get("reason") == "manual" and until:
            mins_left = max(1, int(until - _t.time()) // 60 + 1)
            self.lock_sub.config(text=f"Máy sẽ tự mở sau khoảng {mins_left} phút, hoặc ngay khi bố mẹ bấm Mở khóa.")
        else:
            self.lock_sub.config(text="Máy sẽ tự mở ngay khi bố mẹ cộng thêm giờ hoặc tới giờ được dùng nhé.")

    def _schedule_lock_guard(self):
        if self._lock_guard_id is not None:
            try:
                self.root.after_cancel(self._lock_guard_id)
            except Exception:
                pass
        self._lock_guard_id = self.root.after(400, self._lock_guard_tick)

    def _lock_guard_tick(self):
        self._lock_guard_id = None
        s = self.state or {}
        if s.get("state") != "locked" or s.get("applies") is False or self._dev_bypass:
            self._destroy_lock_screen()
            return
        if not (self.lock_win and self.lock_win.winfo_exists()):
            self._build_lock_screen()
            self._update_lock_labels(s)
            self._schedule_lock_guard()
            return
        try:
            hwnd = self.lock_win.winfo_id()
            if not self.platform.is_window_on_current_desktop(hwnd):
                try:
                    self._lock_note_draft = self.lock_note.get()
                except Exception:
                    pass
                self._recreate_lock_screen(s)
                self._schedule_lock_guard()
                return
            self.lock_win.attributes("-topmost", True)
            self.lock_win.lift()
            if not self.platform.is_window_foreground(hwnd):
                self.platform.bring_window_to_front(hwnd)
        except Exception:
            pass
        self._schedule_lock_guard()

    def _recreate_lock_screen(self, s):
        if self.lock_win is not None:
            try:
                if self.lock_win.winfo_exists():
                    self.lock_win.destroy()
            except Exception:
                pass
            self.lock_win = None
        self._build_lock_screen()
        self._update_lock_labels(s)

    def _build_lock_screen(self):
        tk, bg = self.tk, "#14302a"
        w = self.lock_win = tk.Toplevel(self.root)
        w.title("Open Guardian Kids · Tạm nghỉ một chút nhé")
        w.configure(bg=bg)
        rect = self.platform.virtual_screen_rect()
        if rect:
            x, y, vw, vh = rect
            w.overrideredirect(True)
            w.geometry(f"{vw}x{vh}+{x}+{y}")
        else:
            w.attributes("-fullscreen", True)
        w.attributes("-topmost", True)
        w.protocol("WM_DELETE_WINDOW", lambda: None)
        for seq in ("<Alt-F4>", "<Escape>", "<Alt-Tab>", "<Control-Escape>"):
            w.bind(seq, lambda _e: "break")
        if os.environ.get("OGK_DEV_UNLOCK") == "1":
            w.bind("<Control-Shift-Alt-U>", lambda _e: self._dev_emergency_close())

        box = tk.Frame(w, bg=bg)
        box.place(relx=0.5, rely=0.47, anchor="center")
        tk.Label(box, text="🌿", bg=bg, fg="white", font=("Segoe UI Emoji", 54)).pack()
        tk.Label(box, text="Nghỉ ngơi một chút nhé bé ơi!", bg=bg, fg="white", font=("Segoe UI", 28, "bold")).pack(pady=(6, 8))
        self.lock_msg = tk.Label(box, bg=bg, fg="#d7f2e5", font=("Segoe UI", 14), wraplength=660, justify="center")
        self.lock_msg.pack()
        self.lock_sub = tk.Label(box, bg=bg, fg="#9fc6b8", font=("Segoe UI", 11), wraplength=660, justify="center")
        self.lock_sub.pack(pady=(6, 18))

        tk.Label(box, text="Bé muốn nhắn gì cho bố mẹ không?", bg=bg, fg="white", font=("Segoe UI", 11, "bold")).pack()
        quick_row = tk.Frame(box, bg=bg)
        quick_row.pack(pady=(6, 6))
        for q in ("Con đang làm bài tập", "Cho con thêm 15 phút nha"):
            tk.Button(
                quick_row,
                text=q,
                bg="#1f493f",
                fg="#e4f5ec",
                font=("Segoe UI", 10),
                relief="flat",
                padx=10,
                pady=4,
                cursor="hand2",
                command=lambda text=q: (self.lock_note.delete(0, "end"), self.lock_note.insert(0, text)),
            ).pack(side="left", padx=4)

        self.lock_note = tk.Entry(box, width=48, font=("Segoe UI", 12), relief="flat")
        self.lock_note.pack(ipady=6, pady=6)
        if self._lock_note_draft:
            self.lock_note.insert(0, self._lock_note_draft)

        row = tk.Frame(box, bg=bg)
        row.pack(pady=10)
        btn = dict(font=("Segoe UI", 11, "bold"), padx=16, pady=8, relief="flat", cursor="hand2")
        tk.Button(row, text="🙋 Xin thêm 15 phút", bg=GREEN, fg="white", command=self._lock_request, **btn).pack(side="left", padx=6)
        tk.Button(row, text="🔄 Đăng xuất", bg="white", fg=TEXT, command=lambda: self.platform_power("logoff"), **btn).pack(side="left", padx=6)
        tk.Button(row, text="⏻ Tắt máy", bg="white", fg=TEXT, command=lambda: self.platform_power("shutdown"), **btn).pack(side="left", padx=6)

        self.lock_status = tk.Label(box, bg=bg, fg="#f5c65c", font=("Segoe UI", 11, "bold"))
        self.lock_status.pack(pady=4)
        tk.Label(
            box,
            text="Cần giúp đỡ khẩn cấp? Gọi 111 (Tổng đài quốc gia bảo vệ trẻ em) hoặc nhờ người lớn.",
            bg=bg,
            fg="#9fc6b8",
            font=("Segoe UI", 10),
        ).pack(pady=(16, 0))
        try:
            w.update_idletasks()
            self.platform.bring_window_to_front(w.winfo_id())
        except Exception:
            pass

    def _dev_emergency_close(self):
        self._dev_bypass = True
        self._destroy_lock_screen()

    def _lock_request(self):
        try:
            code, res = self.ipc.call(
                "POST",
                "/v1/request",
                {"kind": "add-time", "subject": "", "minutes": 15, "message": self.lock_note.get().strip()},
                timeout=10,
            )
            msg = res.get("message") or (
                "Đã gửi cho bố mẹ rồi nè! Máy sẽ tự mở ngay khi bố mẹ đồng ý 💚" if code in (200, 202) else res.get("error", "Có lỗi.")
            )
        except Exception:
            msg = "Chưa gửi được, bé thử lại sau vài giây nhé."
        self.lock_status.config(text=msg)

    def platform_power(self, what):
        args = ["shutdown", "/l"] if what == "logoff" else ["shutdown", "/s", "/t", "0"]
        try:
            subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            pass

    def show_about(self):
        self.popup("Open Guardian Kids làm gì?", ABOUT)

    # ------------------------------------------------------------------ cửa sổ chính thân thiện cho bé ("Góc nhỏ của bé")
    def open_window(self, tab=0, subject=None):
        from tkinter import ttk
        tk = self.tk
        if not (self.win and self.win.winfo_exists()):
            self.win = tk.Toplevel(self.root)
            self.win.title("Open Guardian Kids · Góc nhỏ của bé")
            self.win.geometry("540x680")
            self.win.configure(bg=BG)
            self.win.minsize(480, 600)

            style = ttk.Style(self.win)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            style.configure("OGK.TNotebook", background=BG, borderwidth=0)
            style.configure("OGK.TNotebook.Tab", background=GREEN_SOFT, foreground=TEXT, padding=(14, 8), font=("Segoe UI", 10, "bold"))
            style.map("OGK.TNotebook.Tab", background=[("selected", GREEN)], foreground=[("selected", "white")])

            self.nb = ttk.Notebook(self.win, style="OGK.TNotebook")
            self.nb.pack(fill="both", expand=True, padx=10, pady=10)
            self.tabs = [tk.Frame(self.nb, bg=BG, padx=12, pady=10) for _ in range(3)]
            for f, title in zip(self.tabs, ("🌱 Góc của bé", "🙋 Xin bố mẹ", "📜 Quy định của bố mẹ")):
                self.nb.add(f, text=title)
            self.build_home(self.tabs[0])
            self.build_help(self.tabs[1])
            self.build_audit(self.tabs[2])

        self.nb.select(min(tab, 2))
        if subject:
            self._set_req_kind("false-block")
            self.subject_var.set(subject)
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.win.after(250, lambda: self.win.winfo_exists() and self.win.attributes("-topmost", False))
        self.update_window()
        if tab == 1:
            self.load_my_requests()
        elif tab == 2:
            self.load_audit()

    def build_home(self, f):
        tk = self.tk
        garden_card = tk.Frame(f, bg=CARD, highlightbackground=LINE, highlightthickness=1, padx=14, pady=12)
        garden_card.pack(fill="x", pady=(0, 10))
        top_row = tk.Frame(garden_card, bg=CARD)
        top_row.pack(fill="x")
        self.canvas = tk.Canvas(top_row, width=148, height=138, bg=SKY, highlightthickness=0)
        self.canvas.pack(side="left", padx=(0, 14))
        text_col = tk.Frame(top_row, bg=CARD)
        text_col.pack(side="left", fill="both", expand=True)
        self.lbl_hello = tk.Label(text_col, text="Chào bé yêu! 🌱", bg=CARD, fg=GREEN, font=("Segoe UI", 13, "bold"), anchor="w")
        self.lbl_hello.pack(fill="x")
        self.lbl_time = tk.Label(text_col, text="Đang kiểm tra giờ…", bg=CARD, fg=TEXT, font=("Segoe UI", 19, "bold"), anchor="w")
        self.lbl_time.pack(fill="x", pady=(4, 2))
        self.lbl_sub = tk.Label(text_col, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 10), justify="left", wraplength=300, anchor="w")
        self.lbl_sub.pack(fill="x")

        self.prog_canvas = tk.Canvas(garden_card, height=14, bg=GREEN_SOFT, highlightthickness=0)
        self.prog_canvas.pack(fill="x", pady=(10, 4))
        self.lbl_stats = tk.Label(garden_card, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 9, "bold"), anchor="w")
        self.lbl_stats.pack(fill="x")

        btn_row = tk.Frame(f, bg=BG)
        btn_row.pack(fill="x", pady=(2, 10))
        tk.Button(
            btn_row,
            text="🙋 Xin thêm giờ",
            bg=GREEN,
            fg="white",
            font=("Segoe UI", 12, "bold"),
            relief="flat",
            pady=12,
            cursor="hand2",
            command=lambda: (self._set_req_kind("add-time"), self.nb.select(1), self.load_my_requests()),
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn_exchange = tk.Button(
            btn_row,
            text="⭐ Đổi điểm học",
            bg=SUN_SOFT,
            fg="#7c5200",
            font=("Segoe UI", 12, "bold"),
            relief="flat",
            pady=12,
            cursor="hand2",
            command=self.open_exchange_popup,
        )
        self.btn_exchange.pack(side="left", fill="x", expand=True, padx=(6, 0))

        used_card = tk.Frame(f, bg=CARD, highlightbackground=LINE, highlightthickness=1, padx=14, pady=10)
        used_card.pack(fill="both", expand=True)
        tk.Label(used_card, text="📋 Hôm nay bé đã dùng", bg=CARD, fg=TEXT, font=("Segoe UI", 11, "bold"), anchor="w").pack(fill="x", pady=(0, 6))
        self.lst = tk.Text(used_card, height=9, state="disabled", relief="flat", bg=BG, fg=TEXT, font=("Segoe UI", 10), padx=10, pady=8)
        self.lst.pack(fill="both", expand=True)
        tk.Label(
            f,
            text="Cần giúp đỡ khẩn cấp? Gọi Tổng đài 111 (miễn phí) · Không chụp màn hình, không đọc tin nhắn.",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 8),
        ).pack(pady=(6, 0))

    def draw_plant(self, mood):
        c = self.canvas
        c.delete("all")
        c.create_rectangle(0, 112, 148, 138, fill="#d9efe2", outline="")
        c.create_polygon(46, 102, 102, 102, 94, 132, 54, 132, fill=SOIL, outline="")
        c.create_rectangle(42, 96, 106, 105, fill="#a36b49", outline="")
        stem_top = 48 if mood == "happy" else (58 if mood == "ok" else 72)
        leaf_color = GREEN if mood != "sleepy" else "#8aa896"
        c.create_line(74, 96, 74, stem_top, width=6, fill=leaf_color)
        c.create_oval(38, stem_top + 4, 74, stem_top + 28, fill=leaf_color, outline="")
        c.create_oval(74, stem_top - 4, 112, stem_top + 22, fill=leaf_color, outline="")
        if mood == "happy":
            c.create_oval(64, 24, 84, 44, fill="#f7c948", outline="")
            c.create_oval(69, 29, 79, 39, fill="#fff6d6", outline="")
        elif mood == "sleepy":
            c.create_text(114, 34, text="z Z", font=("Segoe UI", 11, "bold"), fill="#5a7684")
        face = {"happy": "◠ ‿ ◠", "ok": "• ‿ •", "sleepy": "- ﹏ -"}.get(mood, "• ‿ •")
        c.create_text(74, 116, text=face, font=("Segoe UI", 10, "bold"), fill="white")

    def update_window(self):
        s = self.state or {}
        child_name = s.get("child") or "bé"
        self.lbl_hello.config(text=f"Chào {child_name}! 🌱")
        if s.get("applies") is False:
            self.draw_plant("ok")
            self.lbl_time.config(text="Không bị giới hạn")
            self.lbl_sub.config(text="Tài khoản này không áp dụng giới hạn giờ dùng máy.")
            return
        garden = s.get("garden") or {}
        mood = garden.get("mood", "ok")
        self.draw_plant(mood)
        rem = s.get("remaining_sec")
        used = s.get("used_sec", 0)
        edu = garden.get("education_sec", 0)
        self.lbl_time.config(
            text=(f"Còn {fmt_min(rem)}" if rem is not None else "Không giới hạn"),
            fg=RED if (rem is not None and rem <= 0) else (SUN if (rem is not None and rem < 600) else TEXT),
        )
        msg = {
            "sleepy": "Bé ngồi máy hơi lâu rồi, đứng dậy vươn vai nhìn xa cho đỡ mỏi mắt nhé!",
            "happy": "Cây xanh mướt vì hôm nay bé học tập rất chăm chỉ nè!",
            "ok": "Cây nhỏ đang lớn lên cùng bé mỗi ngày!",
        }.get(mood, "Cây nhỏ đang lớn lên cùng bé!")
        if s.get("stale"):
            msg += " (Máy đang ngoại tuyến lâu ngày, một số nhóm giải trí tạm khóa.)"
        self.lbl_sub.config(text=msg)

        total = used + max(0, rem or 0)
        pct = min(1.0, used / total) if (rem is not None and total > 0) else 0.0
        pc = self.prog_canvas
        pc.delete("all")
        w = max(pc.winfo_width(), 440)
        bar_col = RED if (rem is not None and rem <= 0) else (SUN if (rem is not None and rem < 600) else GREEN)
        pc.create_rectangle(0, 0, int(w * pct), 14, fill=bar_col, outline="")
        self.lbl_stats.config(text=f"⏱ Đã dùng hôm nay: {fmt_min(used)}   ·   📚 Học tập: {fmt_min(edu)}")

        ltp = s.get("ltp") or {}
        avail = int(ltp.get("available_min", 0))
        if not ltp.get("enabled"):
            self.btn_exchange.config(text="⭐ Đổi điểm học (tắt)", bg="#ecefed", fg=MUTED)
        elif avail > 0:
            self.btn_exchange.config(text=f"⭐ Đổi ngay +{avail} phút!", bg=SUN, fg="white")
        else:
            self.btn_exchange.config(text="⭐ Đổi điểm học (0 phút)", bg=SUN_SOFT, fg="#7c5200")

        try:
            _, t = self.ipc.call("GET", "/v1/today")
            lines = []
            if t.get("apps"):
                lines.append("💻 Ứng dụng đã mở:")
                for n, v in t["apps"][:5]:
                    lines.append(f"   • {n.replace('.exe', '')}: {fmt_min(v)}")
            if t.get("domains"):
                if lines:
                    lines.append("")
                lines.append("🌐 Trang web đã xem:")
                for n, v in t["domains"][:5]:
                    lines.append(f"   • {n}: {fmt_min(v)}")
            groups = s.get("groups") or {}
            if groups:
                if lines:
                    lines.append("")
                lines.append("🎯 Giờ theo nhóm hôm nay:")
                for g in groups.values():
                    lines.append(f"   • {g['label']}: đã dùng {fmt_min(g['used_sec'])} / {fmt_min(g['budget_sec'])}")
            if not lines:
                lines = ["Hôm nay bé mới bắt đầu dùng máy. Chúc bé học tập vui vẻ nhé! 🌟"]
            self.lst.config(state="normal")
            self.lst.delete("1.0", "end")
            self.lst.insert("1.0", "\n".join(lines))
            self.lst.config(state="disabled")
        except Exception:
            pass

    def open_exchange_popup(self):
        tk = self.tk
        if self._exchange_win and self._exchange_win.winfo_exists():
            self._exchange_win.lift()
            return
        w = self._exchange_win = tk.Toplevel(self.root)
        w.title("⭐ Đổi giờ học lấy giờ chơi")
        w.configure(bg=BG, padx=22, pady=18)
        w.attributes("-topmost", True)
        ltp = (self.state or {}).get("ltp") or {}
        avail = int(ltp.get("available_min", 0))
        tk.Label(w, text="⭐ Học chăm được thưởng giờ chơi!", bg=BG, fg=GREEN, font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(
            w,
            text="Khi bé học trên các trang giáo dục (Scratch, Khan Academy, Violympic…), bé sẽ tích lũy thêm phút chơi tự động!",
            bg=BG,
            fg=MUTED,
            wraplength=380,
            justify="left",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(6, 10))
        lbl = tk.Label(
            w,
            text=(f"Bé đang có: {avail} phút thưởng để đổi 🎉" if ltp.get("enabled") else "Bố mẹ chưa bật tính năng này."),
            bg=CARD,
            fg=TEXT,
            font=("Segoe UI", 12, "bold"),
            padx=12,
            pady=10,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        lbl.pack(fill="x", pady=(0, 12))
        status_lbl = tk.Label(w, text="", bg=BG, fg=GREEN, font=("Segoe UI", 10, "bold"), wraplength=380, justify="left")

        def do_ex(m):
            try:
                code, res = self.ipc.call("POST", "/v1/exchange", {"minutes": m})
                if code == 200:
                    status_lbl.config(text=f"Tuyệt vời! Bé vừa đổi thành công +{res['minutes']} phút chơi 🎉", fg=GREEN)
                    self.poll()
                    new_avail = int(((self.state or {}).get("ltp") or {}).get("available_min", max(0, avail - m)))
                    lbl.config(text=f"Bé còn lại: {new_avail} phút thưởng")
                else:
                    status_lbl.config(text=res.get("error", "Chưa đổi được."), fg=RED)
            except Exception:
                status_lbl.config(text="Không kết nối được, bé thử lại nhé.", fg=RED)

        row = tk.Frame(w, bg=BG)
        row.pack(fill="x", pady=(0, 8))
        for m in (5, 10, 15):
            tk.Button(
                row,
                text=f"Đổi +{m} phút",
                bg=GREEN if (ltp.get("enabled") and avail >= m) else "#d8e3dd",
                fg="white" if (ltp.get("enabled") and avail >= m) else MUTED,
                font=("Segoe UI", 11, "bold"),
                relief="flat",
                pady=9,
                cursor="hand2",
                command=lambda mins=m: do_ex(mins),
            ).pack(side="left", fill="x", expand=True, padx=4)
        status_lbl.pack(anchor="w", pady=(4, 8))
        tk.Button(w, text="Đóng", bg=CARD, fg=TEXT, font=("Segoe UI", 10, "bold"), relief="flat", padx=16, pady=6, command=w.destroy).pack(anchor="e")

    def build_help(self, f):
        tk = self.tk
        self.kind = tk.StringVar(value="add-time")
        self.min_var = tk.IntVar(value=15)
        self.subject_var = tk.StringVar()

        mode_row = tk.Frame(f, bg=BG)
        mode_row.pack(fill="x", pady=(0, 10))
        self.btn_mode_time = tk.Button(
            mode_row, text="⏰ Xin thêm giờ", font=("Segoe UI", 11, "bold"), relief="flat", pady=9, cursor="hand2",
            command=lambda: self._set_req_kind("add-time"),
        )
        self.btn_mode_time.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.btn_mode_block = tk.Button(
            mode_row, text="🔓 Báo chặn nhầm", font=("Segoe UI", 11, "bold"), relief="flat", pady=9, cursor="hand2",
            command=lambda: self._set_req_kind("false-block"),
        )
        self.btn_mode_block.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self.frm_minutes = tk.Frame(f, bg=CARD, highlightbackground=LINE, highlightthickness=1, padx=12, pady=10)
        self.frm_minutes.pack(fill="x", pady=(0, 10))
        tk.Label(self.frm_minutes, text="Bé muốn xin thêm bao nhiêu phút?", bg=CARD, fg=TEXT, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        min_row = tk.Frame(self.frm_minutes, bg=CARD)
        min_row.pack(fill="x", pady=(6, 0))
        self.min_btns = {}
        for m in (10, 15, 30):
            b = tk.Button(
                min_row, text=f"+{m} phút", font=("Segoe UI", 11, "bold"), relief="flat", pady=8, cursor="hand2",
                command=lambda val=m: self._select_minutes(val),
            )
            b.pack(side="left", fill="x", expand=True, padx=4)
            self.min_btns[m] = b

        self.frm_subject = tk.Frame(f, bg=CARD, highlightbackground=LINE, highlightthickness=1, padx=12, pady=10)
        tk.Label(self.frm_subject, text="Tên trang web hoặc ứng dụng bị chặn nhầm:", bg=CARD, fg=TEXT, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Entry(self.frm_subject, textvariable=self.subject_var, font=("Segoe UI", 11), relief="flat", bg=BG).pack(fill="x", ipady=5, pady=(6, 0))

        msg_card = tk.Frame(f, bg=CARD, highlightbackground=LINE, highlightthickness=1, padx=12, pady=10)
        msg_card.pack(fill="x", pady=(0, 10))
        tk.Label(msg_card, text="Bấm chọn nhanh lý do (hoặc tự gõ):", bg=CARD, fg=TEXT, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        chips_top = tk.Frame(msg_card, bg=CARD)
        chips_top.pack(fill="x", pady=(6, 4))
        chips_bot = tk.Frame(msg_card, bg=CARD)
        chips_bot.pack(fill="x", pady=(0, 6))
        for idx, reason in enumerate(QUICK_REASONS):
            parent = chips_top if idx < 2 else chips_bot
            tk.Button(
                parent,
                text=reason,
                bg=GREEN_SOFT,
                fg=TEXT,
                font=("Segoe UI", 9),
                relief="flat",
                padx=8,
                pady=4,
                cursor="hand2",
                command=lambda r=reason: (self.msg.delete("1.0", "end"), self.msg.insert("1.0", r)),
            ).pack(side="left", fill="x", expand=True, padx=3)
        self.msg = tk.Text(msg_card, height=3, font=("Segoe UI", 10), relief="flat", bg=BG, padx=8, pady=6)
        self.msg.pack(fill="x")

        tk.Button(
            f,
            text="📨 Gửi cho bố mẹ",
            bg=GREEN,
            fg="white",
            font=("Segoe UI", 12, "bold"),
            relief="flat",
            pady=11,
            cursor="hand2",
            command=self.send_request,
        ).pack(fill="x", pady=(0, 6))
        self.help_status = tk.Label(f, text="", bg=BG, fg=GREEN, font=("Segoe UI", 10, "bold"), wraplength=460, justify="left")
        self.help_status.pack(anchor="w")

        tk.Label(f, text="📬 Phản hồi của bố mẹ gần đây:", bg=BG, fg=TEXT, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 4))
        self.req_hist = tk.Text(f, height=5, state="disabled", relief="flat", bg=CARD, fg=TEXT, font=("Segoe UI", 9), padx=10, pady=6)
        self.req_hist.pack(fill="both", expand=True)

        self._set_req_kind("add-time")
        self._select_minutes(15)

    def _set_req_kind(self, kind):
        self.kind.set(kind)
        is_time = kind == "add-time"
        self.btn_mode_time.config(bg=GREEN if is_time else CARD, fg="white" if is_time else TEXT)
        self.btn_mode_block.config(bg=GREEN if not is_time else CARD, fg="white" if not is_time else TEXT)
        if is_time:
            self.frm_subject.pack_forget()
            self.frm_minutes.pack(fill="x", pady=(0, 10), after=self.btn_mode_time.master)
        else:
            self.frm_minutes.pack_forget()
            self.frm_subject.pack(fill="x", pady=(0, 10), after=self.btn_mode_time.master)

    def _select_minutes(self, val):
        self.min_var.set(val)
        for m, b in self.min_btns.items():
            b.config(bg=GREEN if m == val else GREEN_SOFT, fg="white" if m == val else TEXT)

    def send_request(self):
        try:
            code, res = self.ipc.call(
                "POST",
                "/v1/request",
                {
                    "kind": self.kind.get(),
                    "subject": self.subject_var.get().strip(),
                    "minutes": self.min_var.get(),
                    "message": self.msg.get("1.0", "end").strip(),
                },
                timeout=10,
            )
            ok = code in (200, 202)
            txt = res.get("message") or ("Đã gửi cho bố mẹ rồi nè! Chờ bố mẹ trả lời nhé 💚" if ok else res.get("error", "Có lỗi xảy ra."))
            self.help_status.config(text=txt, fg=GREEN if ok else RED)
            if ok:
                self.msg.delete("1.0", "end")
                self.load_my_requests()
        except Exception:
            self.help_status.config(text="Chưa gửi được lúc này, bé thử lại nhé.", fg=RED)

    def load_my_requests(self):
        st_vi = {"pending": "⏳ đang chờ bố mẹ", "approved": "✅ bố mẹ đồng ý!", "denied": "❌ bố mẹ chưa đồng ý"}
        try:
            code, res = self.ipc.call("GET", "/v1/requests", timeout=6)
            rows = res.get("items", []) if code == 200 else []
            lines = []
            for r in rows[:6]:
                t = datetime.datetime.fromtimestamp(r.get("created_at", 0)).strftime("%d/%m %H:%M")
                what = f"Xin +{r.get('minutes', 15)} phút" if r.get("kind") == "add-time" else f"Báo chặn nhầm ({r.get('subject')})"
                lines.append(f"• {t} — {what}: {st_vi.get(r.get('status'), r.get('status'))}")
            text = "\n".join(lines) if lines else "Bé chưa gửi yêu cầu nào."
        except Exception:
            text = "Chưa tải được danh sách yêu cầu."
        self.req_hist.config(state="normal")
        self.req_hist.delete("1.0", "end")
        self.req_hist.insert("1.0", text)
        self.req_hist.config(state="disabled")

    def build_audit(self, f):
        tk = self.tk
        tk.Label(f, text="📜 Bố mẹ đã chỉnh những gì? (bé luôn xem được)", bg=BG, fg=TEXT, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(
            f,
            text="Mọi thay đổi giờ chơi hoặc danh sách trang web đều hiện rõ ở đây để hai bên cùng biết.",
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
            wraplength=460,
            justify="left",
        ).pack(anchor="w", pady=(2, 8))
        self.audit_txt = tk.Text(f, height=20, state="disabled", relief="flat", bg=CARD, fg=TEXT, font=("Segoe UI", 10), padx=12, pady=10)
        self.audit_txt.pack(fill="both", expand=True, pady=(0, 8))
        tk.Button(f, text="🔄 Làm mới danh sách", bg=GREEN_SOFT, fg=TEXT, font=("Segoe UI", 10, "bold"), relief="flat", padx=14, pady=7, cursor="hand2", command=self.load_audit).pack(anchor="w")
        self.load_audit()

    def load_audit(self):
        try:
            code, res = self.ipc.call("GET", "/v1/audit", timeout=8)
            rows = res.get("items", []) if code == 200 else []
            text = "\n".join(_humanize_audit(a) for a in rows[:40]) or res.get("error", "Chưa có thay đổi nào.")
        except Exception:
            text = "Chưa tải được nhật ký lúc này (máy đang ngoại tuyến)."
        self.audit_txt.config(state="normal")
        self.audit_txt.delete("1.0", "end")
        self.audit_txt.insert("1.0", text)
        self.audit_txt.config(state="disabled")


def main():
    plat = get_platform()
    if plat.acquire_single_instance("Local\\OGK_Tray_SingleInstance") is None:
        return
    TrayApp(AgentConfig.load()).run()


if __name__ == "__main__":
    main()
