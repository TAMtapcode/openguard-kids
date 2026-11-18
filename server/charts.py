"""Biểu đồ SVG dựng phía server (không cần thư viện JS, không gửi dữ liệu ra ngoài)."""
from markupsafe import Markup, escape


def fmt_duration(sec: int) -> str:
    sec = int(sec or 0)
    h, m = divmod(sec // 60, 60)
    return f"{h} giờ {m:02d} phút" if h else f"{m} phút"


def bar_chart(points: list[dict], height: int = 170) -> Markup:
    """Biểu đồ cột 7 ngày: points = [{label, seconds}]."""
    w, pad_b, pad_t = 560, 28, 22
    mx = max([p["seconds"] for p in points] + [1800])
    n = len(points)
    slot = w / n
    parts = [f'<svg class="chart" viewBox="0 0 {w} {height}" role="img" aria-label="Thời lượng sử dụng máy theo ngày">']
    for i, p in enumerate(points):
        h = (height - pad_b - pad_t) * p["seconds"] / mx
        x = i * slot + slot * 0.2
        y = height - pad_b - h
        last = i == n - 1
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{slot*0.6:.1f}" height="{max(h, 2):.1f}" rx="6" class="bar{" bar-today" if last else ""}"/>')
        parts.append(f'<text x="{x + slot*0.3:.1f}" y="{y - 6:.1f}" class="bar-val" text-anchor="middle">{p["seconds"] // 60}′</text>')
        parts.append(f'<text x="{x + slot*0.3:.1f}" y="{height - 8}" class="bar-lbl" text-anchor="middle">{escape(p["label"])}</text>')
    parts.append("</svg>")
    return Markup("".join(parts))
