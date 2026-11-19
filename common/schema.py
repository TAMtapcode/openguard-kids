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

# Lược đồ sự kiện: chỉ các loại này được phép (NT2 & F3).
EVENT_TYPES = (
    "app_start", "app_stop", "domain_query", "blocked_app", "blocked_domain",
    "quota_warning", "locked", "unlock_request", "override_granted",
)
REQUEST_KINDS = ("add-time", "false-block")
COMMAND_ACTIONS = ("LOCK_NOW", "UNLOCK", "ADD_TIME", "WIPE_LOCAL", "POLICY_UPDATED")

# NT6 - danh sách miễn chặn vĩnh viễn (bất biến, không policy nào ghi đè được).
EXEMPT_SUFFIXES = (".edu.vn", ".gov.vn", ".ac.vn")
EXEMPT_DOMAINS = frozenset({
    "tongdai111.vn", "111.vn", "vnedu.vn", "moet.gov.vn", "moh.gov.vn",
    "redcross.org.vn", "hanhtrangso.nxbgd.vn", "olm.vn", "vio.edu.vn",
})

# Smart Fail-Safe: nhóm nào fail-open / fail-closed khi mất kết nối quá hạn.
FAIL_OPEN_CATEGORIES = frozenset({"education"})
FAIL_CLOSED_CATEGORIES = frozenset({"entertainment", "game", "social", "inappropriate"})

# Ánh xạ tên tiến trình -> nhóm ứng dụng (để đếm ngân sách theo nhóm & phân loại tự động).
DEFAULT_APP_GROUPS = {
    # Trò chơi & Launcher
    "robloxplayerbeta.exe": "game", "robloxstudiobeta.exe": "game", "robloxplayerlauncher.exe": "game",
    "minecraftlauncher.exe": "game", "javaw.exe": "game", "tlauncher.exe": "game",
    "steam.exe": "game", "steamwebhelper.exe": "game",
    "epicgameslauncher.exe": "game", "epicwebhelper.exe": "game",
    "leagueclient.exe": "game", "leagueclientux.exe": "game", "league of legends.exe": "game",
    "riotclientservices.exe": "game", "riotclientux.exe": "game",
    "garena.exe": "game", "garenamessenger.exe": "game",
    "battle.net.exe": "game", "valorant.exe": "game", "valorant-win64-shipping.exe": "game",
    "genshinimpact.exe": "game", "starrail.exe": "game", "zenlesszonezero.exe": "game",
    "pubg.exe": "game", "tslgame.exe": "game", "cs2.exe": "game", "dota2.exe": "game",
    "fc24.exe": "game", "fifa.exe": "game", "bluestacks.exe": "game", "hd-player.exe": "game",
    "ldplayer.exe": "game", "nox.exe": "game",
    # Mạng xã hội & Nhắn tin
    "discord.exe": "social", "discordptb.exe": "social", "discordcanary.exe": "social",
    "telegram.exe": "social", "unigram.exe": "social",
    "zalo.exe": "social", "zalopc.exe": "social",
    "messenger.exe": "social", "whatsapp.exe": "social", "viber.exe": "social",
    "line.exe": "social", "skype.exe": "social", "tiktok.exe": "social",
    # Giải trí
    "spotify.exe": "entertainment", "vlc.exe": "entertainment", "netflix.exe": "entertainment",
    "potplayermini64.exe": "entertainment", "potplayermini.exe": "entertainment",
    "mpc-hc64.exe": "entertainment", "itunes.exe": "entertainment", "kodi.exe": "entertainment",
    # Giáo dục & Học tập
    "scratch 3.exe": "education", "scratch.exe": "education",
    "geogebra.exe": "education", "geogebracalculator.exe": "education",
    "anki.exe": "education", "winword.exe": "education", "excel.exe": "education",
    "powerpnt.exe": "education", "onenote.exe": "education",
    "code.exe": "education", "pycharm64.exe": "education", "idle.exe": "education",
    "zoom.exe": "education", "ms-teams.exe": "education", "teams.exe": "education",
}

# Tên hiển thị thân thiện cho tiến trình (giúp bé và phụ huynh dễ đọc trên báo cáo & hộp thoại chặn)
APP_FRIENDLY_NAMES = {
    "chrome.exe": "Google Chrome",
    "msedge.exe": "Microsoft Edge",
    "firefox.exe": "Mozilla Firefox",
    "brave.exe": "Brave Browser",
    "coccoc.exe": "Trình duyệt Cốc Cốc",
    "opera.exe": "Opera Browser",
    "robloxplayerbeta.exe": "Roblox",
    "robloxstudiobeta.exe": "Roblox Studio",
    "minecraftlauncher.exe": "Minecraft Launcher",
    "javaw.exe": "Minecraft (Java)",
    "steam.exe": "Steam",
    "epicgameslauncher.exe": "Epic Games",
    "leagueclient.exe": "Liên Minh Huyền Thoại",
    "riotclientservices.exe": "Riot Client",
    "garena.exe": "Garena",
    "valorant.exe": "VALORANT",
    "discord.exe": "Discord",
    "telegram.exe": "Telegram",
    "zalo.exe": "Zalo",
    "spotify.exe": "Spotify",
    "vlc.exe": "VLC Media Player",
    "scratch 3.exe": "Scratch 3",
    "geogebra.exe": "GeoGebra",
    "anki.exe": "Anki Flashcards",
    "winword.exe": "Microsoft Word",
    "excel.exe": "Microsoft Excel",
    "powerpnt.exe": "Microsoft PowerPoint",
    "code.exe": "Visual Studio Code",
    "zoom.exe": "Zoom",
    "ms-teams.exe": "Microsoft Teams",
    "notepad.exe": "Notepad",
    "calc.exe": "Máy tính (Calculator)",
}

BROWSER_PROCESSES = frozenset({
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe",
    "coccoc.exe", "opera.exe", "vivaldi.exe", "waterfox.exe",
})

_SLD2 = {
    "com.vn", "net.vn", "org.vn", "edu.vn", "gov.vn", "ac.vn", "info.vn", "biz.vn", "co.uk", "org.uk",
    "ac.uk", "gov.uk", "com.au", "net.au", "co.jp", "co.kr", "com.sg", "com.cn", "com.tw", "com.hk",
}


def friendly_app_name(proc_name: str) -> str:
    n = (proc_name or "").strip().lower()
    if not n:
        return "Ứng dụng không xác định"
    if n in APP_FRIENDLY_NAMES:
        return APP_FRIENDLY_NAMES[n]
    stem = n[:-4] if n.endswith(".exe") else n
    return stem.replace("_", " ").replace("-", " ").title()


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
    if h.endswith(EXEMPT_SUFFIXES) or h in ("edu.vn", "gov.vn", "ac.vn"):
        return True
    return match_suffix(h, EXEMPT_DOMAINS) is not None or match_suffix(h, extra) is not None


def is_valid_subject(s: str) -> bool:
    """NT2: subject chỉ là tên ứng dụng hoặc tên miền, tuyệt đối không phải URL đầy đủ."""
    if not s or len(s) > 120:
        return False
    return not any(ch in s for ch in ("/", "\\", "?", "#", "@", "=", "&")) and "://" not in s
