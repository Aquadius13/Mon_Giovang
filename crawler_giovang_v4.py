#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   CRAWLER giovang.vin  v6.0                                  ║
║                                                              ║
║   Luồng:                                                     ║
║   1. GET live.json + all.json  → danh sách trận Hot Match   ║
║   2. POST WP AJAX              → logo đội (flashscore/keo)  ║
║   3. GET /api/fixtures/{id}    → link_stream_hd / sd        ║
║   4. Tạo thumbnail WEBP base64 (2 logo + giải + giờ)        ║
║   5. Build IPTV JSON                                         ║
╚══════════════════════════════════════════════════════════════╝

Cài:  pip install cloudscraper requests pillow
Chạy:
    python crawler_giovang.py
    python crawler_giovang.py --no-stream
    python crawler_giovang.py --all
    python crawler_giovang.py --output out.json
"""

import argparse, base64, hashlib, io, json, re, sys, time
from datetime import datetime, timezone, timedelta

try:
    import cloudscraper
    import requests
    from PIL import Image, ImageDraw, ImageFont
    _PILLOW = True
except ImportError as e:
    print(f"Cài: pip install cloudscraper requests pillow  ({e})")
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

# ─── Fonts ────────────────────────────────────────────────────
_FONT_BOLD_PATH = next(
    (p for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ] if __import__("os").path.exists(p)),
    None
)
_FONT_REG_PATH = next(
    (p for p in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ] if __import__("os").path.exists(p)),
    None
)

def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path = _FONT_BOLD_PATH if bold else _FONT_REG_PATH
    try:
        return ImageFont.truetype(path, size) if path else ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()

def log(msg): print(msg, flush=True)

# ─── HTTP ─────────────────────────────────────────────────────
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

def init_session(sc):
    """Visit giovang.vin để lấy cookie session cần cho stream API."""
    try:
        r = sc.get(BASE_URL + "/", timeout=20)
        log(f"  🍪 Session: {r.status_code} | cookies: {list(sc.cookies.keys())}")
    except Exception as e:
        log(f"  ⚠ Init session: {e}")

def _get_json(url, sc, label="", params=None):
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

def _post_ajax(sc, payload):
    for i in range(3):
        try:
            r = sc.post(WP_AJAX, data=payload, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i < 2: time.sleep(2 ** i)
    return {}

def _download_logo(url: str, sc) -> "Image.Image | None":
    """Download logo, trả về PIL Image hoặc None."""
    if not url:
        return None
    try:
        r = sc.get(url, timeout=8, headers={
            "Accept": "image/webp,image/png,image/*,*/*",
            "Referer": BASE_URL + "/",
        })
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "html" in ct or "json" in ct or len(r.content) < 100:
            return None
        return Image.open(io.BytesIO(r.content)).convert("RGBA")
    except Exception:
        return None

# ─── Slug ─────────────────────────────────────────────────────
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

# ─── Parse time ───────────────────────────────────────────────
def parse_time(f):
    raw_t = f.get("time", "")
    dm    = f.get("day_month", "")
    raw_d = f.get("date", "")
    mt    = re.match(r"(\d{1,2}):(\d{2})", raw_t)
    time_str = f"{int(mt.group(1)):02d}:{mt.group(2)}" if mt else ""
    sort_key = ""
    if mt:
        p = re.split(r"[-/]", raw_d)
        if len(p) == 3:
            try:
                dd, mm, yy = int(p[0]), int(p[1]), int(p[2])
                hh, mn = int(mt.group(1)), int(mt.group(2))
                sort_key = f"{yy}{mm:02d}{dd:02d}{hh:02d}{mn:02d}"
            except Exception:
                pass
    return time_str, dm, sort_key

def get_status(sc):
    if sc in LIVE_CODES:     return "live"
    if sc in FINISHED_CODES: return "finished"
    return "upcoming"

# ─── Parse fixture ────────────────────────────────────────────
def parse_fixture(f):
    fid    = str(f.get("id", ""))
    home   = f.get("teams", {}).get("home", {}) or {}
    away   = f.get("teams", {}).get("away", {}) or {}
    league = f.get("league", {}) or {}
    goals  = f.get("goals", {}) or {}
    blvs   = f.get("blv", []) or []
    sc     = f.get("status_code", "NS")
    gh = goals.get("home")
    ga = goals.get("away")
    if gh is None:
        ft = ((f.get("score") or {}).get("fulltime") or {})
        gh, ga = ft.get("home"), ft.get("away")
    score = f"{gh}-{ga}" if gh is not None and ga is not None else ""
    time_str, date_str, sort_key = parse_time(f)
    home_name = home.get("name", "")
    away_name = away.get("name", "")
    return {
        "id":          fid,
        "base_title":  f"{home_name} vs {away_name}",
        "home_team":   home_name,
        "away_team":   away_name,
        "logo_a":      home.get("logo", ""),
        "logo_b":      away.get("logo", ""),
        "league":      league.get("title", ""),
        "score":       score,
        "status":      get_status(sc),
        "status_code": sc,
        "live_time":   str(f.get("live_time", "")),
        "time_str":    time_str,
        "date_str":    date_str,
        "sort_key":    sort_key,
        "detail_url":  build_detail_url(home_name, away_name,
                                         f.get("day_month", ""), fid),
        "blv_keys":    [b for b in blvs if b != "nha-dai"],
        "blv_names":   [BLV_MAP.get(b, b) for b in blvs if b != "nha-dai"],
        "is_hot":      bool(f.get("is_hot")),
        "is_hot_top":  bool(f.get("is_hot_top")),
        "sport_type":  f.get("type", "football"),
    }

# ─── Fetch matches ────────────────────────────────────────────
def fetch_matches(sc, only_hot):
    log("\n📡 Bước 1: Fetch live.json + all.json...")
    ts = int(time.time() * 1000)
    live_data = _get_json(API_LIVE, sc, "live.json", {"t": ts})
    all_data  = _get_json(API_ALL,  sc, "all.json",  {"t": ts})
    matches, seen = [], set()
    for f in (live_data.get("response") or []):
        if get_status(f.get("status_code","NS")) == "finished": continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")): continue
        m = parse_fixture(f)
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"]); matches.append(m)
            log(f"  🔴 [{m['status_code']:3s}] {m['base_title']} | {m['league']} | {m['time_str']}")
    for f in (all_data.get("response") or []):
        if f.get("status_code","NS") != "NS": continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")): continue
        m = parse_fixture(f)
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"]); matches.append(m)
            log(f"  🕐 [NS ] {m['base_title']} | {m['league']} | {m['time_str']}")
    prio = {"live":0,"upcoming":1,"finished":2}
    matches.sort(key=lambda x: (prio.get(x["status"],9), x["sort_key"]))
    log(f"\n  ✅ {len(matches)} trận — "
        f"🔴 {sum(1 for m in matches if m['status']=='live')} live | "
        f"🕐 {sum(1 for m in matches if m['status']=='upcoming')} sắp tới")
    return matches

# ─── WP AJAX → logo chính xác ────────────────────────────────
def fetch_wp_logos(sc, fid):
    try:
        resp = _post_ajax(sc, {"action": "load_live_stream", "id": fid})
        if not resp or not isinstance(resp, dict) or not resp.get("success"):
            return {}, {}
        d    = resp.get("data") or {}
        home = d.get("home") or {}
        away = d.get("away") or {}
        if not isinstance(home, dict): home = {}
        if not isinstance(away, dict): away = {}
        return home, away
    except Exception as e:
        log(f"     ⚠ WP AJAX: {e}")
        return {}, {}

# ─── Stream API ───────────────────────────────────────────────
def fetch_streams(sc, fid):
    """
    GET /api/fixtures/{fid}
    → { code:0, response:{ blv:[{blv_key, blv_name, link_stream_hd, link_stream_sd}] } }
    """
    url = API_STREAM.format(fid=fid)
    for attempt in range(3):
        try:
            r = sc.get(url, timeout=15, headers={
                "Accept":   "application/json, */*",
                "Referer":  BASE_URL + "/",
                "Origin":   BASE_URL,
            })
            if r.status_code != 200 or not r.content:
                time.sleep(1); continue
            data     = r.json()
            if data.get("code", -1) != 0:
                log(f"     ⚠ Stream API code={data.get('code')}")
                return []
            resp     = data.get("response") or {}
            blv_list = resp.get("blv") or []
            if not blv_list:
                return []
            result = []
            for blv in blv_list:
                key  = blv.get("blv_key", "")
                name = blv.get("blv_name") or BLV_MAP.get(key, key)
                hd   = blv.get("link_stream_hd", "")
                sd   = blv.get("link_stream_sd", "")
                if hd or sd:
                    result.append({"blv_key":key,"blv_name":name,"url_hd":hd,"url_sd":sd})
                    log(f"     🎙 {name}: {(hd or sd)[-45:]}")
            return result
        except Exception as e:
            log(f"     ⚠ attempt {attempt+1}: {e}")
            if attempt < 2: time.sleep(2)
    return []

# ─── Thumbnail WEBP base64 ────────────────────────────────────
def make_thumbnail(home_name: str, away_name: str,
                   logo_a: "Image|None", logo_b: "Image|None",
                   time_str: str, date_str: str,
                   league: str, status: str = "upcoming",
                   score: str = "", live_time: str = "") -> str:
    """
    Tạo thumbnail 800×450 WEBP base64.
    Gồm: 2 logo đội + hộp giữa (giờ/tỉ số) + tên đội + thanh dưới (giải + ngày giờ).
    """
    W, H    = 800, 450
    LOGO_SZ = 140
    LOGO_Y  = 170
    BAR_Y   = 350

    # ── Nền gradient ─────────────────────────────────────────
    img  = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)],
                  fill=(int(8+5*t), int(16+10*t), int(36+16*t)))

    # Vạch sân mờ
    draw.line([(W//2, 60), (W//2, BAR_Y-20)],
              fill=(255,255,255,10), width=1)

    # ── Paste logo ────────────────────────────────────────────
    def paste_logo(cx, logo_img, name):
        nonlocal img, draw
        if logo_img:
            logo_img = logo_img.convert("RGBA")
            logo_img.thumbnail((LOGO_SZ, LOGO_SZ), Image.LANCZOS)
            canvas = Image.new("RGBA", (LOGO_SZ, LOGO_SZ), (0,0,0,0))
            ox = (LOGO_SZ - logo_img.width)  // 2
            oy = (LOGO_SZ - logo_img.height) // 2
            canvas.paste(logo_img, (ox, oy), logo_img)
            base = img.convert("RGBA")
            base.paste(canvas, (cx - LOGO_SZ//2, LOGO_Y - LOGO_SZ//2), canvas)
            img  = base.convert("RGB")
            draw = ImageDraw.Draw(img)
        else:
            init = "".join(w[0].upper() for w in name.split()[:2]) or "?"
            draw.ellipse([(cx-50, LOGO_Y-50),(cx+50, LOGO_Y+50)],
                         fill=(18, 45, 95))
            draw.text((cx, LOGO_Y), init, fill=(190,215,255),
                      font=_font(36), anchor="mm")

    paste_logo(110,   logo_a, home_name)
    paste_logo(W-110, logo_b, away_name)

    # ── Hộp giữa ─────────────────────────────────────────────
    cx = W // 2
    if status == "live" and score:
        box_fill   = (160, 20, 20)
        main_text  = score.replace("-", " : ")
        main_size  = 28
        sub_text   = f"● {live_time}'" if live_time and live_time not in ("","0") else "● LIVE"
        sub_color  = (255, 130, 130)
    elif status == "live":
        box_fill   = (160, 20, 20)
        main_text  = "LIVE"
        main_size  = 30
        sub_text   = f"● {live_time}'" if live_time and live_time not in ("","0") else ""
        sub_color  = (255, 130, 130)
    else:
        box_fill   = (14, 32, 78)
        main_text  = time_str or "VS"
        main_size  = 28 if time_str else 34
        sub_text   = "VS" if time_str else ""
        sub_color  = (140, 165, 225)

    bw, bh = 118, 76
    draw.rounded_rectangle(
        [(cx-bw//2, LOGO_Y-bh//2), (cx+bw//2, LOGO_Y+bh//2)],
        radius=18, fill=box_fill
    )
    draw.text((cx, LOGO_Y - (10 if sub_text else 0)),
              main_text, fill=(255,255,255),
              font=_font(main_size), anchor="mm")
    if sub_text:
        draw.text((cx, LOGO_Y + 22), sub_text,
                  fill=sub_color, font=_font(13, False), anchor="mm")

    # ── Tên đội ───────────────────────────────────────────────
    NAME_Y = LOGO_Y + LOGO_SZ//2 + 22

    def draw_name(cx, name):
        words = name.split()
        if len(name) <= 13 or len(words) <= 1:
            draw.text((cx, NAME_Y), name[:15], fill=(220,232,255),
                      font=_font(17), anchor="mm")
        else:
            mid = max(1, len(words)//2)
            draw.text((cx, NAME_Y-10), " ".join(words[:mid])[:15],
                      fill=(220,232,255), font=_font(15), anchor="mm")
            draw.text((cx, NAME_Y+10), " ".join(words[mid:])[:15],
                      fill=(195,212,245), font=_font(13, False), anchor="mm")

    draw_name(110,   home_name)
    draw_name(W-110, away_name)

    # ── Thanh dưới: Giải đấu + Ngày giờ ─────────────────────
    bar_img = Image.new("RGB", (W, H-BAR_Y), (5, 12, 26))
    img.paste(bar_img, (0, BAR_Y))
    draw = ImageDraw.Draw(img)
    draw.line([(0, BAR_Y), (W, BAR_Y)], fill=(255,165,0), width=2)

    mid_y = BAR_Y + (H - BAR_Y) // 2

    # Tên giải (trái)
    sport_icon = {"basketball":"🏀","tennis":"🎾","esports":"🎮",
                  "volleyball":"🏐"}.get(
        (league or "").lower()[:4] if False else "", "⚽")
    league_disp = f"{sport_icon}  {league[:30]}" if league else ""
    if league_disp:
        draw.text((22, mid_y), league_disp, fill=(255,200,65),
                  font=_font(16), anchor="lm")

    # Ngày + Giờ (phải)
    if time_str and date_str:
        dt = f"{time_str}   {date_str}"
    elif time_str:
        dt = time_str
    elif date_str:
        dt = date_str
    else:
        dt = ""
    if dt:
        draw.text((W-22, mid_y), dt, fill=(175,205,255),
                  font=_font(16), anchor="rm")

    # ── Xuất WEBP base64 ─────────────────────────────────────
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=80, method=4)
    b64 = base64.b64encode(out.getvalue()).decode()
    return f"data:image/webp;base64,{b64}"

# ─── Build channel ────────────────────────────────────────────
def make_id(*parts):
    raw    = "-".join(str(p) for p in parts if p)
    slug   = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    digest = hashlib.md5(raw.encode()).hexdigest()[:8]
    return slug[:48] + "-" + digest if len(slug) > 56 else slug

def build_title(m):
    base, score = m["base_title"], m["score"]
    t, d = m["time_str"], m["date_str"]
    if m["status"] == "live":
        lt  = m["live_time"]
        sfx = f" {lt}'" if lt and lt not in ("","0") else ""
        return (f"{m['home_team']} {score} {m['away_team']}  🔴{sfx}"
                if score else f"{base}  🔴 LIVE{sfx}")
    if m["status"] == "finished":
        return (f"{m['home_team']} {score} {m['away_team']}  ✅"
                if score else f"{base}  ✅ KT")
    ti = (f"  🕐 {t} | {d}" if t and d else
          f"  🕐 {t}"       if t else
          f"  📅 {d}"       if d else "")
    return f"{base}{ti}"

def build_stream_links(ch_id, streams):
    """
    Tạo stream links:
    - 1 BLV  → "HD", "SD"
    - 2+ BLV → "HD" (nguồn 1 mặc định), "SD", "HD 2", "SD 2", ...
    """
    links = []
    for i, s in enumerate(streams):
        # Tên link: chỉ "HD" hoặc "SD", không kèm tên BLV
        suffix = f" {i+1}" if len(streams) > 1 and i > 0 else ""
        for quality, url in [("HD", s.get("url_hd","")), ("SD", s.get("url_sd",""))]:
            if not url:
                continue
            # HD của BLV đầu = default
            is_default = (len(links) == 0)
            link_name  = f"{quality}{suffix}"
            links.append({
                "id":      make_id(ch_id, f"l{i}{quality}"),
                "name":    link_name,
                "type":    "hls",
                "default": is_default,
                "url":     url,
                "request_headers": [
                    {"key": "Referer",    "value": BASE_URL + "/"},
                    {"key": "User-Agent", "value": CHROME_UA},
                ],
            })
    return links

def build_channel(m, streams, thumb_uri, idx):
    ch_id  = make_id("gv", str(idx), slugify(m["base_title"])[:24])
    title  = build_title(m)
    league = m["league"]
    score  = m["score"]
    blv_names = m.get("blv_names", [])
    multi_blv = len(streams) > 1     # 2+ BLV

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

    # Tỉ số live bottom-right (bỏ label giải đấu)
    if score and m["status"] == "live":
        lt  = m["live_time"]
        txt = f"{score}  {lt}'" if lt and lt not in ("","0") else score
        labels.append({"text": txt, "color": "#B71C1C",
                       "text_color": "#ffffff", "position": "bottom-right"})

    # BLV bottom-left
    if blv_names:
        btxt = (f"🎙 {blv_names[0]}" if len(blv_names) == 1
                else f"🎙 {len(blv_names)} BLV")
        labels.append({"text": btxt, "color": "#1B5E20",
                       "text_color": "#ffffff", "position": "bottom-left"})

    # ── Stream links ─────────────────────────────────────────
    links = build_stream_links(ch_id, streams)

    if not links:
        links.append({
            "id": make_id(ch_id,"lnk0"), "name": "Xem trực tiếp",
            "type": "iframe", "default": True, "url": m["detail_url"],
            "request_headers": [
                {"key": "Referer",    "value": BASE_URL + "/"},
                {"key": "User-Agent", "value": CHROME_UA},
            ],
        })

    # ── Image object ─────────────────────────────────────────
    img_obj = {
        "padding":          0,
        "background_color": "#0a1628",
        "display":          "contain",
        "url":              thumb_uri,
        "width":            800,
        "height":           450,
    }

    # ── Description ──────────────────────────────────────────
    parts = []
    if league:          parts.append(league)
    if m["time_str"]:   parts.append(m["time_str"])
    if m["date_str"]:   parts.append(m["date_str"])
    if m["status"] == "live":
        lt = m["live_time"]
        parts.append(f"🔴 LIVE{' '+lt+chr(39) if lt and lt not in ('','0') else ''}")
        if score: parts.append(score)
    elif m["status"] == "upcoming":
        parts.append("🕐 Sắp diễn ra")
    if blv_names:
        parts.append("🎙 " + " | ".join(blv_names))

    # ── Channel type: multi-BLV → single + enable_detail True ─
    # Theo yêu cầu: 2+ BLV → type=single, enable_detail=true, default=HD
    ch_type     = "single"
    enable_det  = multi_blv        # True nếu có 2+ BLV

    return {
        "id":            ch_id,
        "name":          title,
        "description":   " | ".join(parts),
        "type":          ch_type,
        "display":       "thumbnail-only",
        "enable_detail": enable_det,
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

# ─── Main ─────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-stream", action="store_true")
    ap.add_argument("--all",       action="store_true")
    ap.add_argument("--output",    default=OUTPUT_FILE)
    args = ap.parse_args()

    log("\n" + "═"*62)
    log("  🏟  CRAWLER giovang.vin  v6.0")
    log("  📡  live/all.json → WP AJAX → stream API → WEBP thumb")
    log("═"*62 + "\n")

    now_str = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M ICT")
    sc = make_scraper()
    init_session(sc)

    # Bước 1: Danh sách trận
    matches = fetch_matches(sc, only_hot=not args.all)
    if not matches:
        log("❌ Không có trận nào."); sys.exit(1)

    # Bước 2+3+4: Mỗi trận
    log(f"\n🔄 Bước 2-4: Logo + Stream + Thumbnail ({len(matches)} trận)...")
    channels = []

    for i, m in enumerate(matches, 1):
        log(f"\n  [{i:02d}/{len(matches):02d}] {m['base_title']}")
        log(f"        {m['status']:8s} | {m['league']:25s} | {m['time_str']} {m['date_str']}")

        streams   = []
        logo_a_img = None
        logo_b_img = None

        if not args.no_stream:
            # WP AJAX → logo chính xác
            try:
                log("        📋 WP AJAX logo...")
                home_wp, away_wp = fetch_wp_logos(sc, m["id"])
                if (home_wp or {}).get("logo"): m["logo_a"] = home_wp["logo"]
                if (away_wp or {}).get("logo"): m["logo_b"] = away_wp["logo"]
                if m.get("logo_a"): log(f"        logo_a ✓ {m['logo_a'][-45:]}")
                if m.get("logo_b"): log(f"        logo_b ✓ {m['logo_b'][-45:]}")
            except Exception as e:
                log(f"        ⚠ WP AJAX: {e}")

            # Download logo cho thumbnail
            try:
                if m.get("logo_a"): logo_a_img = _download_logo(m["logo_a"], sc)
                if m.get("logo_b"): logo_b_img = _download_logo(m["logo_b"], sc)
                log(f"        logo dl: A={'✓' if logo_a_img else '✗'} B={'✓' if logo_b_img else '✗'}")
            except Exception as e:
                log(f"        ⚠ Logo download: {e}")

            # Stream API
            try:
                log("        🎬 Stream API...")
                streams = fetch_streams(sc, m["id"])
                if not streams:
                    log("        ⚠ Không có stream")
            except Exception as e:
                log(f"        ⚠ Stream API: {e}")
                streams = []

            time.sleep(0.4)

        # Thumbnail WEBP
        try:
            log("        🖼  Tạo thumbnail WEBP...")
            thumb_uri = make_thumbnail(
                home_name  = m["home_team"],
                away_name  = m["away_team"],
                logo_a     = logo_a_img,
                logo_b     = logo_b_img,
                time_str   = m["time_str"],
                date_str   = m["date_str"],
                league     = m["league"],
                status     = m["status"],
                score      = m["score"],
                live_time  = m["live_time"],
            )
            log(f"        ✓ {len(thumb_uri):,} chars")
        except Exception as e:
            log(f"        ⚠ Thumbnail: {e}")
            thumb_uri = DEFAULT_LOGO

        channels.append(build_channel(m, streams, thumb_uri, i))

    # Ghi file
    result = build_iptv_json(channels, now_str)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    live_n = sum(1 for m in matches if m["status"] == "live")
    up_n   = sum(1 for m in matches if m["status"] == "upcoming")
    try:
        hls_n = sum(1 for c in channels
                    if c["sources"][0]["contents"][0]["streams"][0]
                    ["stream_links"][0]["type"] == "hls")
    except Exception:
        hls_n = 0

    log(f"\n{'═'*62}")
    log(f"  ✅  Xong!  →  {args.output}")
    log(f"  📊  {len(channels)} trận | 🔴 {live_n} live | 🕐 {up_n} sắp tới | 🎬 {hls_n} có HLS")
    log(f"  🕐  {now_str}")
    log("═"*62 + "\n")


if __name__ == "__main__":
    main()
