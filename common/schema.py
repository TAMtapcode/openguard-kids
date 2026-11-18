"""Hằng số và tiện ích dùng chung giữa Agent và Server."""
from __future__ import annotations

CATEGORIES = {
    "education": "Giáo dục",
    "entertainment": "Giải trí",
    "social": "Mạng xã hội",
    "game": "Trò chơi",
    "inappropriate": "Không phù hợp lứa tuổi",
    "uncategorized": "Chưa phân loại",
}

# Lược đồ sự kiện: chỉ các loại này được phép.
EVENT_TYPES = (
    "app_start", "app_stop", "domain_query", "blocked_app", "blocked_domain",
    "quota_warning", "locked", "unlock_request", "override_granted",
)
REQUEST_KINDS = ("add-time", "false-block")
COMMAND_ACTIONS = ("LOCK_NOW", "UNLOCK", "ADD_TIME", "WIPE_LOCAL", "POLICY_UPDATED")

# NT6 - danh sách miễn chặn vĩnh viễn (bất biến, không policy nào ghi đè được).
EXEMPT_SUFFIXES = (".edu.vn", ".gov.vn")
EXEMPT_DOMAINS = frozenset({
    "tongdai111.vn", "111.vn", "vnedu.vn", "moet.gov.vn", "moh.gov.vn", "redcross.org.vn",
})

# Smart Fail-Safe: nhóm nào fail-open / fail-closed khi mất kết nối quá hạn.
FAIL_OPEN_CATEGORIES = frozenset({"education"})
FAIL_CLOSED_CATEGORIES = frozenset({"entertainment", "game", "social", "inappropriate"})

# Ánh xạ tên tiến trình -> nhóm ứng dụng (để đếm ngân sách theo nhóm).
DEFAULT_APP_GROUPS = {
    "robloxplayerbeta.exe": "game", "robloxstudiobeta.exe": "game", "minecraftlauncher.exe": "game",
    "steam.exe": "game", "epicgameslauncher.exe": "game", "leagueclient.exe": "game",
    "riotclientservices.exe": "game", "garena.exe": "game", "battle.net.exe": "game",
    "discord.exe": "social", "telegram.exe": "social", "zalo.exe": "social", "messenger.exe": "social",
    "spotify.exe": "entertainment", "vlc.exe": "entertainment", "netflix.exe": "entertainment",
    "scratch 3.exe": "education", "geogebra.exe": "education", "anki.exe": "education",
}

_SLD2 = {
    "com.vn", "net.vn", "org.vn", "edu.vn", "gov.vn", "ac.vn", "info.vn", "biz.vn", "co.uk", "org.uk",
    "ac.uk", "gov.uk", "com.au", "net.au", "co.jp", "co.kr", "com.sg", "com.cn", "com.tw", "com.hk",
}


def normalize_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def second_level_domain(host: str) -> str:
    """Tên miền cấp hai (NT2: chỉ lưu mức này, không lưu subdomain/đường dẫn)."""
    parts = normalize_host(host).split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    if ".".join(parts[-2:]) in _SLD2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def match_suffix(host: str, mapping) -> str | None:
    """Khớp tên miền đa cấp: trả khóa dài nhất trong `mapping` mà host bằng hoặc là subdomain của nó."""
    labels = normalize_host(host).split(".")
    for i in range(len(labels)):
        cand = ".".join(labels[i:])
        if cand in mapping:
            return cand
    return None


def is_exempt_host(host: str, extra: frozenset | set = frozenset()) -> bool:
    h = normalize_host(host)
    if h.endswith(EXEMPT_SUFFIXES) or h in ("edu.vn", "gov.vn"):
        return True
    return match_suffix(h, EXEMPT_DOMAINS) is not None or match_suffix(h, extra) is not None


def is_valid_subject(s: str) -> bool:
    """NT2: subject chỉ là tên ứng dụng hoặc tên miền, tuyệt đối không phải URL đầy đủ."""
    if not s or len(s) > 120:
        return False
    return not any(ch in s for ch in ("/", "\\", "?", "#", "@", "=", "&")) and "://" not in s
