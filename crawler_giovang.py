#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   CRAWLER — giovang.vin  v1.0                               ║
║   Crawl "Đang diễn ra" + "Sắp diễn ra" → Hot Match JSON    ║
╚══════════════════════════════════════════════════════════════╝

Cài đặt:
    pip install cloudscraper beautifulsoup4 lxml requests pillow

Chạy:
    python crawler_giovang.py
    python crawler_giovang.py --no-stream
    python crawler_giovang.py --output giovang_iptv.json
"""

import argparse, base64, hashlib, io, json, os, re, sys, time, unicodedata
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

try:
    import cloudscraper
    from bs4 import BeautifulSoup, NavigableString
    import requests
except ImportError:
    print("Cài đặt: pip install cloudscraper beautifulsoup4 lxml requests")
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────
BASE_URL    = "https://giovang.vin"
OUTPUT_FILE = "giovang_iptv.json"
CHROME_UA   = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
VN_TZ = timezone(timedelta(hours=7))

# ── Logging ───────────────────────────────────────────────────
def log(msg: str):
    print(msg, flush=True)

# ── HTTP ──────────────────────────────────────────────────────
def make_scraper():
    sc = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    sc.headers.update({
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
        "Referer": BASE_URL + "/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    })
    return sc

def fetch_html(url: str, scraper, retries: int = 3) -> str | None:
    for i in range(retries):
        try:
            r = scraper.get(url, timeout=30, allow_redirects=True)
            r.raise_for_status()
            log(f"  ✓ [{r.status_code}] {url[:72]}")
            return r.text
        except Exception as e:
            wait = 2 ** i
            log(f"  ⚠ Lần {i+1}/{retries}: {e} → chờ {wait}s")
            if i < retries - 1:
                time.sleep(wait)
    return None

# ── Parse HTML ────────────────────────────────────────────────
def parse_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")

# ── Helpers ───────────────────────────────────────────────────
def make_id(*parts) -> str:
    raw  = "-".join(str(p) for p in parts if p)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    return (
        slug[:48] + "-" + hashlib.md5(raw.encode()).hexdigest()[:8]
        if len(slug) > 56 else slug
    )

def normalize_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", ascii_str.lower())

# ── Date/Time ─────────────────────────────────────────────────
def parse_match_datetime(raw: str):
    """
    Parse giờ từ giovang.vin. Giờ trên site là UTC → chuyển sang VN (UTC+7).
    Trả về (time_str, date_str, sort_key).
    """
    if not raw:
        return ("", "", "")

    # Format HH:MM | DD/MM hoặc HH:MM|DD.MM
    m = re.search(r"(\d{1,2}):(\d{2})\s*[|]\s*(\d{1,2})[./](\d{1,2})", raw)
    if m:
        hh, mm, day, mon = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if 0 <= hh <= 23 and 0 <= mm <= 59 and 1 <= day <= 31 and 1 <= mon <= 12:
            try:
                dt_utc = datetime(datetime.now(VN_TZ).year, mon, day, hh, mm, tzinfo=timezone.utc)
                dt_vn  = dt_utc.astimezone(VN_TZ)
                hh, mm, day, mon = dt_vn.hour, dt_vn.minute, dt_vn.day, dt_vn.month
            except Exception:
                pass
            return (
                f"{hh:02d}:{mm:02d}",
                f"{day:02d}/{mon:02d}",
                f"{mon:02d}-{day:02d} {hh:02d}:{mm:02d}",
            )

    # Chỉ HH:MM
    m2 = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", raw)
    if m2:
        hh, mm = int(m2.group(1)), int(m2.group(2))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            hh_vn      = (hh + 7) % 24
            day_offset = (hh + 7) // 24
            dt_vn      = datetime.now(timezone.utc) + timedelta(hours=7, days=day_offset)
            return (
                f"{hh_vn:02d}:{mm:02d}",
                dt_vn.strftime("%d/%m"),
                f"{dt_vn.strftime('%m-%d')} {hh_vn:02d}:{mm:02d}",
            )
    return ("", "", "")

# ── Card/Match Extraction ─────────────────────────────────────
def _find_section(bs: BeautifulSoup, patterns: list[str]) -> "BeautifulSoup | None":
    """Tìm section theo text heading (hỗ trợ nhiều pattern)."""
    for pattern in patterns:
        for node in bs.find_all(string=re.compile(pattern, re.I)):
            parent = node.find_parent()
            if parent:
                # Đi lên cây DOM để tìm container chứa các card
                for _ in range(10):
                    section = parent
                    cards   = section.find_all("a", href=re.compile(r"/truc-tiep/|/match/|/live/"))
                    if cards:
                        return section
                    parent = parent.find_parent()
                    if not parent:
                        break
    return None

def _extract_teams_from_card(card: BeautifulSoup) -> tuple[str, str]:
    """Tách tên home/away team từ card."""
    # Thử lấy từ các thẻ chứa tên đội
    # Pattern 1: class chứa team/club/home/away
    for cls_kw in ["team", "club", "home", "away", "name"]:
        elems = card.find_all(class_=re.compile(cls_kw, re.I))
        names = [e.get_text(strip=True) for e in elems if e.get_text(strip=True)]
        names = [n for n in names if 2 <= len(n) <= 40 and not re.fullmatch(r"[\d\s:.-]+", n)]
        if len(names) >= 2:
            return names[0], names[1]

    # Pattern 2: Tìm div/span có VS ở giữa
    raw = card.get_text(" ", strip=True)
    m   = re.search(
        r"([\w\u00C0-\u024F\u1E00-\u1EFF][\w\u00C0-\u024F\u1E00-\u1EFF .'-]{1,34}?)"
        r"\s+(?:VS|vs|–|-)\s+"
        r"([\w\u00C0-\u024F\u1E00-\u1EFF][\w\u00C0-\u024F\u1E00-\u1EFF .'-]{1,34})",
        raw, re.UNICODE,
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()

    return "", ""

def _extract_score(card: BeautifulSoup) -> str:
    """Lấy tỉ số từ card nếu đang live."""
    raw = card.get_text(" ", strip=True)
    m   = re.search(r"\b(\d{1,2})\s*[-–:]\s*(\d{1,2})\b", raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return ""

def _extract_logo_urls(card: BeautifulSoup) -> tuple[str, str]:
    """Lấy logo URLs từ img tags trong card."""
    imgs = card.find_all("img")
    logos = []
    for img in imgs:
        src = img.get("src", "") or img.get("data-src", "")
        if not src or not src.startswith("http"):
            continue
        w   = str(img.get("width",  "0")).replace("px", "")
        h   = str(img.get("height", "0")).replace("px", "")
        alt = img.get("alt", "").lower()
        cls = " ".join(img.get("class", []))
        # Bỏ ảnh nền / icon nhỏ / favicon
        try:
            if int(w) < 20 or int(h) < 20:
                continue
        except Exception:
            pass
        # Ảnh logo đội thường có từ khoá team/logo/badge
        if any(k in alt for k in ("logo", "team", "badge", "crest", "club")):
            logos.append(src)
        elif any(k in cls for k in ("logo", "team", "badge")):
            logos.append(src)
        elif any(k in src.lower() for k in ("logo", "team", "badge", "crest", "clubs", "teams")):
            logos.append(src)
    return (logos[0] if len(logos) >= 1 else "",
            logos[1] if len(logos) >= 2 else "")

def _extract_league(card: BeautifulSoup) -> str:
    """Lấy tên giải đấu từ card."""
    raw = card.get_text(" ", strip=True)
    # Loại bỏ team names và score từ raw text
    for cls_kw in ["league", "tournament", "competition", "cup", "giai"]:
        elems = card.find_all(class_=re.compile(cls_kw, re.I))
        for e in elems:
            t = e.get_text(strip=True)
            if 2 < len(t) < 60:
                return t
    return ""

def _extract_time_raw(card: BeautifulSoup) -> str:
    """Lấy giờ thi đấu raw từ card."""
    raw = card.get_text(" ", strip=True)
    # Pattern HH:MM | DD/MM hoặc HH:MM | DD.MM
    m = re.search(r"(\d{1,2}:\d{2})\s*[|]?\s*(\d{1,2}[./]\d{1,2})", raw)
    if m:
        return f"{m.group(1)} | {m.group(2)}"
    m2 = re.search(r"(\d{1,2}:\d{2})", raw)
    if m2:
        return m2.group(1)
    return ""

def _determine_status(card: BeautifulSoup, section_label: str) -> str:
    """Xác định trạng thái trận dựa vào section và card."""
    raw = card.get_text(" ", strip=True).lower()
    if section_label in ("live", "đang diễn ra", "dang dien ra"):
        return "live"
    if section_label in ("upcoming", "sắp diễn ra", "sap dien ra"):
        return "upcoming"
    if any(k in raw for k in ("live", "đang diễn ra", "hiệp", "phút")):
        return "live"
    if any(k in raw for k in ("kết thúc", "finished", "ft", "full time")):
        return "finished"
    return "upcoming"

def parse_match_card(card: BeautifulSoup, section_label: str) -> dict | None:
    """Parse 1 card trận → dict info."""
    href = card.get("href", "")
    if not href:
        return None
    detail_url = href if href.startswith("http") else urljoin(BASE_URL, href)

    home_team, away_team = _extract_teams_from_card(card)
    if not home_team:
        return None

    logo_home, logo_away = _extract_logo_urls(card)
    score     = _extract_score(card) if "live" in section_label.lower() or "đang" in section_label.lower() else ""
    league    = _extract_league(card)
    time_raw  = _extract_time_raw(card)
    time_str, date_str, sort_key = parse_match_datetime(time_raw)
    status    = _determine_status(card, section_label.lower())

    base_title = f"{home_team} vs {away_team}" if away_team else home_team

    return {
        "base_title": base_title,
        "home_team":  home_team,
        "away_team":  away_team,
        "score":      score,
        "status":     status,
        "league":     league,
        "match_time": time_raw,
        "time_str":   time_str,
        "date_str":   date_str,
        "sort_key":   sort_key,
        "detail_url": detail_url,
        "thumbnail":  "",
        "_logo_a":    logo_home,
        "_logo_b":    logo_away,
    }

# ── Section crawling ──────────────────────────────────────────
# Các section cần crawl trên giovang.vin
SECTION_CONFIGS = [
    {
        "label":    "Đang diễn ra",
        "patterns": [r"đang diễn ra", r"dang dien ra", r"live", r"đang phát sóng", r"trực tiếp"],
        "status":   "live",
    },
    {
        "label":    "Sắp diễn ra",
        "patterns": [r"sắp diễn ra", r"sap dien ra", r"upcoming", r"sắp tới", r"sắp bắt đầu"],
        "status":   "upcoming",
    },
]

def extract_all_match_links(bs: BeautifulSoup) -> list[str]:
    """Lấy tất cả link trực tiếp từ trang."""
    links = set()
    for a in bs.find_all("a", href=True):
        href = a.get("href", "")
        if re.search(r"/(truc-tiep|live|match|tran-dau)/", href, re.I):
            full = href if href.startswith("http") else urljoin(BASE_URL, href)
            links.add(full)
    return list(links)

def crawl_homepage(html: str) -> list[dict]:
    """
    Crawl trang chủ giovang.vin, lấy các trận trong:
      - 'Đang diễn ra' (live)
      - 'Sắp diễn ra' (upcoming)
    Gộp thành mục 'Hot Match'.
    """
    bs = parse_html(html)
    collected: list[dict] = []
    seen_urls: set[str]   = set()

    for cfg in SECTION_CONFIGS:
        log(f"\n  🔍 Tìm section: {cfg['label']}")

        # Tìm section container
        section_node = _find_section(bs, cfg["patterns"])

        if section_node:
            # Lấy tất cả link trực tiếp trong section
            card_links = section_node.find_all(
                "a",
                href=re.compile(r"/(truc-tiep|live|match|tran-dau)/", re.I)
            )
            log(f"  → Tìm thấy {len(card_links)} card trong section")

            for card in card_links:
                m = parse_match_card(card, cfg["label"])
                if not m:
                    continue
                if m["detail_url"] in seen_urls:
                    continue
                seen_urls.add(m["detail_url"])
                # Ghi đè status theo section
                m["status"] = cfg["status"]
                collected.append(m)
        else:
            log(f"  ⚠ Không tìm thấy section '{cfg['label']}' theo heading, thử toàn trang...")

    # Fallback: lấy toàn bộ link trận nếu không tìm được section
    if not collected:
        log("  ⚠ Fallback: parse toàn bộ card trên trang...")
        for a in bs.find_all("a", href=re.compile(r"/(truc-tiep|live|match|tran-dau)/", re.I)):
            m = parse_match_card(a, "live")
            if not m or m["detail_url"] in seen_urls:
                continue
            seen_urls.add(m["detail_url"])
            collected.append(m)

    log(f"\n  ✅ Tổng: {len(collected)} trận (Đang diễn ra + Sắp diễn ra)")
    return collected

# ── Stream extraction ─────────────────────────────────────────
_QUALITY_RE  = re.compile(r"[_-](?:full[_-]?hd|fhd|1080p?|720p?|480p?|360p?|hd|sd)$", re.I)
_QUALITY_MAP = {
    "hd": "HD", "sd": "SD", "full-hd": "Full HD", "full_hd": "Full HD",
    "fhd": "Full HD", "1080": "Full HD", "1080p": "Full HD",
    "720": "HD", "720p": "HD", "480": "SD", "480p": "SD",
    "360": "360p", "360p": "360p",
}
_QUALITY_ORDER  = {"Full HD": 0, "HD": 1, "SD": 2, "360p": 3, "Auto": 4}
_QUALITY_LABELS = [("Full HD", "full-hd"), ("HD", "hd"), ("SD", "sd")]

def _quality_label(url: str) -> str:
    fname = re.sub(r"\.\w+$", "", url.rstrip("/").split("/")[-1]).lower()
    m = _QUALITY_RE.search(fname)
    return _QUALITY_MAP.get(m.group(0).lstrip("-_").lower(), m.group(0).upper()) if m else "Auto"

def _stream_base(url: str) -> str:
    fname = re.sub(r"\.\w+$", "", url.rstrip("/").split("/")[-1])
    return _QUALITY_RE.sub("", fname).lower()

def _derive_variants(url: str, referer: str) -> list[dict]:
    """Sinh Full HD / HD / SD từ 1 URL m3u8."""
    slash_idx = url.rfind("/")
    if slash_idx < 0:
        return [{"name": "Auto", "url": url, "type": "hls", "referer": referer}]
    prefix   = url[:slash_idx + 1]
    fname    = url[slash_idx + 1:]
    ext_idx  = fname.rfind(".")
    ext      = fname[ext_idx:] if ext_idx >= 0 else ".m3u8"
    basename = fname[:ext_idx] if ext_idx >= 0 else fname
    base     = _QUALITY_RE.sub("", basename)
    return [
        {"name": label, "url": f"{prefix}{base}_{suffix}{ext}", "type": "hls", "referer": referer}
        for label, suffix in _QUALITY_LABELS
    ]

def _collect_m3u8(obj, found: list, depth: int = 0):
    if depth > 12:
        return
    if isinstance(obj, str):
        if obj.startswith("http") and ".m3u8" in obj:
            found.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_m3u8(v, found, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _collect_m3u8(item, found, depth + 1)

def extract_streams(detail_url: str, html: str, bs: BeautifulSoup) -> list[dict]:
    """Lấy danh sách stream m3u8 từ trang chi tiết trận."""
    seen: set[str]    = set()
    all_m3u8: list[str] = []

    def _add(u: str):
        u = u.strip().split("?")[0].split("#")[0]
        if u and u.startswith("http") and ".m3u8" in u and u not in seen:
            seen.add(u)
            all_m3u8.append(u)

    # 1. __NEXT_DATA__ JSON (Next.js sites)
    next_tag = bs.find("script", id="__NEXT_DATA__")
    if next_tag and next_tag.string:
        try:
            nd    = json.loads(next_tag.string)
            found: list[str] = []
            _collect_m3u8(nd, found)
            for u in found:
                _add(u)
        except Exception:
            pass

    # 2. Nuxt / Vue data
    for script in bs.find_all("script", type=re.compile(r"application/json", re.I)):
        if script.string:
            found: list[str] = []
            _collect_m3u8_str(script.string, found)
            for u in found:
                _add(u)

    # 3. Regex trong HTML
    for mo in re.finditer(r"(https?://[^\s'\"<>\]\\]+\.m3u8)", html):
        _add(mo.group(1))

    # 4. Script tags
    for script in bs.find_all("script"):
        c = script.string or ""
        for pat in [
            r'"(?:file|src|stream|url|hls|videoUrl|streamUrl|hlsUrl)"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
            r"'(?:file|src|stream|url|hls)'\s*:\s*'(https?://[^']+\.m3u8[^']*)'",
            r'(?:streamUrl|videoUrl|hlsUrl|m3u8Url|source)\s*[=:]\s*["\']([^"\']+\.m3u8[^"\']*)["\']',
        ]:
            for mo in re.finditer(pat, c, re.I):
                _add(mo.group(1))

    # Không có m3u8 → iframe fallback
    if not all_m3u8:
        for iframe in bs.find_all("iframe", src=True):
            src = iframe["src"]
            if re.search(r"live|stream|embed|player|sport|watch", src, re.I):
                return [{"name": "Live", "url": src, "type": "iframe", "referer": detail_url}]
        return [{"name": "Trang trực tiếp", "url": detail_url, "type": "iframe", "referer": detail_url}]

    # Lấy URL đầu tiên làm template
    first_url   = all_m3u8[0]
    first_base  = _stream_base(first_url)
    same_base   = [u for u in all_m3u8 if _stream_base(u) == first_base]

    if len(same_base) >= 2:
        streams = [{"name": _quality_label(u), "url": u, "type": "hls", "referer": detail_url}
                   for u in same_base]
        streams.sort(key=lambda x: _QUALITY_ORDER.get(x["name"], 99))
        return streams

    return _derive_variants(first_url, detail_url)

def _collect_m3u8_str(text: str, found: list):
    for mo in re.finditer(r"(https?://[^\s'\"<>\]\\]+\.m3u8)", text):
        found.append(mo.group(1))

def extract_thumb(html: str, bs: BeautifulSoup) -> str:
    """Lấy thumbnail URL từ trang chi tiết."""
    # og:image
    og = bs.find("meta", property="og:image")
    if og:
        url = og.get("content", "")
        if url and url.startswith("http"):
            return url

    # JSON-LD
    for script in bs.find_all("script", type="application/ld+json"):
        if script.string:
            try:
                data = json.loads(script.string)
                img  = data.get("image", "")
                if isinstance(img, str) and img.startswith("http"):
                    return img
                if isinstance(img, dict):
                    url = img.get("url", "")
                    if url:
                        return url
            except Exception:
                pass

    # Regex webp/jpg CDN
    m = re.search(r'(https?://[^\s\'"<>]+(?:thumb|banner|cover|poster)[^\s\'"<>]+\.(?:webp|jpg|png))', html, re.I)
    if m:
        return m.group(1)

    return ""

def crawl_detail(detail_url: str, scraper) -> tuple[list[dict], str]:
    """Crawl trang chi tiết → (streams, thumb_url)."""
    html = fetch_html(detail_url, scraper, retries=2)
    if not html:
        return [], ""
    bs     = parse_html(html)
    thumb  = extract_thumb(html, bs)
    streams = extract_streams(detail_url, html, bs)
    return streams, thumb

# ── Build IPTV JSON ───────────────────────────────────────────
def build_display_title(m: dict) -> str:
    base  = m["base_title"]
    score = m.get("score", "")
    t     = m.get("time_str", "")
    d     = m.get("date_str", "")

    if m["status"] == "live":
        if score and score != "VS":
            home, away = m.get("home_team", ""), m.get("away_team", "")
            if home and away:
                return f"{home} {score} {away}  🔴"
        return f"{base}  🔴 LIVE"
    elif m["status"] == "finished":
        if score and score != "VS":
            home, away = m.get("home_team", ""), m.get("away_team", "")
            if home and away:
                return f"{home} {score} {away}  ✅"
        return f"{base}  ✅ KT"
    else:
        time_info = ""
        if t and d:
            time_info = f"  🕐 {t} | {d}"
        elif t:
            time_info = f"  🕐 {t}"
        elif d:
            time_info = f"  📅 {d}"
        return f"{base}{time_info}"

def build_channel(m: dict, streams: list[dict], thumb: str, index: int) -> dict:
    ch_id        = make_id("gv", str(index), re.sub(r"[^a-z0-9]", "-", m["base_title"].lower())[:24])
    display_name = build_display_title(m)
    league       = m.get("league", "")
    score        = m.get("score", "")

    # Labels
    labels = []
    status_map = {
        "live":     {"text": "● LIVE",          "color": "#E73131", "text_color": "#ffffff"},
        "upcoming": {"text": "🕐 Sắp diễn ra",  "color": "#1A6DD5", "text_color": "#ffffff"},
        "finished": {"text": "✅ Kết thúc",      "color": "#444444", "text_color": "#ffffff"},
    }
    st_cfg = status_map.get(m["status"], status_map["live"])
    labels.append({**st_cfg, "position": "top-left"})

    if score and score not in ("", "VS") and m["status"] == "live":
        labels.append({"text": f"⚽ {score}", "position": "bottom-right",
                       "color": "#E73131", "text_color": "#ffffff"})

    # Stream links
    stream_links = []
    for idx, s in enumerate(streams):
        referer = s.get("referer", m["detail_url"])
        stream_links.append({
            "id":      make_id(ch_id, f"l{idx}"),
            "name":    s.get("name", f"Link {idx+1}"),
            "type":    s.get("type", "hls"),
            "default": idx == 0,
            "url":     s["url"],
            "request_headers": [
                {"key": "Referer",    "value": referer},
                {"key": "User-Agent", "value": CHROME_UA},
            ],
        })

    if not stream_links:
        stream_links.append({
            "id": "lnk0", "name": "Link 1", "type": "iframe", "default": True,
            "url": m["detail_url"],
            "request_headers": [
                {"key": "Referer",    "value": m["detail_url"]},
                {"key": "User-Agent", "value": CHROME_UA},
            ],
        })

    stream_obj = [{
        "id":           make_id(ch_id, "st0"),
        "name":         "Trực tiếp",
        "stream_links": stream_links,
    }]

    # Thumbnail
    img_url = thumb or m.get("_logo_a", "") or f"{BASE_URL}/favicon.ico"
    img_obj = {
        "padding":          1,
        "background_color": "#0a1a2e",
        "display":          "contain",
        "url":              img_url,
        "width":            1600,
        "height":           1200,
    }

    content_name = display_name
    if league:
        content_name += f" · {league.strip()}"

    parts = []
    if league:
        parts.append(league.strip()[:40])
    if m.get("time_str"):
        parts.append(m["time_str"])
    if m.get("date_str"):
        parts.append(m["date_str"])
    if m["status"] == "live":
        parts.append(f"🔴 LIVE{' ' + score if score and score != 'VS' else ''}")
    elif m["status"] == "upcoming":
        parts.append("🕐 Sắp diễn ra")
    description = " | ".join(p for p in parts if p)

    return {
        "id":            ch_id,
        "name":          display_name,
        "description":   description,
        "type":          "single",
        "display":       "thumbnail-only",
        "enable_detail": False,
        "image":         img_obj,
        "labels":        labels,
        "sources": [{
            "id":   make_id(ch_id, "src"),
            "name": "GioVang Live",
            "contents": [{
                "id":      make_id(ch_id, "ct"),
                "name":    content_name,
                "streams": stream_obj,
            }],
        }],
    }

def build_iptv_json(channels: list[dict], now_str: str) -> dict:
    """Tạo JSON IPTV hoàn chỉnh với 1 group 'Hot Match'."""
    return {
        "id":          "giovang-live",
        "name":        "GioVang TV - Trực tiếp bóng đá",
        "url":         BASE_URL + "/",
        "description": "GioVang.vin - Xem trực tiếp bóng đá, các trận đang diễn ra và sắp diễn ra với chất lượng HD.",
        "disable_ads": True,
        "color":       "#f5a623",
        "grid_number": 3,
        "image":       {"type": "cover", "url": f"{BASE_URL}/favicon.ico"},
        "groups": [{
            "id":            "hot-match",
            "name":          "🔥 Hot Match",
            "image":         None,
            "display":       "vertical",
            "grid_number":   2,
            "enable_detail": False,
            "channels":      channels,
        }],
    }

# ── Main ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Crawler giovang.vin → IPTV JSON")
    ap.add_argument("--no-stream", action="store_true", help="Không crawl stream (nhanh hơn)")
    ap.add_argument("--output",    default=OUTPUT_FILE,   help="Tên file output JSON")
    args = ap.parse_args()

    log("\n" + "═" * 62)
    log("  🏟  CRAWLER — giovang.vin  v1.0")
    log("  🔴 Đang diễn ra  +  🕐 Sắp diễn ra  →  🔥 Hot Match")
    log("═" * 62 + "\n")

    now_vn  = datetime.now(VN_TZ)
    now_str = now_vn.strftime("%d/%m/%Y %H:%M") + " ICT (UTC+7)"

    scraper = make_scraper()

    # Bước 1: Tải trang chủ
    log("📥 Bước 1: Tải trang chủ giovang.vin...")
    html = fetch_html(BASE_URL, scraper)
    if not html:
        log("❌ Không tải được trang chủ. Thoát.")
        sys.exit(1)
    if "Just a moment" in html or "cf-browser-verification" in html:
        log("⚠ Cloudflare challenge — thử lại sau.")
        sys.exit(1)

    # Bước 2: Parse tất cả trận
    log("\n🔍 Bước 2: Phân tích trang chủ, tìm Đang diễn ra + Sắp diễn ra...")
    matches = crawl_homepage(html)

    if not matches:
        log("❌ Không tìm thấy trận nào. Thoát.")
        sys.exit(1)

    # Sắp xếp: live trước, upcoming sau, theo giờ
    priority = {"live": 0, "upcoming": 1, "finished": 2}
    matches.sort(key=lambda x: (priority.get(x["status"], 9), x.get("sort_key", "")))

    log(f"\n  ✅ Tìm thấy {len(matches)} trận")

    # Bước 3: Crawl stream từng trận
    log(f"\n🎬 Bước 3: Crawl stream{'(bỏ qua)' if args.no_stream else ''}...")
    channels = []
    for i, m in enumerate(matches, 1):
        log(f"\n  [{i:03d}/{len(matches):03d}] {m['base_title']}")
        log(f"        Status: {m['status']} | {m['detail_url'][-50:]}")

        streams, thumb = [], ""
        if not args.no_stream:
            streams, thumb = crawl_detail(m["detail_url"], scraper)
            log(f"        Streams: {len(streams)} | Thumb: {'✓' if thumb else '✗'}")
            time.sleep(0.5)  # throttle

        ch = build_channel(m, streams, thumb, i)
        channels.append(ch)

    # Bước 4: Ghi file JSON
    result = build_iptv_json(channels, now_str)
    output_path = args.output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log(f"\n{'═' * 62}")
    log(f"  ✅ Hoàn tất!  📁 {output_path}")
    log(f"  📊 {len(channels)} trận  |  🕐 {now_str}")
    log("═" * 62 + "\n")


if __name__ == "__main__":
    main()
