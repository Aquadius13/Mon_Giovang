#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   CRAWLER giovang.vin  v8.0                                  ║
║                                                              ║
║   Luồng:                                                     ║
║   1. GET live.json + all.json  → danh sách trận Hot Match   ║
║   2. POST WP AJAX              → logo đội (flashscore)      ║
║   3. GET /api/fixtures/{id}    → link_stream_hd / sd        ║
║   4. Tạo thumbnail WEBP base64 (nền sáng)                   ║
║   5. Build IPTV JSON:                                        ║
║      1 source → 1 content → N streams (N = số BLV)         ║
║      Mỗi stream tên = tên BLV, có HD + SD riêng             ║
╚══════════════════════════════════════════════════════════════╝

Cài:  pip install cloudscraper requests pillow
Chạy:
    python crawler_giovang.py
    python crawler_giovang.py --no-stream
    python crawler_giovang.py --all
    python crawler_giovang.py --output out.json
"""

import argparse, base64, hashlib, io, json, os, re, sys, time
from datetime import datetime, timezone, timedelta

try:
    import cloudscraper
    import requests
    from PIL import Image, ImageDraw, ImageFont
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

# Icon chính thức giovang.vin (từ <link rel="icon"> trong HTML)
SITE_ICON = "https://giovang.vin/wp-content/uploads/2025/04/cropped-favicon-giovang-192x192.png"

SITE_DESC = (
    "Giovang TV là nền tảng phát trực tiếp bóng đá số 1 Việt Nam hiện nay, "
    "chuyên phát sóng trực tiếp các giải đấu từ quốc nội cho đến quốc tế như "
    "Ngoại hạng Anh, La Liga, Serie A, Bundesliga, Champions League và "
    "nhiều sự kiện thể thao khác."
)

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
_FB = next((p for p in [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/crosextra/Carlito-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
] if os.path.exists(p)), None)

_FR = next((p for p in [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/crosextra/Carlito-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
] if os.path.exists(p)), None)

def _f(size: int, bold: bool = True):
    p = _FB if bold else _FR
    try:
        return ImageFont.truetype(p, size) if p else ImageFont.load_default()
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
        "Accept-Language": "vi-VN,vi;q=0.9",
        "Referer":         BASE_URL + "/",
    })
    return sc

def init_session(sc):
    try:
        r = sc.get(BASE_URL + "/", timeout=20)
        log(f"  🍪 Session: {r.status_code} | cookies={list(sc.cookies.keys())}")
    except Exception as e:
        log(f"  ⚠ Session: {e}")

def _get(url, sc, label="", params=None) -> dict:
    for i in range(3):
        try:
            r = sc.get(url, timeout=20, params=params)
            r.raise_for_status()
            data = r.json()
            n = len(data.get("response", [])) if isinstance(data, dict) else "?"
            log(f"  ✓ {label}  →  {n} items")
            return data
        except Exception as e:
            if i < 2: time.sleep(2**i)
    return {}

def _post(sc, payload) -> dict:
    for i in range(3):
        try:
            r = sc.post(WP_AJAX, data=payload, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i < 2: time.sleep(2**i)
    return {}

def _dl_logo(url: str, sc) -> "Image.Image | None":
    if not url: return None
    try:
        r = sc.get(url, timeout=8, headers={
            "Accept":  "image/webp,image/png,image/*,*/*",
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

def slugify(s: str) -> str:
    s = s.strip().lower()
    for f, t in zip(_FROM, _TO): s = s.replace(f, t)
    s = re.sub(r"[^a-z0-9 -]", "", s)
    s = re.sub(r"\s+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")

def build_detail_url(home, away, day_month, fid):
    return f"{BASE_URL}/{slugify(f'truc tiep {home} vs {away}-{day_month}--{fid}')}/"

# ─── Parse ────────────────────────────────────────────────────
def parse_time(f: dict) -> tuple[str, str, str]:
    raw_t = f.get("time", "")
    dm    = f.get("day_month", "")
    raw_d = f.get("date", "")
    mt    = re.match(r"(\d{1,2}):(\d{2})", raw_t)
    ts    = f"{int(mt.group(1)):02d}:{mt.group(2)}" if mt else ""
    sk    = ""
    if mt:
        p = re.split(r"[-/]", raw_d)
        if len(p) == 3:
            try:
                dd, mm, yy = int(p[0]), int(p[1]), int(p[2])
                sk = f"{yy}{mm:02d}{dd:02d}{int(mt.group(1)):02d}{mt.group(2)}"
            except Exception: pass
    return ts, dm, sk

def get_status(sc: str) -> str:
    if sc in LIVE_CODES:     return "live"
    if sc in FINISHED_CODES: return "finished"
    return "upcoming"

def parse_fixture(f: dict) -> dict:
    fid    = str(f.get("id", ""))
    home   = (f.get("teams") or {}).get("home") or {}
    away   = (f.get("teams") or {}).get("away") or {}
    league = f.get("league") or {}
    goals  = f.get("goals")  or {}
    blvs   = f.get("blv")    or []
    sc     = f.get("status_code", "NS")
    gh, ga = goals.get("home"), goals.get("away")
    if gh is None:
        ft = ((f.get("score") or {}).get("fulltime") or {})
        gh, ga = ft.get("home"), ft.get("away")
    score  = f"{gh}-{ga}" if gh is not None and ga is not None else ""
    ts, ds, sk = parse_time(f)
    hn = home.get("name", ""); an = away.get("name", "")
    return {
        "id": fid, "base_title": f"{hn} vs {an}",
        "home_team": hn, "away_team": an,
        "logo_a": home.get("logo",""), "logo_b": away.get("logo",""),
        "league": league.get("title","") or "",
        "score": score, "status": get_status(sc), "status_code": sc,
        "live_time": str(f.get("live_time","")),
        "time_str": ts, "date_str": ds, "sort_key": sk,
        "detail_url": build_detail_url(hn, an, f.get("day_month",""), fid),
        "blv_keys":  [b for b in blvs if b != "nha-dai"],
        "blv_names": [BLV_MAP.get(b, b) for b in blvs if b != "nha-dai"],
        "is_hot": bool(f.get("is_hot")), "is_hot_top": bool(f.get("is_hot_top")),
        "sport_type": f.get("type","football"),
    }

# ─── Fetch matches ────────────────────────────────────────────
def fetch_matches(sc, only_hot: bool) -> list[dict]:
    log("\n📡 Bước 1: Fetch live.json + all.json...")
    ts = int(time.time() * 1000)
    live = _get(API_LIVE, sc, "live.json", {"t": ts})
    all_ = _get(API_ALL,  sc, "all.json",  {"t": ts})
    out, seen = [], set()
    for f in (live.get("response") or []):
        if get_status(f.get("status_code","")) == "finished": continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")): continue
        m = parse_fixture(f)
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"]); out.append(m)
            log(f"  🔴 [{m['status_code']:3s}] {m['base_title']} | {m['league']} | {m['time_str']}")
    for f in (all_.get("response") or []):
        if f.get("status_code","NS") != "NS": continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")): continue
        m = parse_fixture(f)
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"]); out.append(m)
            log(f"  🕐 [NS ] {m['base_title']} | {m['league']} | {m['time_str']}")
    prio = {"live":0,"upcoming":1,"finished":2}
    out.sort(key=lambda x: (prio.get(x["status"],9), x["sort_key"]))
    log(f"\n  ✅ {len(out)} trận — 🔴{sum(1 for m in out if m['status']=='live')} live "
        f"| 🕐{sum(1 for m in out if m['status']=='upcoming')} sắp tới")
    return out

# ─── WP AJAX logo ─────────────────────────────────────────────
def fetch_wp_logos(sc, fid: str) -> tuple[dict, dict]:
    try:
        resp = _post(sc, {"action": "load_live_stream", "id": fid})
        if not resp or not isinstance(resp, dict) or not resp.get("success"):
            return {}, {}
        d = resp.get("data") or {}
        home = d.get("home") or {}
        away = d.get("away") or {}
        return (home if isinstance(home, dict) else {}), (away if isinstance(away, dict) else {})
    except Exception as e:
        log(f"     ⚠ WP AJAX: {e}"); return {}, {}

# ─── Stream API ───────────────────────────────────────────────
def fetch_streams(sc, fid: str) -> list[dict]:
    """
    GET /api/fixtures/{fid}
    → { code:0, response:{ blv:[{blv_key,blv_name,link_stream_hd,link_stream_sd}] } }
    """
    url = API_STREAM.format(fid=fid)
    for attempt in range(3):
        try:
            r = sc.get(url, timeout=15, headers={
                "Accept":  "application/json, */*",
                "Referer": BASE_URL + "/",
                "Origin":  BASE_URL,
            })
            if r.status_code != 200 or not r.content:
                time.sleep(1); continue
            data     = r.json()
            if data.get("code", -1) != 0: return []
            blv_list = (data.get("response") or {}).get("blv") or []
            result   = []
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
            log(f"     ⚠ stream {attempt+1}: {e}")
            if attempt < 2: time.sleep(2)
    return []

# ─── Thumbnail WEBP ───────────────────────────────────────────
def make_thumbnail(home_name: str, away_name: str,
                   logo_a: "Image|None", logo_b: "Image|None",
                   time_str: str, date_str: str, league: str,
                   status: str = "upcoming", score: str = "",
                   live_time: str = "") -> str:
    """
    800×450 WEBP, nền sáng trắng → xanh nhạt.
    Layout:
      Y=110 : Tên giải đấu
      Y=205 : Logo trái & Logo phải (cùng mức Giờ/VS ở giữa)
      Y=288 : Tên đội (dưới logo)
      Y=316 : Ngày thi đấu (căn giữa)
    Scale +10% so với v6: LOGO_SZ=130, font size tăng.
    Logo sát vào vùng info (gap=12px), không đè.
    """
    W, H     = 800, 450
    LOGO_SZ  = 130           # +10% từ ~118
    LOGO_CY  = 205           # center Y logo & giờ/VS
    NAME_Y   = LOGO_CY + LOGO_SZ//2 + 18   # 205+65+18 = 288
    DATE_Y   = NAME_Y + 28                  # 316
    LEAGUE_Y = 110
    MID_X    = W // 2
    INFO_HALF = 100          # vùng info giữa: 200px (300→500)
    GAP       = 12           # khoảng cách logo↔vùng info
    LX = MID_X - INFO_HALF - GAP - LOGO_SZ//2   # 400-100-12-65 = 223
    RX = MID_X + INFO_HALF + GAP + LOGO_SZ//2   # 400+100+12+65 = 577

    # ── Nền sáng: trắng → xanh nhạt ──────────────────────────
    img  = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)], fill=(
            int(252 - 8*t),
            int(254 - 6*t),
            255
        ))

    # Dải cam giovang top/bottom
    draw.rectangle([(0, 0),   (W, 8)], fill=(255, 140, 0))
    draw.rectangle([(0, H-8), (W, H)], fill=(255, 140, 0))

    # ── Tên giải đấu ─────────────────────────────────────────
    if league:
        draw.text((MID_X, LEAGUE_Y), league[:26],
                  fill=(55, 80, 160), font=_f(17, False), anchor="mm")
        draw.line([(MID_X-95, LEAGUE_Y+14), (MID_X+95, LEAGUE_Y+14)],
                  fill=(195, 210, 235), width=1)

    # ── Giờ / Tỉ số (trung tâm, cùng mức logo) ───────────────
    if status == "live" and score:
        main_txt, main_col, main_sz = score.replace("-"," : "), (190,20,20), 36
        sub_txt = f"● {live_time}'" if live_time and live_time not in ("","0") else "● LIVE"
        sub_col = (190, 20, 20)
    elif status == "live":
        main_txt, main_col, main_sz = "LIVE", (190,20,20), 36
        sub_txt = f"● {live_time}'" if live_time and live_time not in ("","0") else "●"
        sub_col = (190, 20, 20)
    else:
        main_txt  = time_str or "VS"
        main_col  = (20, 45, 130)
        main_sz   = 36
        sub_txt   = "VS" if time_str else ""
        sub_col   = (90, 115, 195)

    has_sub = bool(sub_txt)
    draw.text((MID_X, LOGO_CY - (13 if has_sub else 0)),
              main_txt, fill=main_col, font=_f(main_sz), anchor="mm")
    if has_sub:
        draw.text((MID_X, LOGO_CY + 24), sub_txt,
                  fill=sub_col, font=_f(14, False), anchor="mm")

    # ── Logo ─────────────────────────────────────────────────
    def paste_logo(cx, cy, logo_img, name, circle_col):
        nonlocal img, draw
        if logo_img:
            logo_img = logo_img.convert("RGBA")
            logo_img.thumbnail((LOGO_SZ, LOGO_SZ), Image.LANCZOS)
            canvas = Image.new("RGBA", (LOGO_SZ, LOGO_SZ), (0,0,0,0))
            ox = (LOGO_SZ - logo_img.width)  // 2
            oy = (LOGO_SZ - logo_img.height) // 2
            canvas.paste(logo_img, (ox, oy), logo_img)
            base = img.convert("RGBA")
            base.paste(canvas, (cx-LOGO_SZ//2, cy-LOGO_SZ//2), canvas)
            img  = base.convert("RGB")
            draw = ImageDraw.Draw(img)
        else:
            r2 = LOGO_SZ//2 - 4
            # Shadow
            draw.ellipse([(cx-r2+3, cy-r2+3), (cx+r2+3, cy+r2+3)], fill=(185,195,220))
            draw.ellipse([(cx-r2,   cy-r2),   (cx+r2,   cy+r2)],   fill=circle_col)
            init = "".join(w[0].upper() for w in name.split()[:2]) or "?"
            draw.text((cx, cy), init, fill="white", font=_f(34), anchor="mm")

    paste_logo(LX, LOGO_CY, logo_a, home_name, (25, 70, 175))
    paste_logo(RX, LOGO_CY, logo_b, away_name, (175, 30, 55))

    # ── Tên đội ───────────────────────────────────────────────
    def draw_name(cx, name):
        words = name.split()
        col   = (25, 50, 125)
        if len(name) <= 12 or len(words) <= 1:
            draw.text((cx, NAME_Y), name[:16], fill=col, font=_f(17), anchor="mm")
        else:
            mid = max(1, len(words)//2)
            draw.text((cx, NAME_Y-9), " ".join(words[:mid])[:16], fill=col, font=_f(15), anchor="mm")
            draw.text((cx, NAME_Y+9), " ".join(words[mid:])[:16], fill=col, font=_f(13,False), anchor="mm")

    draw_name(LX, home_name)
    draw_name(RX, away_name)

    # ── Ngày thi đấu (căn giữa, dưới tên đội) ─────────────────
    if date_str:
        draw.text((MID_X, DATE_Y), f"📅  {date_str}",
                  fill=(75, 100, 170), font=_f(14, False), anchor="mm")

    # Watermark
    draw.text((W-12, H-14), "giovang.vin",
              fill=(155, 170, 205), font=_f(10, False), anchor="rm")

    # ── Xuất WEBP base64 ─────────────────────────────────────
    out = io.BytesIO()
    img.save(out, format="WEBP", quality=83, method=4)
    return f"data:image/webp;base64,{base64.b64encode(out.getvalue()).decode()}"

# ─── Build channel ────────────────────────────────────────────
def make_id(*parts) -> str:
    raw    = "-".join(str(p) for p in parts if p)
    slug   = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    digest = hashlib.md5(raw.encode()).hexdigest()[:8]
    return slug[:48] + "-" + digest if len(slug) > 56 else slug

def build_title(m: dict) -> str:
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
    ti = (f"  🕐 {t} | {d}" if t and d else f"  🕐 {t}" if t else f"  📅 {d}" if d else "")
    return f"{base}{ti}"

def build_streams_list(ch_id: str, streams: list[dict]) -> list[dict]:
    """
    Tạo danh sách stream objects:
    Mỗi stream = 1 BLV, name = tên BLV, có stream_links HD + SD.
    """
    result = []
    for i, s in enumerate(streams):
        blv_name = s["blv_name"]
        links    = []

        if s.get("url_hd"):
            links.append({
                "id":      make_id(ch_id, f"l{i}hd"),
                "name":    "HD",
                "type":    "hls",
                "default": True,
                "url":     s["url_hd"],
                "request_headers": [
                    {"key": "Referer",    "value": BASE_URL + "/"},
                    {"key": "User-Agent", "value": CHROME_UA},
                ],
            })
        if s.get("url_sd"):
            links.append({
                "id":      make_id(ch_id, f"l{i}sd"),
                "name":    "SD",
                "type":    "hls",
                "default": not bool(s.get("url_hd")),
                "url":     s["url_sd"],
                "request_headers": [
                    {"key": "Referer",    "value": BASE_URL + "/"},
                    {"key": "User-Agent", "value": CHROME_UA},
                ],
            })

        if links:
            result.append({
                "id":           make_id(ch_id, f"st{i}"),
                "name":         blv_name,    # tên stream = tên BLV
                "stream_links": links,
            })

    return result

def build_sources(ch_id: str, streams: list[dict], detail_url: str) -> list[dict]:
    """
    Cấu trúc:
    1 source → 1 content → N streams (N = số BLV)
    Mỗi stream: name=tên BLV, stream_links=[HD, SD]
    """
    stream_list = build_streams_list(ch_id, streams) if streams else []

    if not stream_list:
        # Fallback iframe
        stream_list = [{
            "id":   make_id(ch_id, "st0"),
            "name": "Trực tiếp",
            "stream_links": [{
                "id":      make_id(ch_id, "l0"),
                "name":    "Xem trực tiếp",
                "type":    "iframe",
                "default": True,
                "url":     detail_url,
                "request_headers": [
                    {"key": "Referer",    "value": BASE_URL + "/"},
                    {"key": "User-Agent", "value": CHROME_UA},
                ],
            }],
        }]

    return [{
        "id":   make_id(ch_id, "src0"),
        "name": "GioVang Live",
        "contents": [{
            "id":      make_id(ch_id, "ct0"),
            "name":    "Trực tiếp",
            "streams": stream_list,
        }],
    }]

def build_channel(m: dict, streams: list[dict], thumb_uri: str, idx: int) -> dict:
    ch_id     = make_id("gv", str(idx), slugify(m["base_title"])[:24])
    title     = build_title(m)
    league    = m["league"]
    score     = m["score"]
    blv_names = m.get("blv_names", [])
    multi_blv = len(streams) > 1

    # ── Labels ───────────────────────────────────────────────
    labels = []
    st_map = {
        "live":     ("● LIVE",         "#C62828"),
        "upcoming": ("🕐 Sắp diễn ra", "#1565C0"),
        "finished": ("✅ Kết thúc",    "#424242"),
    }
    st_t, st_c = st_map.get(m["status"], ("● LIVE","#C62828"))
    labels.append({"text":st_t,"color":st_c,"text_color":"#ffffff","position":"top-left"})

    if score and m["status"] == "live":
        lt  = m["live_time"]
        txt = f"{score}  {lt}'" if lt and lt not in ("","0") else score
        labels.append({"text":txt,"color":"#B71C1C","text_color":"#ffffff","position":"bottom-right"})

    if blv_names:
        btxt = f"🎙 {blv_names[0]}" if len(blv_names)==1 else f"🎙 {len(blv_names)} BLV"
        labels.append({"text":btxt,"color":"#1B5E20","text_color":"#ffffff","position":"bottom-left"})

    # ── Image ─────────────────────────────────────────────────
    img_obj = {
        "padding":          0,
        "background_color": "#F5F8FF",
        "display":          "contain",
        "url":              thumb_uri,
        "width":            800,
        "height":           450,
    }

    # ── Sources ───────────────────────────────────────────────
    sources = build_sources(ch_id, streams, m["detail_url"])

    # ── Description ──────────────────────────────────────────
    parts = []
    if league:        parts.append(league)
    if m["time_str"]: parts.append(m["time_str"])
    if m["date_str"]: parts.append(m["date_str"])
    if m["status"] == "live":
        lt = m["live_time"]
        parts.append(f"🔴 LIVE{' '+lt+chr(39) if lt and lt not in ('','0') else ''}")
        if score: parts.append(score)
    elif m["status"] == "upcoming":
        parts.append("🕐 Sắp diễn ra")
    if blv_names: parts.append("🎙 " + " | ".join(blv_names))

    return {
        "id":            ch_id,
        "name":          title,
        "description":   " | ".join(parts),
        "type":          "single",
        "display":       "thumbnail-only",
        "enable_detail": multi_blv,
        "image":         img_obj,
        "labels":        labels,
        "sources":       sources,
    }

def build_iptv_json(channels: list[dict], now_str: str) -> dict:
    return {
        "id":          "giovang-iptv",
        "name":        "GioVang TV",
        "url":         BASE_URL + "/",
        "description": SITE_DESC,
        "disable_ads": True,
        "color":       "#FF8C00",
        "grid_number": 3,
        "image":       {"type": "cover", "url": SITE_ICON},
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
    ap = argparse.ArgumentParser(description="Crawler giovang.vin v8")
    ap.add_argument("--no-stream", action="store_true")
    ap.add_argument("--all",       action="store_true")
    ap.add_argument("--output",    default=OUTPUT_FILE)
    args = ap.parse_args()

    log("\n" + "═"*62)
    log("  🏟  CRAWLER giovang.vin  v8.0")
    log("  📡  1 source → 1 content → N streams (tên BLV)")
    log("  🖼  Thumbnail WEBP nền sáng, scale +10%")
    log("═"*62 + "\n")

    now_str = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M ICT")
    sc      = make_scraper()
    init_session(sc)

    matches = fetch_matches(sc, only_hot=not args.all)
    if not matches:
        log("❌ Không có trận nào."); sys.exit(1)

    log(f"\n🔄 Bước 2-4: Logo + Stream + Thumbnail ({len(matches)} trận)...")
    channels = []

    for i, m in enumerate(matches, 1):
        log(f"\n  [{i:02d}/{len(matches):02d}] {m['base_title']}")
        log(f"        {m['status']:8s} | {m['league']:25s} | {m['time_str']} {m['date_str']}")

        logo_a_img = logo_b_img = None
        streams    = []

        if not args.no_stream:
            try:
                log("        📋 WP AJAX logo...")
                hw, aw = fetch_wp_logos(sc, m["id"])
                la = (hw or {}).get("logo",""); lb = (aw or {}).get("logo","")
                if la: m["logo_a"] = la
                if lb: m["logo_b"] = lb
                if m.get("logo_a"): log(f"        A ✓ {m['logo_a'][-45:]}")
                if m.get("logo_b"): log(f"        B ✓ {m['logo_b'][-45:]}")
            except Exception as e:
                log(f"        ⚠ WP AJAX: {e}")

            try:
                if m.get("logo_a"): logo_a_img = _dl_logo(m["logo_a"], sc)
                if m.get("logo_b"): logo_b_img = _dl_logo(m["logo_b"], sc)
                log(f"        dl: A={'✓' if logo_a_img else '✗'} B={'✓' if logo_b_img else '✗'}")
            except Exception as e:
                log(f"        ⚠ Logo: {e}")

            try:
                log("        🎬 Stream API...")
                streams = fetch_streams(sc, m["id"])
                log(f"        → {len(streams)} BLV")
            except Exception as e:
                log(f"        ⚠ Stream: {e}"); streams = []

            time.sleep(0.4)

        try:
            thumb = make_thumbnail(
                m["home_team"], m["away_team"],
                logo_a_img, logo_b_img,
                m["time_str"], m["date_str"], m["league"],
                m["status"], m["score"], m["live_time"],
            )
            log(f"        🖼  WEBP {len(thumb):,} chars")
        except Exception as e:
            log(f"        ⚠ Thumbnail: {e}"); thumb = SITE_ICON

        channels.append(build_channel(m, streams, thumb, i))

    result = build_iptv_json(channels, now_str)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    live_n = sum(1 for m in matches if m["status"]=="live")
    up_n   = sum(1 for m in matches if m["status"]=="upcoming")
    log(f"\n{'═'*62}")
    log(f"  ✅  Xong!  →  {args.output}")
    log(f"  📊  {len(channels)} trận | 🔴 {live_n} | 🕐 {up_n} | {now_str}")
    log("═"*62 + "\n")


if __name__ == "__main__":
    main()
