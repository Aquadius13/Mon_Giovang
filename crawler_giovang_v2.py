#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   CRAWLER — giovang.vin  v2.0                               ║
║   Dùng API JSON: live-api.keovip88.net                      ║
║   • live.json  → Đang diễn ra (status_code != NS/FT)        ║
║   • all.json   → Sắp diễn ra  (status_code == NS)           ║
║   → Gộp thành nhóm "🔥 Hot Match"                           ║
╚══════════════════════════════════════════════════════════════╝

Cài đặt:
    pip install cloudscraper beautifulsoup4 lxml requests

Chạy:
    python crawler_giovang.py
    python crawler_giovang.py --no-stream
    python crawler_giovang.py --output giovang_iptv.json
    python crawler_giovang.py --all        # lấy toàn bộ, không chỉ hot
"""

import argparse, hashlib, json, re, sys, time, unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin

try:
    import cloudscraper
    from bs4 import BeautifulSoup
    import requests
except ImportError:
    print("Cài: pip install cloudscraper beautifulsoup4 lxml requests")
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────
BASE_URL     = "https://giovang.vin"
API_LIVE     = "https://live-api.keovip88.net/storage/livestream/live.json"
API_ALL      = "https://live-api.keovip88.net/storage/livestream/all.json"
OUTPUT_FILE  = "giovang_iptv.json"
CHROME_UA    = (
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

def fetch_json(url: str, scraper, retries: int = 3) -> dict | None:
    """Fetch JSON từ API."""
    t = int(time.time() * 1000)
    full_url = f"{url}?t={t}"
    for i in range(retries):
        try:
            r = scraper.get(full_url, timeout=20)
            r.raise_for_status()
            data = r.json()
            log(f"  ✓ [{r.status_code}] {url} — {len(data.get('response', []))} items")
            return data
        except Exception as e:
            wait = 2 ** i
            log(f"  ⚠ Lần {i+1}/{retries}: {e} → chờ {wait}s")
            if i < retries - 1:
                time.sleep(wait)
    return None

def fetch_html(url: str, scraper, retries: int = 3) -> str | None:
    for i in range(retries):
        try:
            r = scraper.get(url, timeout=25, allow_redirects=True)
            r.raise_for_status()
            return r.text
        except Exception as e:
            wait = 2 ** i
            log(f"  ⚠ Lần {i+1}/{retries}: {e} → chờ {wait}s")
            if i < retries - 1:
                time.sleep(wait)
    return None

# ── Slug builder (dịch từ JS getSlug của giovang.vin) ─────────
_SLUG_FROM = "àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđç·/_,:;"
_SLUG_TO   = "aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyydc------"

def get_slug(s: str) -> str:
    """Python port của hàm getSlug() trong JS giovang.vin."""
    s = s.strip().lower()
    for f, t in zip(_SLUG_FROM, _SLUG_TO):
        s = s.replace(f, t)
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s]+', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s

def build_match_url(home_name: str, away_name: str, day_month: str, fixture_id: str) -> str:
    """Tạo URL trang trận đấu, giống hệt JS buildMatchSlug."""
    raw = f"truc tiep {home_name} vs {away_name} {day_month} {fixture_id}"
    slug = get_slug(raw)
    return f"{BASE_URL}/{slug}/"

# ── Parse giờ từ API ──────────────────────────────────────────
def parse_time(fixture: dict) -> tuple[str, str, str]:
    """
    Trả về (time_str, date_str, sort_key).
    API trả về: fixture['time'] = 'HH:MM' (UTC), fixture['day_month'] = 'DD/MM'
    → Chuyển UTC+7
    """
    raw_time  = fixture.get("time", "")         # "19:00" UTC
    day_month = fixture.get("day_month", "")     # "17/04"

    m_time = re.match(r"(\d{1,2}):(\d{2})", raw_time)
    m_date = re.match(r"(\d{1,2})[/.](\d{1,2})", day_month)

    if m_time and m_date:
        hh_utc, mm  = int(m_time.group(1)), int(m_time.group(2))
        day, mon    = int(m_date.group(1)),  int(m_date.group(2))
        try:
            year = datetime.now(VN_TZ).year
            dt_utc = datetime(year, mon, day, hh_utc, mm, tzinfo=timezone.utc)
            dt_vn  = dt_utc.astimezone(VN_TZ)
            return (
                dt_vn.strftime("%H:%M"),
                dt_vn.strftime("%d/%m"),
                dt_vn.strftime("%m-%d %H:%M"),
            )
        except Exception:
            pass

    if m_time:
        hh, mm = int(m_time.group(1)), int(m_time.group(2))
        hh_vn  = (hh + 7) % 24
        return (f"{hh_vn:02d}:{mm:02d}", day_month, f"00-00 {hh_vn:02d}:{mm:02d}")

    return ("", day_month, "")

# ── Xác định status trận ──────────────────────────────────────
def get_status(fixture: dict) -> str:
    sc = fixture.get("status_code", "NS")
    if sc in ("FT", "AET", "PEN", "AWD", "WO"):
        return "finished"
    if sc == "NS":
        return "upcoming"
    # Đang live: 1H, 2H, HT, ET, BT, P, INT, LIVE, SUSP, ...
    return "live"

# ── Parse 1 fixture từ API ────────────────────────────────────
def parse_fixture(f: dict) -> dict:
    home      = f.get("teams", {}).get("home", {})
    away      = f.get("teams", {}).get("away", {})
    league    = f.get("league", {})
    blv_list  = f.get("blv", []) or []
    goals     = f.get("goals", {}) or {}

    home_name = home.get("name", "")
    away_name = away.get("name", "")
    logo_a    = home.get("logo", "")
    logo_b    = away.get("logo", "")

    # Score
    g_home = goals.get("home")
    g_away = goals.get("away")
    score  = f"{g_home}-{g_away}" if g_home is not None and g_away is not None else ""

    time_str, date_str, sort_key = parse_time(f)
    status   = get_status(f)
    base_title = f"{home_name} vs {away_name}" if home_name and away_name else home_name

    # URL trang trận
    detail_url = build_match_url(home_name, away_name, f.get("day_month", ""), f.get("id", ""))

    # Lọc BLV thật (loại "nha-dai")
    real_blv = [b for b in blv_list if b != "nha-dai"] if isinstance(blv_list, list) else []

    return {
        "id":          f.get("id", ""),
        "base_title":  base_title,
        "home_team":   home_name,
        "away_team":   away_name,
        "logo_a":      logo_a,
        "logo_b":      logo_b,
        "league":      league.get("title", ""),
        "league_logo": league.get("logo", ""),
        "score":       score,
        "status":      status,
        "status_code": f.get("status_code", "NS"),
        "time_str":    time_str,
        "date_str":    date_str,
        "sort_key":    sort_key,
        "detail_url":  detail_url,
        "blv_list":    real_blv,
        "is_hot":      bool(f.get("is_hot")),
        "is_hot_top":  bool(f.get("is_hot_top")),
        "type":        f.get("type", "football"),
        "live_time":   f.get("live_time", ""),
    }

# ── Fetch & filter matches từ API ─────────────────────────────
def fetch_matches(scraper, only_hot: bool = True) -> list[dict]:
    """
    Lấy:
    - live.json  → các trận đang diễn ra (is_live/is_hot)
    - all.json   → các trận sắp diễn ra  (status_code == NS, is_hot_top)
    Gộp lại, dedup theo id.
    """
    log("\n📡 Bước 1: Fetch API live.json...")
    live_data = fetch_json(API_LIVE, scraper)

    log("\n📡 Bước 2: Fetch API all.json...")
    all_data = fetch_json(API_ALL, scraper)

    matches:   list[dict] = []
    seen_ids:  set[str]   = set()

    # ── Trận đang diễn ra (live.json) ──────────────────────
    live_items = (live_data or {}).get("response", [])
    log(f"\n  live.json: {len(live_items)} trận")

    for f in live_items:
        fid    = str(f.get("id", ""))
        status = get_status(f)

        # Bỏ qua trận kết thúc
        if status == "finished":
            continue
        # Nếu only_hot, chỉ lấy hot
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")):
            continue

        m = parse_fixture(f)
        if fid and fid not in seen_ids:
            seen_ids.add(fid)
            matches.append(m)
            log(f"    ✓ [{m['status']}] {m['base_title']} | {m['league']} | {m['time_str']}")

    # ── Trận sắp diễn ra (all.json) ───────────────────────
    all_items = (all_data or {}).get("response", [])
    log(f"\n  all.json: {len(all_items)} trận")

    upcoming = []
    for f in all_items:
        sc  = f.get("status_code", "NS")
        fid = str(f.get("id", ""))
        if sc != "NS":
            continue
        if fid in seen_ids:
            continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")):
            continue

        m = parse_fixture(f)
        seen_ids.add(fid)
        upcoming.append(m)
        log(f"    ✓ [upcoming] {m['base_title']} | {m['league']} | {m['time_str']}")

    matches.extend(upcoming)

    # Sắp xếp: live trước → upcoming sau, theo giờ
    priority = {"live": 0, "upcoming": 1, "finished": 2}
    matches.sort(key=lambda x: (priority.get(x["status"], 9), x.get("sort_key", "")))

    log(f"\n  ✅ Tổng: {len(matches)} trận ({sum(1 for m in matches if m['status']=='live')} live, "
        f"{sum(1 for m in matches if m['status']=='upcoming')} sắp tới)")
    return matches

# ── Extract streams từ trang detail ──────────────────────────
_QUALITY_RE    = re.compile(r"[_-](?:full[_-]?hd|fhd|1080p?|720p?|480p?|360p?|hd|sd)$", re.I)
_QUALITY_MAP   = {
    "hd":"HD","sd":"SD","full-hd":"Full HD","full_hd":"Full HD","fhd":"Full HD",
    "1080":"Full HD","1080p":"Full HD","720":"HD","720p":"HD",
    "480":"SD","480p":"SD","360":"360p","360p":"360p",
}
_QUALITY_ORDER = {"Full HD":0,"HD":1,"SD":2,"360p":3,"Auto":4}
_QUALITY_SFXS  = [("Full HD","full-hd"),("HD","hd"),("SD","sd")]

def _quality_label(url: str) -> str:
    fname = re.sub(r"\.\w+$","",url.rstrip("/").split("/")[-1]).lower()
    m = _QUALITY_RE.search(fname)
    return _QUALITY_MAP.get(m.group(0).lstrip("-_").lower(), "Auto") if m else "Auto"

def _stream_base(url: str) -> str:
    fname = re.sub(r"\.\w+$","",url.rstrip("/").split("/")[-1])
    return _QUALITY_RE.sub("",fname).lower()

def _derive_variants(url: str, referer: str, blv: str = "") -> list[dict]:
    slash = url.rfind("/")
    if slash < 0:
        return [{"name":"Auto","url":url,"type":"hls","referer":referer,"blv":blv}]
    prefix   = url[:slash+1]
    fname    = url[slash+1:]
    ext_idx  = fname.rfind(".")
    ext      = fname[ext_idx:] if ext_idx >= 0 else ".m3u8"
    basename = fname[:ext_idx] if ext_idx >= 0 else fname
    base     = _QUALITY_RE.sub("",basename)
    return [
        {"name":label,"url":f"{prefix}{base}_{sfx}{ext}","type":"hls","referer":referer,"blv":blv}
        for label, sfx in _QUALITY_SFXS
    ]

def _collect_m3u8(obj, found: list, depth: int = 0):
    if depth > 12: return
    if isinstance(obj, str):
        if obj.startswith("http") and ".m3u8" in obj:
            found.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values(): _collect_m3u8(v, found, depth+1)
    elif isinstance(obj, list):
        for i in obj: _collect_m3u8(i, found, depth+1)

def extract_streams_from_html(detail_url: str, html: str) -> list[dict]:
    """Lấy danh sách stream HLS từ HTML trang detail."""
    bs   = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    all_m3u8: list[str] = []

    def _add(u: str):
        u = u.strip().split("?")[0].split("#")[0]
        if u and u.startswith("http") and ".m3u8" in u and u not in seen:
            seen.add(u); all_m3u8.append(u)

    # 1. __NEXT_DATA__
    tag = bs.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            found: list[str] = []
            _collect_m3u8(json.loads(tag.string), found)
            for u in found: _add(u)
        except Exception:
            pass

    # 2. Regex toàn HTML
    for mo in re.finditer(r"(https?://[^\s'\"<>\]\\]+\.m3u8)", html):
        _add(mo.group(1))

    # 3. Script tags
    for script in bs.find_all("script"):
        c = script.string or ""
        for pat in [
            r'"(?:file|src|stream|url|hls|videoUrl|streamUrl|hlsUrl)"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
            r"(?:streamUrl|videoUrl|hlsUrl|source)\s*[=:]\s*[\"']([^\"']+\.m3u8[^\"']*)[\"']",
        ]:
            for mo in re.finditer(pat, c, re.I):
                _add(mo.group(1))

    if not all_m3u8:
        # fallback iframe
        for iframe in bs.find_all("iframe", src=True):
            src = iframe["src"]
            if re.search(r"live|stream|embed|player|sport|watch", src, re.I):
                return [{"name":"Live","url":src,"type":"iframe","referer":detail_url,"blv":""}]
        return [{"name":"Trang trực tiếp","url":detail_url,"type":"iframe","referer":detail_url,"blv":""}]

    first_url  = all_m3u8[0]
    first_base = _stream_base(first_url)
    same       = [u for u in all_m3u8 if _stream_base(u) == first_base]

    if len(same) >= 2:
        streams = [{"name":_quality_label(u),"url":u,"type":"hls","referer":detail_url,"blv":""}
                   for u in same]
        streams.sort(key=lambda x: _QUALITY_ORDER.get(x["name"],99))
        return streams

    return _derive_variants(first_url, detail_url)

def crawl_detail(detail_url: str, scraper) -> tuple[list[dict], str]:
    """Crawl trang chi tiết → (streams, thumbnail_url)."""
    html = fetch_html(detail_url, scraper, retries=2)
    if not html:
        return [], ""
    bs    = BeautifulSoup(html, "lxml")
    thumb = ""

    # Lấy data-livestream-id từ widget
    widget = bs.find(class_="livestream_widget")
    if widget:
        lid = widget.get("data-livestream-id","")
        if lid:
            log(f"    livestream-id: {lid}")

    # og:image thumbnail
    og = bs.find("meta", property="og:image")
    if og:
        thumb = og.get("content","")

    streams = extract_streams_from_html(detail_url, html)
    return streams, thumb

# ── Build IPTV JSON ───────────────────────────────────────────
def make_id(*parts) -> str:
    raw  = "-".join(str(p) for p in parts if p)
    slug = re.sub(r"[^a-zA-Z0-9]+","-",raw).strip("-").lower()
    return (slug[:48]+"-"+hashlib.md5(raw.encode()).hexdigest()[:8]
            if len(slug)>56 else slug)

def build_display_title(m: dict) -> str:
    base  = m["base_title"]
    score = m.get("score","")
    t     = m.get("time_str","")
    d     = m.get("date_str","")

    if m["status"] == "live":
        lt = m.get("live_time","")
        if score:
            live_suffix = (" " + lt + "'") if lt else ""
        return f"{m['home_team']} {score} {m['away_team']}  🔴{live_suffix}"
        return f"{base}  🔴 LIVE"
    elif m["status"] == "finished":
        if score:
            return f"{m['home_team']} {score} {m['away_team']}  ✅"
        return f"{base}  ✅ KT"
    else:
        time_info = ""
        if t and d:   time_info = f"  🕐 {t} | {d}"
        elif t:       time_info = f"  🕐 {t}"
        elif d:       time_info = f"  📅 {d}"
        return f"{base}{time_info}"

def build_channel(m: dict, streams: list[dict], thumb: str, index: int) -> dict:
    ch_id        = make_id("gv", str(index), re.sub(r"[^a-z0-9]","-",m["base_title"].lower())[:24])
    display_name = build_display_title(m)
    league       = m.get("league","")
    score        = m.get("score","")

    # Labels
    labels = []
    status_cfg = {
        "live":     {"text":"● LIVE",          "color":"#E73131","text_color":"#ffffff"},
        "upcoming": {"text":"🕐 Sắp diễn ra",  "color":"#1A6DD5","text_color":"#ffffff"},
        "finished": {"text":"✅ Kết thúc",      "color":"#444444","text_color":"#ffffff"},
    }.get(m["status"], {"text":"● LIVE","color":"#E73131","text_color":"#ffffff"})
    labels.append({**status_cfg, "position":"top-left"})

    # Label giải đấu
    if league:
        labels.append({"text":f"⚽ {league[:25]}", "position":"top-right",
                        "color":"#0a3d62","text_color":"#ffffff"})

    # Label tỉ số live
    if score and m["status"] == "live":
        lt = m.get("live_time","")
        labels.append({"text":f"{score}{(' '+lt+chr(39)) if lt else ''}",
                        "position":"bottom-right","color":"#E73131","text_color":"#ffffff"})

    # Stream links
    stream_links = []
    for idx, s in enumerate(streams):
        stream_links.append({
            "id":      make_id(ch_id, f"l{idx}"),
            "name":    s.get("name", f"Link {idx+1}"),
            "type":    s.get("type","hls"),
            "default": idx == 0,
            "url":     s["url"],
            "request_headers": [
                {"key":"Referer",    "value": s.get("referer", m["detail_url"])},
                {"key":"User-Agent", "value": CHROME_UA},
            ],
        })

    if not stream_links:
        stream_links.append({
            "id":"lnk0","name":"Xem trực tiếp","type":"iframe","default":True,
            "url": m["detail_url"],
            "request_headers":[
                {"key":"Referer",    "value": m["detail_url"]},
                {"key":"User-Agent", "value": CHROME_UA},
            ],
        })

    stream_obj = [{
        "id":           make_id(ch_id,"st0"),
        "name":         "Trực tiếp",
        "stream_links": stream_links,
    }]

    # Thumbnail: dùng logo đội nếu không có thumb từ detail
    img_url = (
        thumb
        or m.get("logo_a","")
        or m.get("league_logo","")
        or f"{BASE_URL}/favicon.ico"
    )
    img_obj = {
        "padding":1,"background_color":"#0a1a2e","display":"contain",
        "url":img_url,"width":1600,"height":1200,
    }

    # Description
    parts = []
    if league: parts.append(league[:40])
    if m.get("time_str"): parts.append(m["time_str"])
    if m.get("date_str"): parts.append(m["date_str"])
    if m["status"] == "live":
        lt = m.get("live_time","")
        parts.append(f"🔴 LIVE{' '+lt+chr(39) if lt else ''}")
        if score: parts.append(score)
    elif m["status"] == "upcoming":
        parts.append("🕐 Sắp diễn ra")
    blv = m.get("blv_list",[])
    if blv: parts.append(f"🎙 {', '.join(blv[:2])}")
    description = " | ".join(p for p in parts if p)

    content_name = display_name
    if league: content_name += f" · {league}"

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
            "id":   make_id(ch_id,"src"),
            "name": "GioVang Live",
            "contents": [{
                "id":      make_id(ch_id,"ct"),
                "name":    content_name,
                "streams": stream_obj,
            }],
        }],
    }

def build_iptv_json(channels: list[dict], now_str: str) -> dict:
    return {
        "id":          "giovang-live",
        "name":        "GioVang TV - Trực tiếp bóng đá",
        "url":         BASE_URL + "/",
        "description": (f"GioVang.vin — {len(channels)} trận đang diễn ra & sắp diễn ra. "
                        f"Cập nhật lúc {now_str}."),
        "disable_ads": True,
        "color":       "#f5a623",
        "grid_number": 3,
        "image":       {"type":"cover","url":f"{BASE_URL}/wp-content/uploads/2025/01/logo.png"},
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
    ap = argparse.ArgumentParser(description="Crawler giovang.vin → IPTV JSON (API-based)")
    ap.add_argument("--no-stream", action="store_true", help="Không crawl stream (nhanh hơn)")
    ap.add_argument("--all",       action="store_true", help="Lấy tất cả, không chỉ hot match")
    ap.add_argument("--output",    default=OUTPUT_FILE)
    args = ap.parse_args()

    log("\n" + "═"*62)
    log("  🏟  CRAWLER — giovang.vin  v2.0  (API JSON)")
    log("  🔴 Đang diễn ra  +  🕐 Sắp diễn ra  →  🔥 Hot Match")
    log("═"*62+"\n")

    now_vn  = datetime.now(VN_TZ)
    now_str = now_vn.strftime("%d/%m/%Y %H:%M") + " ICT"

    scraper    = make_scraper()
    only_hot   = not args.all

    # ── Bước 1: Fetch matches từ API ──────────────────────────
    matches = fetch_matches(scraper, only_hot=only_hot)
    if not matches:
        log("❌ Không có trận nào. Thoát.")
        sys.exit(1)

    # ── Bước 2: Crawl stream từng trận ────────────────────────
    log(f"\n🎬 Bước 3: Crawl stream {'(bỏ qua)' if args.no_stream else ''}...")
    channels = []
    for i, m in enumerate(matches, 1):
        log(f"\n  [{i:02d}/{len(matches):02d}] {m['base_title']}")
        log(f"        {m['status']} | {m['league']} | {m['time_str']} {m['date_str']}")
        log(f"        URL: {m['detail_url']}")

        streams, thumb = [], ""
        if not args.no_stream:
            streams, thumb = crawl_detail(m["detail_url"], scraper)
            log(f"        Streams: {len(streams)} | Thumb: {'✓' if thumb else '✗'}")
            time.sleep(0.5)

        ch = build_channel(m, streams, thumb, i)
        channels.append(ch)

    # ── Bước 3: Ghi file ──────────────────────────────────────
    result = build_iptv_json(channels, now_str)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log(f"\n{'═'*62}")
    log(f"  ✅ Xong!  📁 {args.output}  |  {len(channels)} trận  |  {now_str}")
    log("═"*62+"\n")


if __name__ == "__main__":
    main()
