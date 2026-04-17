#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   CRAWLER giovang.vin  v3.0                                  ║
║   API: live-api.keovip88.net  (live.json + all.json)         ║
║   Output: IPTV JSON — nhóm "🔥 Hot Match"                   ║
╚══════════════════════════════════════════════════════════════╝

Cài đặt:  pip install cloudscraper requests
Chạy:
    python crawler_giovang.py                   # full (có stream)
    python crawler_giovang.py --no-stream       # nhanh, không lấy stream
    python crawler_giovang.py --all             # lấy toàn bộ, không chỉ hot
    python crawler_giovang.py --output out.json
"""

import argparse, hashlib, json, re, sys, time, unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import cloudscraper, requests
except ImportError:
    print("Cài: pip install cloudscraper requests")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────
BASE_URL    = "https://giovang.vin"
API_LIVE    = "https://live-api.keovip88.net/storage/livestream/live.json"
API_ALL     = "https://live-api.keovip88.net/storage/livestream/all.json"
OUTPUT_FILE = "giovang_iptv.json"
VN_TZ       = timezone(timedelta(hours=7))
CHROME_UA   = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# BLV map từ commentatorsList của giovang.vin
BLV_MAP = {
    "nha-dai":           "Nhà Đài",
    "blv-tho":           "BLV Thỏ",
    "blv-perry":         "BLV Perry",
    "blv-1":             "BLV Tí",
    "blv-3":             "BLV Dần",
    "blv-5":             "BLV Thìn",
    "blv-6":             "BLV Tỵ",
    "blv-10":            "BLV Dậu",
    "blv-12":            "BLV Hợi",
    "blv-tom":           "BLV Tôm",
    "blv-ben":           "BLV Ben",
    "blv-cay":           "BLV Cầy",
    "blv-bang":          "BLV Băng",
    "blv-mason":         "BLV Mason",
    "blv-che":           "BLV Chè",
    "blv-cam":           "BLV Câm",
    "blv-dory":          "BLV Dory",
    "blv-chanh":         "BLV Chanh",
    "blv-nen":           "BLV Nến",
}

# Status code → trạng thái + nhóm
LIVE_CODES     = {"1H", "2H", "HT", "PEN", "ET", "BT", "LIVE", "INT", "SUSP", "P"}
FINISHED_CODES = {"FT", "AET", "AWD", "WO", "ABD", "CANC"}
# Còn lại là "NS" (Not Started)

def log(msg): print(msg, flush=True)

# ─── HTTP ────────────────────────────────────────────────────
def make_scraper():
    sc = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    sc.headers.update({
        "User-Agent":      CHROME_UA,
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
        "Referer":         BASE_URL + "/",
    })
    return sc

def fetch_json(url: str, sc, label: str = "") -> dict:
    ts = int(time.time() * 1000)
    for attempt in range(3):
        try:
            r = sc.get(f"{url}?t={ts}", timeout=20)
            r.raise_for_status()
            data = r.json()
            n    = len(data.get("response", []))
            log(f"  ✓ {label or url.split('/')[-1].split('.')[0]}  →  {n} fixtures")
            return data
        except Exception as e:
            wait = 2 ** attempt
            log(f"  ⚠ {label} lần {attempt+1}/3: {e} — chờ {wait}s")
            if attempt < 2: time.sleep(wait)
    return {}

def fetch_html(url: str, sc) -> str:
    for attempt in range(3):
        try:
            r = sc.get(url, timeout=25)
            r.raise_for_status()
            return r.text
        except Exception as e:
            wait = 2 ** attempt
            log(f"  ⚠ fetch_html lần {attempt+1}/3: {e} — chờ {wait}s")
            if attempt < 2: time.sleep(wait)
    return ""

# ─── Slug (port từ JS getSlug của giovang.vin) ───────────────
_FROM = "àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ"
_TO   = "aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyyd"

def get_slug(s: str) -> str:
    s = s.strip().lower()
    for f, t in zip(_FROM, _TO):
        s = s.replace(f, t)
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s

def build_detail_url(home: str, away: str, day_month: str, fid: str) -> str:
    """Tạo URL trang xem trực tiếp — giống hệt JS của giovang.vin."""
    raw  = f"truc tiep {home} vs {away}-{day_month}--{fid}"
    slug = get_slug(raw)
    return f"{BASE_URL}/{slug}/"

# ─── Giờ thi đấu ─────────────────────────────────────────────
def parse_kickoff(fixture: dict) -> tuple[str, str, str]:
    """
    API trả về:
        fixture['time']      = "19:00"  (giờ địa phương, đã là GMT+7)
        fixture['day_month'] = "17/04"
        fixture['date']      = "17-04-2026" (dùng để sort)
    Trả về (time_str, date_str, sort_key).
    """
    raw_time  = fixture.get("time", "")       # "19:00"
    day_month = fixture.get("day_month", "")  # "17/04"
    raw_date  = fixture.get("date", "")       # "17-04-2026"

    m_t = re.match(r"(\d{1,2}):(\d{2})", raw_time)
    m_d = re.match(r"(\d{1,2})[/\-](\d{1,2})", day_month)

    time_str = f"{int(m_t.group(1)):02d}:{m_t.group(2)}" if m_t else ""
    date_str = day_month  # đã đúng định dạng DD/MM

    # Sort key — parse từ date "17-04-2026" + time
    sort_key = ""
    if raw_date and m_t:
        parts = re.split(r"[-/]", raw_date)
        if len(parts) == 3:
            try:
                dd, mm, yyyy = int(parts[0]), int(parts[1]), int(parts[2])
                hh, mn = int(m_t.group(1)), int(m_t.group(2))
                sort_key = f"{yyyy}{mm:02d}{dd:02d}{hh:02d}{mn:02d}"
            except Exception:
                pass
    if not sort_key and m_d and m_t:
        sort_key = f"{int(m_d.group(2)):02d}{int(m_d.group(1)):02d}{int(m_t.group(1)):02d}{m_t.group(2)}"

    return time_str, date_str, sort_key

# ─── Parse fixture ────────────────────────────────────────────
def get_status(sc: str) -> str:
    if sc in LIVE_CODES:     return "live"
    if sc in FINISHED_CODES: return "finished"
    return "upcoming"   # NS

def get_blv_names(blv_list: list) -> list[str]:
    """Lấy tên BLV, loại bỏ 'nha-dai'."""
    if not isinstance(blv_list, list):
        return []
    return [BLV_MAP.get(b, b) for b in blv_list if b != "nha-dai"]

def parse_fixture(f: dict) -> dict:
    fid       = str(f.get("id", ""))
    teams     = f.get("teams", {})
    home      = teams.get("home", {})
    away      = teams.get("away", {})
    league    = f.get("league", {}) or {}
    score_obj = f.get("score", {}) or {}
    goals     = f.get("goals", {}) or {}
    blv_raw   = f.get("blv", []) or []
    sc        = f.get("status_code", "NS")

    # Tên đội
    home_name = home.get("name", "")
    away_name = away.get("name", "")

    # Tỉ số: ưu tiên goals (đơn giản), fallback score.fulltime
    gh = goals.get("home")
    ga = goals.get("away")
    if gh is None and ga is None:
        ft = score_obj.get("fulltime", {}) or {}
        gh, ga = ft.get("home"), ft.get("away")
    score = f"{gh}-{ga}" if gh is not None and ga is not None else ""

    # Giờ
    time_str, date_str, sort_key = parse_kickoff(f)

    # URL
    detail_url = build_detail_url(
        home_name, away_name,
        f.get("day_month", ""),
        fid
    )

    # BLV thật
    blv_names = get_blv_names(blv_raw)

    return {
        "id":          fid,
        "base_title":  f"{home_name} vs {away_name}",
        "home_team":   home_name,
        "away_team":   away_name,
        "logo_a":      home.get("logo", ""),
        "logo_b":      away.get("logo", ""),
        "league":      league.get("title", ""),
        "league_logo": league.get("logo", "") or league.get("icon", ""),
        "score":       score,
        "status":      get_status(sc),
        "status_code": sc,
        "live_time":   str(f.get("live_time", "")),
        "time_str":    time_str,
        "date_str":    date_str,
        "sort_key":    sort_key,
        "detail_url":  detail_url,
        "blv_keys":    [b for b in blv_raw if b != "nha-dai"],
        "blv_names":   blv_names,
        "is_hot":      bool(f.get("is_hot")),
        "is_hot_top":  bool(f.get("is_hot_top")),
        "sport_type":  f.get("type", "football"),
        "btn_soikeo":  f.get("btn_soikeo", ""),
        "thumbnail":   f.get("thumbnail", "") or f.get("thumb", ""),
    }

# ─── Fetch & lọc trận ────────────────────────────────────────
def fetch_all_matches(sc, only_hot: bool) -> list[dict]:
    """
    Gọi 2 API:
      live.json → trận đang diễn ra (status_code ≠ NS, ≠ FT)
      all.json  → trận sắp diễn ra (status_code == NS)
    Trả về list đã sort: live trước → upcoming sau → theo giờ.
    """
    live_data = fetch_json(API_LIVE, sc, "live.json")
    all_data  = fetch_json(API_ALL,  sc, "all.json")

    matches:  list[dict] = []
    seen_ids: set[str]   = set()

    # ── Đang diễn ra ──────────────────────────────────────────
    for f in (live_data.get("response") or []):
        sc_code = f.get("status_code", "NS")
        status  = get_status(sc_code)
        if status == "finished":
            continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")):
            continue
        m   = parse_fixture(f)
        fid = m["id"]
        if fid and fid not in seen_ids:
            seen_ids.add(fid)
            matches.append(m)
            log(f"    🔴 [{sc_code:3s}] {m['base_title']}  |  {m['league']}  |  {m['time_str']} {m['date_str']}")

    # ── Sắp diễn ra ───────────────────────────────────────────
    for f in (all_data.get("response") or []):
        sc_code = f.get("status_code", "NS")
        if sc_code != "NS":
            continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")):
            continue
        m   = parse_fixture(f)
        fid = m["id"]
        if fid and fid not in seen_ids:
            seen_ids.add(fid)
            matches.append(m)
            log(f"    🕐 [NS ] {m['base_title']}  |  {m['league']}  |  {m['time_str']} {m['date_str']}")

    # Sort: live → upcoming, rồi theo giờ
    prio = {"live": 0, "upcoming": 1, "finished": 2}
    matches.sort(key=lambda x: (prio.get(x["status"], 9), x["sort_key"]))

    log(f"\n  ✅ Tổng {len(matches)} trận  "
        f"({sum(1 for m in matches if m['status']=='live')} đang live, "
        f"{sum(1 for m in matches if m['status']=='upcoming')} sắp tới)")
    return matches

# ─── Stream từ trang detail ───────────────────────────────────
_Q_RE    = re.compile(r"[_-](?:full[_-]?hd|fhd|1080p?|720p?|480p?|360p?|hd|sd)$", re.I)
_Q_MAP   = {"hd":"HD","sd":"SD","full-hd":"Full HD","full_hd":"Full HD",
            "fhd":"Full HD","1080":"Full HD","1080p":"Full HD",
            "720":"HD","720p":"HD","480":"SD","480p":"SD","360":"360p","360p":"360p"}
_Q_ORDER = {"Full HD":0,"HD":1,"SD":2,"360p":3,"Auto":4}
_Q_SFXS  = [("Full HD","full-hd"),("HD","hd"),("SD","sd")]

def _qlabel(url: str) -> str:
    n = re.sub(r"\.\w+$","",url.rstrip("/").split("/")[-1]).lower()
    m = _Q_RE.search(n)
    return _Q_MAP.get(m.group(0).lstrip("-_").lower(),"Auto") if m else "Auto"

def _qbase(url: str) -> str:
    n = re.sub(r"\.\w+$","",url.rstrip("/").split("/")[-1])
    return _Q_RE.sub("",n).lower()

def _derive(url: str, referer: str) -> list[dict]:
    slash = url.rfind("/")
    if slash < 0:
        return [{"name":"Auto","url":url,"type":"hls","referer":referer}]
    pre  = url[:slash+1]
    fn   = url[slash+1:]
    ei   = fn.rfind(".")
    ext  = fn[ei:] if ei >= 0 else ".m3u8"
    base = _Q_RE.sub("", fn[:ei] if ei >= 0 else fn)
    return [{"name":lb,"url":f"{pre}{base}_{sfx}{ext}","type":"hls","referer":referer}
            for lb, sfx in _Q_SFXS]

def _walk_m3u8(obj, found: list, depth=0):
    if depth > 12: return
    if isinstance(obj, str):
        if obj.startswith("http") and ".m3u8" in obj:
            found.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values(): _walk_m3u8(v, found, depth+1)
    elif isinstance(obj, list):
        for i in obj: _walk_m3u8(i, found, depth+1)

def extract_streams(detail_url: str, html: str) -> list[dict]:
    """Lấy stream HLS từ HTML trang trận. Fallback iframe nếu không có."""
    from bs4 import BeautifulSoup
    bs   = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    urls: list[str] = []

    def add(u: str):
        u = u.strip().split("?")[0].split("#")[0]
        if u and u.startswith("http") and ".m3u8" in u and u not in seen:
            seen.add(u); urls.append(u)

    # 1. __NEXT_DATA__
    tag = bs.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            found: list[str] = []
            _walk_m3u8(json.loads(tag.string), found)
            for u in found: add(u)
        except Exception:
            pass

    # 2. Regex toàn bộ HTML
    for mo in re.finditer(r"(https?://[^\s'\"<>\]\\]+\.m3u8)", html):
        add(mo.group(1))

    # 3. Script tags — tìm các biến stream phổ biến
    for script in bs.find_all("script"):
        c = script.string or ""
        for pat in [
            r'"(?:file|src|stream|url|hls|videoUrl|streamUrl|hlsUrl)"\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"',
            r"(?:streamUrl|videoUrl|hlsUrl|m3u8Url|src)\s*[=:]\s*[\"'](https?://[^\"']+\.m3u8[^\"']*)[\"']",
        ]:
            for mo in re.finditer(pat, c, re.I): add(mo.group(1))

    if not urls:
        # Fallback iframe
        for iframe in bs.find_all("iframe", src=True):
            src = iframe["src"]
            if re.search(r"live|stream|embed|player|sport|watch", src, re.I):
                return [{"name":"Live","url":src,"type":"iframe","referer":detail_url}]
        return [{"name":"Xem trực tiếp","url":detail_url,"type":"iframe","referer":detail_url}]

    # Nhóm theo base URL đầu tiên
    first_base = _qbase(urls[0])
    group      = [u for u in urls if _qbase(u) == first_base]

    if len(group) >= 2:
        streams = [{"name":_qlabel(u),"url":u,"type":"hls","referer":detail_url}
                   for u in group]
        streams.sort(key=lambda x: _Q_ORDER.get(x["name"],99))
        return streams

    # Chỉ 1 URL → tự sinh Full HD / HD / SD
    return _derive(urls[0], detail_url)

def crawl_detail(url: str, sc) -> tuple[list[dict], str]:
    """Crawl trang trận → (streams, thumb_url)."""
    html = fetch_html(url, sc)
    if not html:
        return [], ""
    from bs4 import BeautifulSoup
    bs    = BeautifulSoup(html, "lxml")
    thumb = ""
    og    = bs.find("meta", property="og:image")
    if og:
        thumb = og.get("content", "")
    streams = extract_streams(url, html)
    return streams, thumb

# ─── Build IPTV channel ───────────────────────────────────────
def make_id(*parts) -> str:
    raw  = "-".join(str(p) for p in parts if p)
    slug = re.sub(r"[^a-zA-Z0-9]+","-",raw).strip("-").lower()
    digest = hashlib.md5(raw.encode()).hexdigest()[:8]
    return slug[:48]+"-"+digest if len(slug) > 56 else slug

def build_title(m: dict) -> str:
    base  = m["base_title"]
    score = m["score"]
    t, d  = m["time_str"], m["date_str"]

    if m["status"] == "live":
        lt = m["live_time"]
        suffix = f" {lt}'" if lt and lt != "0" else ""
        if score:
            return f"{m['home_team']} {score} {m['away_team']}  🔴{suffix}"
        return f"{base}  🔴 LIVE{suffix}"

    if m["status"] == "finished":
        if score: return f"{m['home_team']} {score} {m['away_team']}  ✅"
        return f"{base}  ✅ KT"

    # upcoming
    time_info = ""
    if t and d:   time_info = f"  🕐 {t} | {d}"
    elif t:       time_info = f"  🕐 {t}"
    elif d:       time_info = f"  📅 {d}"
    return f"{base}{time_info}"

def build_channel(m: dict, streams: list[dict], thumb: str, idx: int) -> dict:
    ch_id = make_id("gv", str(idx),
                    re.sub(r"[^a-z0-9]","-", m["base_title"].lower())[:24])
    title  = build_title(m)
    league = m["league"]
    score  = m["score"]

    # ── Labels ────────────────────────────────────────────────
    labels = []

    # Trạng thái (top-left)
    status_label = {
        "live":     ("● LIVE",         "#D32F2F"),
        "upcoming": ("🕐 Sắp diễn ra", "#1565C0"),
        "finished": ("✅ Kết thúc",     "#424242"),
    }.get(m["status"], ("● LIVE", "#D32F2F"))
    labels.append({"text": status_label[0], "color": status_label[1],
                   "text_color": "#ffffff", "position": "top-left"})

    # Giải đấu (top-right)
    if league:
        labels.append({"text": league[:28], "color": "#0D47A1",
                       "text_color": "#ffffff", "position": "top-right"})

    # Tỉ số live (bottom-right)
    if score and m["status"] == "live":
        lt  = m["live_time"]
        txt = f"{score}  {lt}'" if lt and lt != "0" else score
        labels.append({"text": txt, "color": "#B71C1C",
                       "text_color": "#ffffff", "position": "bottom-right"})

    # BLV (bottom-left) nếu có
    blv_names = m.get("blv_names", [])
    if blv_names:
        blv_txt = f"🎙 {blv_names[0]}" if len(blv_names) == 1 else f"🎙 {len(blv_names)} BLV"
        labels.append({"text": blv_txt, "color": "#1B5E20",
                       "text_color": "#ffffff", "position": "bottom-left"})

    # ── Stream links ──────────────────────────────────────────
    links = []
    for i, s in enumerate(streams):
        links.append({
            "id":      make_id(ch_id, f"l{i}"),
            "name":    s.get("name", f"Link {i+1}"),
            "type":    s.get("type", "hls"),
            "default": i == 0,
            "url":     s["url"],
            "request_headers": [
                {"key": "Referer",    "value": s.get("referer", m["detail_url"])},
                {"key": "User-Agent", "value": CHROME_UA},
            ],
        })

    if not links:
        links.append({
            "id": "lnk0", "name": "Xem trực tiếp",
            "type": "iframe", "default": True,
            "url": m["detail_url"],
            "request_headers": [
                {"key": "Referer",    "value": m["detail_url"]},
                {"key": "User-Agent", "value": CHROME_UA},
            ],
        })

    # ── Thumbnail ─────────────────────────────────────────────
    img_url = (
        thumb
        or m.get("thumbnail", "")
        or m.get("logo_a", "")
        or m.get("league_logo", "")
        or f"{BASE_URL}/wp-content/uploads/2025/01/logo.png"
    )
    img_obj = {
        "padding": 2, "background_color": "#071a2e",
        "display": "contain",
        "url": img_url, "width": 1600, "height": 1200,
    }

    # ── Description ───────────────────────────────────────────
    desc_parts = []
    if league:           desc_parts.append(league)
    if m["time_str"]:    desc_parts.append(m["time_str"])
    if m["date_str"]:    desc_parts.append(m["date_str"])
    if m["status"] == "live":
        lt  = m["live_time"]
        desc_parts.append(f"🔴 LIVE{' '+lt+'min' if lt and lt!='0' else ''}")
        if score:         desc_parts.append(score)
    elif m["status"] == "upcoming":
        desc_parts.append("🕐 Sắp diễn ra")
    if blv_names:         desc_parts.append("🎙 " + ", ".join(blv_names[:3]))

    return {
        "id":            ch_id,
        "name":          title,
        "description":   " | ".join(desc_parts),
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
                "name":    title + (f" · {league}" if league else ""),
                "streams": [{
                    "id":           make_id(ch_id, "st"),
                    "name":         "Trực tiếp",
                    "stream_links": links,
                }],
            }],
        }],
    }

# ─── Build IPTV JSON ─────────────────────────────────────────
def build_iptv_json(channels: list[dict], now_str: str) -> dict:
    n_live = sum(1 for c in channels
                 if any(l["text"].startswith("● LIVE")
                        for l in c.get("labels",[])))
    return {
        "id":          "giovang-iptv",
        "name":        "GioVang TV",
        "url":         BASE_URL + "/",
        "description": (f"Trực tiếp bóng đá — {n_live} đang live, "
                        f"{len(channels)-n_live} sắp tới. Cập nhật {now_str}"),
        "disable_ads": True,
        "color":       "#f5a623",
        "grid_number": 3,
        "image": {
            "type": "cover",
            "url":  f"{BASE_URL}/wp-content/uploads/2025/01/logo.png",
        },
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

# ─── Main ────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-stream", action="store_true",
                    help="Không crawl stream (nhanh hơn, chỉ lấy info trận)")
    ap.add_argument("--all",       action="store_true",
                    help="Lấy toàn bộ trận, không chỉ Hot Match")
    ap.add_argument("--output",    default=OUTPUT_FILE)
    args = ap.parse_args()

    log("\n" + "═"*62)
    log("  🏟  CRAWLER giovang.vin  v3.0")
    log("  📡  API: live-api.keovip88.net")
    log("  🔴  Đang diễn ra  +  🕐  Sắp diễn ra  →  🔥 Hot Match")
    log("═"*62 + "\n")

    now_str = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M ICT")
    sc      = make_scraper()

    # ── Bước 1: Fetch API ─────────────────────────────────────
    log("📡 Bước 1: Fetch dữ liệu từ API...")
    matches = fetch_all_matches(sc, only_hot=not args.all)

    if not matches:
        log("❌ Không có trận nào. Kiểm tra API hoặc thêm --all")
        sys.exit(1)

    # ── Bước 2: Crawl stream ──────────────────────────────────
    if args.no_stream:
        log(f"\n⚡ Bỏ qua crawl stream (--no-stream). {len(matches)} trận.")
    else:
        log(f"\n🎬 Bước 2: Crawl stream ({len(matches)} trận)...")

    channels = []
    for i, m in enumerate(matches, 1):
        log(f"\n  [{i:02d}/{len(matches):02d}] {m['base_title']}")
        log(f"        {m['status']:8s} | {m['league']:25s} | {m['time_str']} {m['date_str']}")
        if m["blv_names"]:
            log(f"        BLV: {', '.join(m['blv_names'])}")

        streams, thumb = [], ""
        if not args.no_stream:
            streams, thumb = crawl_detail(m["detail_url"], sc)
            log(f"        Streams: {len(streams)}  |  Thumb: {'✓' if thumb else '✗'}")
            time.sleep(0.4)

        channels.append(build_channel(m, streams, thumb, i))

    # ── Bước 3: Ghi file ──────────────────────────────────────
    result = build_iptv_json(channels, now_str)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    live_count = sum(1 for m in matches if m["status"] == "live")
    up_count   = sum(1 for m in matches if m["status"] == "upcoming")

    log(f"\n{'═'*62}")
    log(f"  ✅  Hoàn tất!  →  {args.output}")
    log(f"  📊  {len(channels)} trận  |  🔴 {live_count} live  |  🕐 {up_count} sắp tới")
    log(f"  🕐  {now_str}")
    log("═"*62 + "\n")


if __name__ == "__main__":
    main()
