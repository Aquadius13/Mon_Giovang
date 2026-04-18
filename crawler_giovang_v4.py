#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   CRAWLER giovang.vin  v5.0  —  HOÀN CHỈNH                 ║
║                                                              ║
║   Luồng:                                                     ║
║   1. GET live.json + all.json  → danh sách trận Hot Match   ║
║   2. POST WP AJAX              → logo đội từ flashscore     ║
║   3. GET /api/fixtures/{id}    → link_stream_hd / sd (HLS)  ║
║      (cần cookie session từ giovang.vin)                     ║
║   4. Build IPTV JSON với 2 logo + multi-BLV streams         ║
╚══════════════════════════════════════════════════════════════╝

Cài:  pip install cloudscraper requests
Chạy:
    python crawler_giovang.py                  # đầy đủ
    python crawler_giovang.py --no-stream      # không lấy stream
    python crawler_giovang.py --all            # toàn bộ trận (không chỉ hot)
    python crawler_giovang.py --output out.json
"""

import argparse, base64, hashlib, json, re, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import cloudscraper
    import requests
except ImportError:
    print("Cài: pip install cloudscraper requests")
    sys.exit(1)

# ─── Constants ────────────────────────────────────────────────
BASE_URL    = "https://giovang.vin"
API_LIVE    = "https://live-api.keovip88.net/storage/livestream/live.json"
API_ALL     = "https://live-api.keovip88.net/storage/livestream/all.json"
API_STREAM  = "https://live-api.keovip88.net/api/fixtures/{fid}"
WP_AJAX     = "https://giovang.vin/wp-admin/admin-ajax.php"
OUTPUT_FILE = "giovang_iptv.json"
VN_TZ       = timezone(timedelta(hours=7))
CHROME_UA   = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_LOGO = "https://giovang.vin/wp-content/uploads/2025/01/logo.png"

LIVE_CODES     = {"1H", "2H", "HT", "PEN", "ET", "BT", "LIVE", "INT", "SUSP", "P"}
FINISHED_CODES = {"FT", "AET", "AWD", "WO", "ABD", "CANC"}

BLV_MAP = {
    "nha-dai":"Nhà Đài","blv-tho":"BLV Thỏ","blv-perry":"BLV Perry",
    "blv-1":"BLV Tí","blv-3":"BLV Dần","blv-5":"BLV Thìn","blv-6":"BLV Tỵ",
    "blv-10":"BLV Dậu","blv-12":"BLV Hợi","blv-tom":"BLV Tôm","blv-ben":"BLV Ben",
    "blv-cay":"BLV Cầy","blv-bang":"BLV Băng","blv-mason":"BLV Mason",
    "blv-che":"BLV Chè","blv-cam":"BLV Câm","blv-dory":"BLV Dory",
    "blv-chanh":"BLV Chanh","blv-nen":"BLV Nến",
}

def log(msg): print(msg, flush=True)

# ─── HTTP client ──────────────────────────────────────────────
def make_scraper():
    """
    Tạo scraper có thể vượt qua Cloudflare.
    Scraper visit giovang.vin trước để lấy cookie session,
    rồi dùng cookie đó gọi API stream.
    """
    sc = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    sc.headers.update({
        "User-Agent":      CHROME_UA,
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer":         BASE_URL + "/",
    })
    return sc

def init_session(sc):
    """Visit giovang.vin để lấy cookie session — cần thiết cho API stream."""
    log("  🍪 Init session (visit giovang.vin)...")
    try:
        r = sc.get(BASE_URL + "/", timeout=20)
        cookies = dict(sc.cookies)
        log(f"     Status: {r.status_code} | Cookies: {list(cookies.keys())}")
        return r.status_code == 200
    except Exception as e:
        log(f"     ⚠ {e}")
        return False

def get_json(url, sc, label="", params=None):
    for i in range(3):
        try:
            r = sc.get(url, timeout=20, params=params)
            r.raise_for_status()
            data = r.json()
            n = len(data.get("response", [])) if isinstance(data, dict) else "?"
            log(f"  ✓ {label}  →  {n} items")
            return data
        except Exception as e:
            if i < 2: time.sleep(2 ** i)
    return {}

def post_ajax(sc, payload):
    for i in range(3):
        try:
            r = sc.post(WP_AJAX, data=payload, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i < 2: time.sleep(2 ** i)
    return {}

# ─── Slug (port từ JS giovang.vin) ───────────────────────────
_FROM = "àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđç·/_,:;"
_TO   = "aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyyddc------"

def slugify(s):
    s = s.strip().lower()
    for f, t in zip(_FROM, _TO):
        s = s.replace(f, t)
    s = re.sub(r"[^a-z0-9 -]", "", s)
    s = re.sub(r"\s+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")

def build_detail_url(home, away, day_month, fid):
    raw = f"truc tiep {home} vs {away}-{day_month}--{fid}"
    return f"{BASE_URL}/{slugify(raw)}/"

# ─── Parse giờ ───────────────────────────────────────────────
def parse_time(f):
    """
    API trả về time (HH:MM, giờ GMT+7) và day_month (DD/MM).
    Trả về (time_str, date_str, sort_key).
    """
    raw_t = f.get("time", "")
    dm    = f.get("day_month", "")
    raw_d = f.get("date", "")

    mt = re.match(r"(\d{1,2}):(\d{2})", raw_t)
    time_str = f"{int(mt.group(1)):02d}:{mt.group(2)}" if mt else ""
    date_str = dm

    sort_key = ""
    if mt:
        # date có thể là "DD-MM-YYYY" hoặc "DD/MM/YYYY"
        p = re.split(r"[-/]", raw_d)
        if len(p) == 3:
            try:
                dd, mm, yy = int(p[0]), int(p[1]), int(p[2])
                hh, mn = int(mt.group(1)), int(mt.group(2))
                sort_key = f"{yy}{mm:02d}{dd:02d}{hh:02d}{mn:02d}"
            except Exception:
                pass
    return time_str, date_str, sort_key

def get_status(sc):
    if sc in LIVE_CODES:     return "live"
    if sc in FINISHED_CODES: return "finished"
    return "upcoming"

# ─── Parse fixture từ live/all API ───────────────────────────
def parse_fixture(f):
    fid    = str(f.get("id", ""))
    home   = f.get("teams", {}).get("home", {})
    away   = f.get("teams", {}).get("away", {})
    league = f.get("league", {}) or {}
    goals  = f.get("goals", {}) or {}
    blvs   = f.get("blv", []) or []
    sc     = f.get("status_code", "NS")

    # Tỉ số
    gh = goals.get("home")
    ga = goals.get("away")
    if gh is None:
        ft = (f.get("score", {}) or {}).get("fulltime", {}) or {}
        gh, ga = ft.get("home"), ft.get("away")
    score = f"{gh}-{ga}" if gh is not None and ga is not None else ""

    time_str, date_str, sort_key = parse_time(f)
    home_name = home.get("name", "")
    away_name = away.get("name", "")
    blv_names = [BLV_MAP.get(b, b) for b in blvs if b != "nha-dai"]

    return {
        "id":         fid,
        "base_title": f"{home_name} vs {away_name}",
        "home_team":  home_name,
        "away_team":  away_name,
        "logo_a":     home.get("logo", ""),
        "logo_b":     away.get("logo", ""),
        "league":     league.get("title", ""),
        "score":      score,
        "status":     get_status(sc),
        "status_code":sc,
        "live_time":  str(f.get("live_time", "")),
        "time_str":   time_str,
        "date_str":   date_str,
        "sort_key":   sort_key,
        "detail_url": build_detail_url(home_name, away_name,
                                       f.get("day_month", ""), fid),
        "blv_keys":   [b for b in blvs if b != "nha-dai"],
        "blv_names":  blv_names,
        "is_hot":     bool(f.get("is_hot")),
        "is_hot_top": bool(f.get("is_hot_top")),
        "sport_type": f.get("type", "football"),
    }

# ─── Step 1: Fetch danh sách trận ────────────────────────────
def fetch_matches(sc, only_hot):
    log("\n📡 Bước 1: Fetch live.json + all.json...")
    ts = int(time.time() * 1000)
    live_data = get_json(API_LIVE, sc, "live.json", {"t": ts})
    all_data  = get_json(API_ALL,  sc, "all.json",  {"t": ts})

    matches, seen = [], set()

    # Đang diễn ra
    for f in (live_data.get("response") or []):
        if get_status(f.get("status_code", "NS")) == "finished":
            continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")):
            continue
        m = parse_fixture(f)
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"])
            matches.append(m)
            log(f"  🔴 [{m['status_code']:3s}] {m['base_title']} | {m['league']} | {m['time_str']}")

    # Sắp diễn ra
    for f in (all_data.get("response") or []):
        if f.get("status_code", "NS") != "NS":
            continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")):
            continue
        m = parse_fixture(f)
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"])
            matches.append(m)
            log(f"  🕐 [NS ] {m['base_title']} | {m['league']} | {m['time_str']}")

    prio = {"live": 0, "upcoming": 1, "finished": 2}
    matches.sort(key=lambda x: (prio.get(x["status"], 9), x["sort_key"]))
    log(f"\n  ✅ {len(matches)} trận — "
        f"🔴 {sum(1 for m in matches if m['status']=='live')} live | "
        f"🕐 {sum(1 for m in matches if m['status']=='upcoming')} sắp tới")
    return matches

# ─── Step 2: WP AJAX → logo chính xác từ flashscore ─────────
def fetch_wp_logos(sc, fid):
    """
    POST admin-ajax.php action=load_live_stream&id={fid}
    Response: { success, data: { home:{name,logo}, away:{name,logo}, ... } }
    Luôn trả về (dict, dict) — không bao giờ None.
    """
    try:
        resp = post_ajax(sc, {"action": "load_live_stream", "id": fid})
        if not resp or not isinstance(resp, dict):
            return {}, {}
        if not resp.get("success"):
            return {}, {}
        d = resp.get("data") or {}
        if not isinstance(d, dict):
            return {}, {}
        home = d.get("home") or {}
        away = d.get("away") or {}
        if not isinstance(home, dict): home = {}
        if not isinstance(away, dict): away = {}
        return home, away
    except Exception as e:
        log(f"     ⚠ WP AJAX lỗi: {e}")
        return {}, {}

# ─── Step 3: Stream API → link_stream_hd / sd ────────────────
def fetch_streams(sc, fid):
    """
    GET https://live-api.keovip88.net/api/fixtures/{fid}
    Response: {
      code: 0,
      response: {
        is_live: true/false,
        blv: [
          { blv_key, blv_name, link_stream_hd, link_stream_sd }
        ]
      }
    }
    Trả về list[dict]: [{name, url_hd, url_sd, blv_key, blv_name}]
    """
    url = API_STREAM.format(fid=fid)

    # Cần Referer đúng (trang trận đó) để API trả về data
    headers = {
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "vi-VN,vi;q=0.9",
        "Origin":          BASE_URL,
        "Referer":         BASE_URL + "/",
    }

    for attempt in range(3):
        try:
            r = sc.get(url, headers=headers, timeout=15)
            if r.status_code != 200 or not r.content:
                time.sleep(1)
                continue

            data = r.json()

            # API trả về: { code:0, response:{...} }
            if data.get("code") != 0:
                log(f"     ⚠ API code={data.get('code')}: {data.get('message','')}")
                return []

            resp     = data.get("response", {}) or {}
            is_live  = resp.get("is_live", False)
            blv_list = resp.get("blv") or []

            if not blv_list:
                log(f"     ℹ Không có BLV stream (is_live={is_live})")
                return []

            streams = []
            for blv in blv_list:
                key     = blv.get("blv_key", "")
                name    = blv.get("blv_name") or BLV_MAP.get(key, key)
                url_hd  = blv.get("link_stream_hd", "")
                url_sd  = blv.get("link_stream_sd", "")
                if url_hd or url_sd:
                    streams.append({
                        "blv_key":  key,
                        "blv_name": name,
                        "url_hd":   url_hd,
                        "url_sd":   url_sd,
                    })
                    log(f"     🎙 {name}: {url_hd[-45:] if url_hd else 'N/A'}")

            return streams

        except Exception as e:
            log(f"     ⚠ Attempt {attempt+1}/3: {e}")
            if attempt < 2: time.sleep(2)

    return []

# ─── Build thumbnail SVG 2 logo ──────────────────────────────
def build_image_obj(logo_a, logo_b):
    """
    SVG inline ghép logo home (trái) + VS + logo away (phải).
    Không cần Pillow, không cần file ngoài.
    """
    la = logo_a or DEFAULT_LOGO
    lb = logo_b or DEFAULT_LOGO

    if logo_a and logo_b:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="800" height="450" viewBox="0 0 800 450">'
            # Nền gradient tối
            '<defs>'
            '<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0%" stop-color="#0a1628"/>'
            '<stop offset="100%" stop-color="#071020"/>'
            '</linearGradient>'
            '</defs>'
            '<rect width="800" height="450" fill="url(#bg)"/>'
            # Đường chia giữa mờ
            '<line x1="400" y1="80" x2="400" y2="370" '
            'stroke="rgba(255,255,255,0.08)" stroke-width="1"/>'
            # Logo home
            f'<image href="{la}" x="60" y="100" width="220" height="220" '
            'preserveAspectRatio="xMidYMid meet" opacity="0.95"/>'
            # Logo away
            f'<image href="{lb}" x="520" y="100" width="220" height="220" '
            'preserveAspectRatio="xMidYMid meet" opacity="0.95"/>'
            # VS text
            '<text x="400" y="228" text-anchor="middle" dominant-baseline="middle" '
            'font-family="Arial Black,Arial,sans-serif" font-size="52" '
            'font-weight="900" fill="white" opacity="0.85">'
            'VS</text>'
            '</svg>'
        )
        b64 = base64.b64encode(svg.encode()).decode()
        return {
            "padding":          0,
            "background_color": "#0a1628",
            "display":          "contain",
            "url":              f"data:image/svg+xml;base64,{b64}",
            "width":            800,
            "height":           450,
        }

    return {
        "padding":          4,
        "background_color": "#0a1628",
        "display":          "contain",
        "url":              la,
        "width":            800,
        "height":           450,
    }

# ─── Build channel ───────────────────────────────────────────
def make_id(*parts):
    raw    = "-".join(str(p) for p in parts if p)
    slug   = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    digest = hashlib.md5(raw.encode()).hexdigest()[:8]
    return slug[:48] + "-" + digest if len(slug) > 56 else slug

def build_title(m):
    base  = m["base_title"]
    score = m["score"]
    t, d  = m["time_str"], m["date_str"]

    if m["status"] == "live":
        lt  = m["live_time"]
        sfx = f" {lt}'" if lt and lt not in ("0", "") else ""
        if score:
            return f"{m['home_team']} {score} {m['away_team']}  🔴{sfx}"
        return f"{base}  🔴 LIVE{sfx}"

    if m["status"] == "finished":
        if score: return f"{m['home_team']} {score} {m['away_team']}  ✅"
        return f"{base}  ✅ KT"

    # upcoming
    ti = ""
    if t and d: ti = f"  🕐 {t} | {d}"
    elif t:     ti = f"  🕐 {t}"
    elif d:     ti = f"  📅 {d}"
    return f"{base}{ti}"

def build_stream_links(ch_id, streams, detail_url):
    """
    Từ list BLV streams → flat list stream_links.
    Mỗi BLV có HD + SD → 2 link, đặt tên "BLV Ben – HD", "BLV Ben – SD".
    """
    links = []
    for i, s in enumerate(streams):
        name = s["blv_name"]
        for quality, url in [("HD", s["url_hd"]), ("SD", s["url_sd"])]:
            if not url:
                continue
            links.append({
                "id":      make_id(ch_id, f"l{i}{quality}"),
                "name":    f"{name} – {quality}",
                "type":    "hls",
                "default": len(links) == 0,   # link đầu tiên là default
                "url":     url,
                "request_headers": [
                    {"key": "Referer",    "value": BASE_URL + "/"},
                    {"key": "User-Agent", "value": CHROME_UA},
                ],
            })

    if not links:
        links.append({
            "id": make_id(ch_id, "lnk0"), "name": "Xem trực tiếp",
            "type": "iframe", "default": True, "url": detail_url,
            "request_headers": [
                {"key": "Referer",    "value": BASE_URL + "/"},
                {"key": "User-Agent", "value": CHROME_UA},
            ],
        })
    return links

def build_channel(m, streams, idx):
    ch_id  = make_id("gv", str(idx), slugify(m["base_title"])[:24])
    title  = build_title(m)
    league = m["league"]
    score  = m["score"]
    blv_names = m.get("blv_names", [])

    # ── Labels ───────────────────────────────────────────────
    labels = []

    # Trạng thái top-left
    st_cfg = {
        "live":     ("● LIVE",         "#C62828"),
        "upcoming": ("🕐 Sắp diễn ra", "#1565C0"),
        "finished": ("✅ Kết thúc",    "#424242"),
    }.get(m["status"], ("● LIVE", "#C62828"))
    labels.append({"text": st_cfg[0], "color": st_cfg[1],
                   "text_color": "#ffffff", "position": "top-left"})

    # Giải đấu top-right
    if league:
        labels.append({"text": league[:28], "color": "#0D47A1",
                       "text_color": "#ffffff", "position": "top-right"})

    # Tỉ số live + phút bottom-right
    if score and m["status"] == "live":
        lt  = m["live_time"]
        txt = f"{score}  {lt}'" if lt and lt not in ("0","") else score
        labels.append({"text": txt, "color": "#B71C1C",
                       "text_color": "#ffffff", "position": "bottom-right"})

    # BLV bottom-left
    if blv_names:
        blv_txt = (f"🎙 {blv_names[0]}" if len(blv_names) == 1
                   else f"🎙 {len(blv_names)} BLV")
        labels.append({"text": blv_txt, "color": "#1B5E20",
                       "text_color": "#ffffff", "position": "bottom-left"})

    # ── Image: 2 logo ────────────────────────────────────────
    img_obj = build_image_obj(m.get("logo_a", ""), m.get("logo_b", ""))

    # ── Streams ──────────────────────────────────────────────
    links = build_stream_links(ch_id, streams, m["detail_url"])

    # ── Description ──────────────────────────────────────────
    parts = []
    if league:          parts.append(league)
    if m["time_str"]:   parts.append(m["time_str"])
    if m["date_str"]:   parts.append(m["date_str"])
    if m["status"] == "live":
        lt = m["live_time"]
        parts.append(f"🔴 LIVE{' '+lt+chr(39) if lt and lt not in ('0','') else ''}")
        if score: parts.append(score)
    elif m["status"] == "upcoming":
        parts.append("🕐 Sắp diễn ra")
    if blv_names:
        parts.append("🎙 " + " | ".join(blv_names))

    return {
        "id":            ch_id,
        "name":          title,
        "description":   " | ".join(parts),
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
def build_iptv_json(channels, now_str):
    n_live = sum(1 for c in channels
                 if any("LIVE" in lb.get("text","") for lb in c.get("labels",[])))
    return {
        "id":          "giovang-iptv",
        "name":        "GioVang TV",
        "url":         BASE_URL + "/",
        "description": (f"Trực tiếp bóng đá — {n_live} đang live, "
                        f"{len(channels)-n_live} sắp tới. Cập nhật {now_str}"),
        "disable_ads": True,
        "color":       "#f5a623",
        "grid_number": 3,
        "image":       {"type": "cover", "url": DEFAULT_LOGO},
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
    ap = argparse.ArgumentParser(description="Crawler giovang.vin → IPTV JSON v5")
    ap.add_argument("--no-stream", action="store_true",
                    help="Không lấy stream (nhanh hơn, dùng để test)")
    ap.add_argument("--all",       action="store_true",
                    help="Lấy toàn bộ trận, không chỉ Hot Match")
    ap.add_argument("--output",    default=OUTPUT_FILE)
    args = ap.parse_args()

    log("\n" + "═"*62)
    log("  🏟  CRAWLER giovang.vin  v5.0")
    log("  📡  live.json → WP AJAX logo → keovip stream API")
    log("  🖼  Thumbnail: 2 logo đội bóng cạnh nhau (SVG)")
    log("═"*62 + "\n")

    now_str = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M ICT")
    sc = make_scraper()

    # Init session để lấy cookie (cần cho API stream)
    init_session(sc)

    # ── Bước 1: Danh sách trận ───────────────────────────────
    matches = fetch_matches(sc, only_hot=not args.all)
    if not matches:
        log("❌ Không có trận nào. Thêm --all để lấy toàn bộ.")
        sys.exit(1)

    # ── Bước 2 + 3: Logo + Stream mỗi trận ──────────────────
    log(f"\n🔄 Bước 2+3: Fetch logo + stream ({len(matches)} trận)...")
    channels = []

    for i, m in enumerate(matches, 1):
        log(f"\n  [{i:02d}/{len(matches):02d}] {m['base_title']}")
        log(f"        {m['status']:8s} | {m['league']:25s} | "
            f"{m['time_str']} {m['date_str']}")

        streams = []

        if not args.no_stream:
            try:
                # WP AJAX → logo chính xác từ flashscore
                log(f"        📋 WP AJAX logo...")
                home_wp, away_wp = fetch_wp_logos(sc, m["id"])
                logo_a = (home_wp or {}).get("logo", "")
                logo_b = (away_wp or {}).get("logo", "")
                if logo_a: m["logo_a"] = logo_a
                if logo_b: m["logo_b"] = logo_b
                if m.get("logo_a"): log(f"        logo_a ✓ {m['logo_a'][-40:]}")
                if m.get("logo_b"): log(f"        logo_b ✓ {m['logo_b'][-40:]}")
            except Exception as e:
                log(f"        ⚠ WP AJAX lỗi: {e}")

            try:
                # Stream API
                log(f"        🎬 Stream API...")
                streams = fetch_streams(sc, m["id"])
                if not streams:
                    log(f"        ⚠ Không có stream (trận chưa live hoặc API lỗi)")
            except Exception as e:
                log(f"        ⚠ Stream API lỗi: {e}")
                streams = []

            time.sleep(0.5)

        channels.append(build_channel(m, streams, i))

    # ── Bước 4: Ghi file ─────────────────────────────────────
    result = build_iptv_json(channels, now_str)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    live_n = sum(1 for m in matches if m["status"] == "live")
    up_n   = sum(1 for m in matches if m["status"] == "upcoming")
    try:
        streams_ok = sum(1 for c in channels
                         if c["sources"][0]["contents"][0]["streams"][0]["stream_links"][0]["type"] == "hls")
    except Exception:
        streams_ok = 0

    log(f"\n{'═'*62}")
    log(f"  ✅  Xong!  →  {args.output}")
    log(f"  📊  {len(channels)} trận | 🔴 {live_n} live | 🕐 {up_n} sắp tới")
    log(f"  🎬  {streams_ok}/{len(channels)} trận có stream HLS")
    log(f"  🕐  {now_str}")
    log("═"*62 + "\n")


if __name__ == "__main__":
    main()
