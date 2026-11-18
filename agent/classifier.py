"""Quyết định chặn/cho phép tên miền. Thứ tự ưu tiên:
miễn chặn bất biến > luật tên miền của phụ huynh (khớp dài nhất) > DoH > fail-closed quá hạn > nhóm bị chặn > ngân sách nhóm."""
from __future__ import annotations

from dataclasses import dataclass

from common.schema import CATEGORIES, FAIL_CLOSED_CATEGORIES, is_exempt_host, match_suffix, normalize_host

SYSTEM_BY = "Hệ thống (quy tắc mặc định cho trẻ 6–11 tuổi)"


@dataclass
class Verdict:
    blocked: bool = False
    code: str = "allow"      # allow | exempt | parent_allow | parent_block | doh | stale | category | group_budget
    category: str | None = None
    rule: str = ""
    by: str = ""
    reason: str = ""


class DomainFilter:
    def __init__(self, get_policy, group_exhausted=lambda g: False, is_stale=lambda: False, extra_exempt=frozenset(), suspended=lambda: False):
        self._policy, self._exhausted, self._stale = get_policy, group_exhausted, is_stale
        self.extra_exempt, self._suspended = frozenset(extra_exempt), suspended

    def check(self, qname: str) -> Verdict:
        host = normalize_host(qname)
        p = self._policy()
        if is_exempt_host(host, self.extra_exempt):
            return Verdict(False, "exempt", rule=host)
        if p is None or self._suspended():
            return Verdict(False, "allow")
        parent_by = f"Phụ huynh ({p.set_by})"
        b, a = match_suffix(host, p.d_blocked), match_suffix(host, p.d_allowed)
        if b or a:
            if a and (not b or a.count(".") > b.count(".")):
                return Verdict(False, "parent_allow", rule=a, by=parent_by)
            return Verdict(True, "parent_block", rule=b, by=parent_by,
                           reason=f"Phụ huynh đã chặn tên miền {b}.")
        if p.block_doh and match_suffix(host, p.doh):
            return Verdict(True, "doh", rule=host, by="Hệ thống (chống vượt rào DNS-over-HTTPS)",
                           reason="Máy chủ DNS-over-HTTPS có thể dùng để né bộ lọc nên bị chặn.")
        cat = p.category_of(host)
        label = CATEGORIES.get(cat, cat)
        if self._stale() and cat in FAIL_CLOSED_CATEGORIES:
            return Verdict(True, "stale", cat, host, "Hệ thống (an toàn khi mất kết nối)",
                           f"Máy chưa kết nối máy chủ của phụ huynh quá {p.stale_days} ngày nên nhóm “{label}” tạm khóa.")
        if cat in p.blocked_cats:
            by = SYSTEM_BY if cat in p.system_cats else parent_by
            return Verdict(True, "category", cat, cat, by, f"Trang này thuộc nhóm “{label}”, nhóm này đang bị chặn.")
        if self._exhausted(cat):
            return Verdict(True, "group_budget", cat, cat, parent_by, f"Con đã dùng hết ngân sách hôm nay của nhóm “{label}”.")
        return Verdict(False, "allow", cat)
