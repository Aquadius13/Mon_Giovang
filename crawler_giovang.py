#!/usr/bin/env python3
"""
Crawler giovang.vin v9
CDN WEBP: lưu thumbnails/*.webp → commit GitHub → CDN URL
pip install cloudscraper requests pillow
"""
import argparse, base64, hashlib, io, json, os, re, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import cloudscraper, requests
from PIL import Image, ImageDraw, ImageFont

BASE_URL    = "https://giovang.vin"
API_LIVE    = "https://live-api.keovip88.net/storage/livestream/live.json"
API_ALL     = "https://live-api.keovip88.net/storage/livestream/all.json"
API_STREAM  = "https://live-api.keovip88.net/api/fixtures/{fid}"
WP_AJAX     = "https://giovang.vin/wp-admin/admin-ajax.php"
THUMB_DIR   = "thumbnails"
OUTPUT_FILE = "giovang_iptv.json"
VN_TZ       = timezone(timedelta(hours=7))
CHROME_UA   = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
SITE_ICON = (
    "https://giovang.vin/wp-content/uploads/2025/04/"
    "cropped-favicon-giovang-192x192.png"
)
SITE_DESC = (
    "Giovang TV là nền tảng phát trực tiếp bóng đá số 1 Việt Nam hiện nay, "
    "chuyên phát sóng trực tiếp các giải đấu từ quốc nội cho đến quốc tế như "
    "Ngoại hạng Anh, La Liga, Serie A, Bundesliga, Champions League và "
    "nhiều sự kiện thể thao khác."
)

LIVE_CODES     = {"1H", "2H", "HT", "PEN", "ET", "BT", "LIVE", "INT", "SUSP", "P"}
FINISHED_CODES = {"FT", "AET", "AWD", "WO", "ABD", "CANC"}

BLV_MAP = {
    "nha-dai":  "Nhà Đài",  "blv-tho":   "BLV Thỏ",   "blv-perry": "BLV Perry",
    "blv-1":    "BLV Tí",   "blv-3":     "BLV Dần",    "blv-5":     "BLV Thìn",
    "blv-6":    "BLV Tỵ",   "blv-10":    "BLV Dậu",    "blv-12":    "BLV Hợi",
    "blv-tom":  "BLV Tôm",  "blv-ben":   "BLV Ben",    "blv-cay":   "BLV Cầy",
    "blv-bang": "BLV Băng", "blv-mason": "BLV Mason",  "blv-che":   "BLV Chè",
    "blv-cam":  "BLV Câm",  "blv-dory":  "BLV Dory",   "blv-chanh": "BLV Chanh",
    "blv-nen":  "BLV Nến",
}

S = 1.15  # scale +15%

def sc(v: int) -> int:
    return int(v * S)

# ─── CDN ─────────────────────────────────────────────────────
def _cdn_base() -> str:
    if o := os.environ.get("THUMB_CDN_BASE", "").rstrip("/"):
        return o
    repo   = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    return (
        f"https://raw.githubusercontent.com/{repo}/{branch}/{THUMB_DIR}"
        if repo else ""
    )

def save_thumbnail(webp_bytes: bytes, ch_id: str) -> str:
    """
    GitHub Actions → lưu thumbnails/{ch_id}.webp → trả về CDN URL.
    Local          → trả về data:image/webp;base64,...
    """
    if not webp_bytes:
        return SITE_ICON
    cdn = _cdn_base()
    if cdn:
        p = Path(THUMB_DIR)
        p.mkdir(exist_ok=True)
        (p / f"{ch_id}.webp").write_bytes(webp_bytes)
        return f"{cdn}/{ch_id}.webp"
    return "data:image/webp;base64," + base64.b64encode(webp_bytes).decode()

# ─── Fonts ───────────────────────────────────────────────────
def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    suffix = "-Bold" if bold else ""
    suffix_lib = "Bold" if bold else "Regular"
    for p in [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{suffix}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans-{suffix_lib}.ttf",
        "C:/Windows/Fonts/" + ("arialbd.ttf" if bold else "arial.ttf"),
    ]:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()

def log(msg: str):
    print(msg, flush=True)

# ─── HTTP ────────────────────────────────────────────────────
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
        log(f"  🍪 Session {r.status_code} | cookies={list(sc.cookies.keys())}")
    except Exception as e:
        log(f"  ⚠ Session: {e}")

def _get(url: str, sc, label: str = "", params: dict = None) -> dict:
    for i in range(3):
        try:
            r = sc.get(url, timeout=20, params=params)
            r.raise_for_status()
            data = r.json()
            n = len(data.get("response", [])) if isinstance(data, dict) else "?"
            log(f"  ✓ {label} → {n} items")
            return data
        except Exception as e:
            if i < 2:
                time.sleep(2 ** i)
    return {}

def _post(sc, payload: dict) -> dict:
    for i in range(3):
        try:
            r = sc.post(WP_AJAX, data=payload, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i < 2:
                time.sleep(2 ** i)
    return {}

def _dl_logo(url: str, sc) -> "Image.Image | None":
    if not url:
        return None
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

# ─── Slug ────────────────────────────────────────────────────
_FROM = "àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđç·/_,:;"
_TO   = "aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyyddc------"

def slugify(s: str) -> str:
    s = s.strip().lower()
    for f, t in zip(_FROM, _TO):
        s = s.replace(f, t)
    s = re.sub(r"[^a-z0-9 -]", "", s)
    s = re.sub(r"\s+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")

def build_detail_url(home: str, away: str, dm: str, fid: str) -> str:
    return f"{BASE_URL}/{slugify(f'truc tiep {home} vs {away}-{dm}--{fid}')}/"

# ─── Parse ───────────────────────────────────────────────────
def parse_time(f: dict) -> tuple:
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
                sk = (
                    f"{int(p[2])}{int(p[1]):02d}{int(p[0]):02d}"
                    f"{int(mt.group(1)):02d}{mt.group(2)}"
                )
            except Exception:
                pass
    return ts, dm, sk

def get_status(code: str) -> str:
    if code in LIVE_CODES:
        return "live"
    if code in FINISHED_CODES:
        return "finished"
    return "upcoming"

def parse_fixture(f: dict) -> dict:
    fid  = str(f.get("id", ""))
    home = (f.get("teams") or {}).get("home") or {}
    away = (f.get("teams") or {}).get("away") or {}
    lg   = f.get("league") or {}
    gls  = f.get("goals")  or {}
    blvs = f.get("blv")    or []
    code = f.get("status_code", "NS")
    gh, ga = gls.get("home"), gls.get("away")
    if gh is None:
        ft = ((f.get("score") or {}).get("fulltime") or {})
        gh, ga = ft.get("home"), ft.get("away")
    score = f"{gh}-{ga}" if gh is not None and ga is not None else ""
    ts, ds, sk = parse_time(f)
    hn = home.get("name", "")
    an = away.get("name", "")
    return {
        "id":          fid,
        "base_title":  f"{hn} vs {an}",
        "home_team":   hn,
        "away_team":   an,
        "logo_a":      home.get("logo", ""),
        "logo_b":      away.get("logo", ""),
        "league":      lg.get("title", "") or "",
        "score":       score,
        "status":      get_status(code),
        "status_code": code,
        "live_time":   str(f.get("live_time", "")),
        "time_str":    ts,
        "date_str":    ds,
        "sort_key":    sk,
        "detail_url":  build_detail_url(hn, an, f.get("day_month", ""), fid),
        "blv_keys":    [b for b in blvs if b != "nha-dai"],
        "blv_names":   [BLV_MAP.get(b, b) for b in blvs if b != "nha-dai"],
        "is_hot":      bool(f.get("is_hot")),
        "is_hot_top":  bool(f.get("is_hot_top")),
    }

# ─── Fetch matches ───────────────────────────────────────────
def fetch_matches(sc, only_hot: bool) -> list:
    log("\n📡 Bước 1: Fetch live.json + all.json...")
    ts   = int(time.time() * 1000)
    live = _get(API_LIVE, sc, "live.json", {"t": ts})
    all_ = _get(API_ALL,  sc, "all.json",  {"t": ts})
    out, seen = [], set()

    for f in (live.get("response") or []):
        if get_status(f.get("status_code", "")) == "finished":
            continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")):
            continue
        m = parse_fixture(f)
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"])
            out.append(m)
            log(f"  🔴 [{m['status_code']:3s}] {m['base_title']} | {m['league']} | {m['time_str']}")

    for f in (all_.get("response") or []):
        if f.get("status_code", "NS") != "NS":
            continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")):
            continue
        m = parse_fixture(f)
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"])
            out.append(m)
            log(f"  🕐 [NS ] {m['base_title']} | {m['league']} | {m['time_str']}")

    prio = {"live": 0, "upcoming": 1, "finished": 2}
    out.sort(key=lambda x: (prio.get(x["status"], 9), x["sort_key"]))
    log(
        f"\n  ✅ {len(out)} trận — "
        f"🔴{sum(1 for m in out if m['status']=='live')} live "
        f"| 🕐{sum(1 for m in out if m['status']=='upcoming')} sắp tới"
    )
    return out

# ─── WP AJAX logo ────────────────────────────────────────────
def fetch_wp_logos(sc, fid: str) -> tuple:
    try:
        resp = _post(sc, {"action": "load_live_stream", "id": fid})
        if not resp or not isinstance(resp, dict) or not resp.get("success"):
            return {}, {}
        d    = resp.get("data") or {}
        home = d.get("home") or {}
        away = d.get("away") or {}
        if not isinstance(home, dict):
            home = {}
        if not isinstance(away, dict):
            away = {}
        return home, away
    except Exception as e:
        log(f"     ⚠ WP AJAX: {e}")
        return {}, {}

# ─── Stream API ──────────────────────────────────────────────
def fetch_streams(sc, fid: str) -> list:
    url = API_STREAM.format(fid=fid)
    for attempt in range(3):
        try:
            r = sc.get(url, timeout=15, headers={
                "Accept":  "application/json, */*",
                "Referer": BASE_URL + "/",
                "Origin":  BASE_URL,
            })
            if r.status_code != 200 or not r.content:
                time.sleep(1)
                continue
            data     = r.json()
            if data.get("code", -1) != 0:
                return []
            blv_list = (data.get("response") or {}).get("blv") or []
            result   = []
            for blv in blv_list:
                key  = blv.get("blv_key", "")
                name = blv.get("blv_name") or BLV_MAP.get(key, key)
                hd   = blv.get("link_stream_hd", "")
                sd   = blv.get("link_stream_sd", "")
                if hd or sd:
                    result.append({"blv_key": key, "blv_name": name, "url_hd": hd, "url_sd": sd})
                    log(f"     🎙 {name}: {(hd or sd)[-45:]}")
            return result
        except Exception as e:
            log(f"     ⚠ stream {attempt+1}: {e}")
            if attempt < 2:
                time.sleep(2)
    return []

# ─── Thumbnail WEBP ──────────────────────────────────────────
def make_thumbnail_bytes(
    home_name: str, away_name: str,
    logo_a: "Image.Image | None", logo_b: "Image.Image | None",
    time_str: str, date_str: str, league: str,
    status: str = "upcoming", score: str = "", live_time: str = "",
) -> bytes:
    W, H      = 800, 450
    LOGO_SZ   = sc(130)
    LOGO_CY   = 205
    MID_X     = W // 2
    INFO_HALF = sc(100)
    GAP       = sc(12)
    LX        = MID_X - INFO_HALF - GAP - LOGO_SZ // 2
    RX        = MID_X + INFO_HALF + GAP + LOGO_SZ // 2
    NAME_Y    = LOGO_CY + LOGO_SZ // 2 + sc(18)
    DATE_Y    = NAME_Y + sc(28)
    LEAGUE_Y  = 112

    # Nền sáng trắng → xanh nhạt
    img  = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)], fill=(int(252 - 8*t), int(254 - 6*t), 255))
    draw.rectangle([(0, 0),   (W, 8)], fill=(255, 140, 0))
    draw.rectangle([(0, H-8), (W, H)], fill=(255, 140, 0))

    # Tên giải + đường kẻ
    if league:
        draw.text(
            (MID_X, LEAGUE_Y), league[:26],
            fill=(55, 80, 160), font=_font(sc(17), False), anchor="mm"
        )
        ll = sc(95)
        draw.line(
            [(MID_X - ll, LEAGUE_Y + sc(14)), (MID_X + ll, LEAGUE_Y + sc(14))],
            fill=(195, 210, 235), width=2
        )

    # Giờ / Tỉ số
    if status == "live" and score:
        main_txt = score.replace("-", " : ")
        main_col = (190, 20, 20)
        sub_txt  = (f"● {live_time}'" if live_time and live_time not in ("", "0") else "● LIVE")
        sub_col  = (190, 20, 20)
    elif status == "live":
        main_txt = "LIVE"
        main_col = (190, 20, 20)
        sub_txt  = (f"● {live_time}'" if live_time and live_time not in ("", "0") else "●")
        sub_col  = (190, 20, 20)
    else:
        main_txt = time_str or "VS"
        main_col = (20, 45, 130)
        sub_txt  = "VS" if time_str else ""
        sub_col  = (90, 115, 195)

    has_sub = bool(sub_txt)
    draw.text(
        (MID_X, LOGO_CY - (sc(13) if has_sub else 0)),
        main_txt, fill=main_col, font=_font(sc(36)), anchor="mm"
    )
    if has_sub:
        draw.text(
            (MID_X, LOGO_CY + sc(24)),
            sub_txt, fill=sub_col, font=_font(sc(14), False), anchor="mm"
        )

    # Logo
    def paste_logo(cx, cy, logo_img, name, col):
        nonlocal img, draw
        if logo_img:
            logo_img = logo_img.convert("RGBA")
            logo_img.thumbnail((LOGO_SZ, LOGO_SZ), Image.LANCZOS)
            canvas = Image.new("RGBA", (LOGO_SZ, LOGO_SZ), (0, 0, 0, 0))
            ox = (LOGO_SZ - logo_img.width)  // 2
            oy = (LOGO_SZ - logo_img.height) // 2
            canvas.paste(logo_img, (ox, oy), logo_img)
            base = img.convert("RGBA")
            base.paste(canvas, (cx - LOGO_SZ//2, cy - LOGO_SZ//2), canvas)
            img  = base.convert("RGB")
            draw = ImageDraw.Draw(img)
        else:
            r2 = LOGO_SZ // 2 - 4
            draw.ellipse([(cx-r2+3, cy-r2+3), (cx+r2+3, cy+r2+3)], fill=(185, 195, 220))
            draw.ellipse([(cx-r2, cy-r2), (cx+r2, cy+r2)], fill=col)
            init = "".join(w[0].upper() for w in name.split()[:2]) or "?"
            draw.text((cx, cy), init, fill="white", font=_font(sc(34)), anchor="mm")

    paste_logo(LX, LOGO_CY, logo_a, home_name, (25, 70, 175))
    paste_logo(RX, LOGO_CY, logo_b, away_name, (175, 30, 55))

    # Tên đội
    def draw_name(cx, name):
        words = name.split()
        col   = (25, 50, 125)
        if len(name) <= 12 or len(words) <= 1:
            draw.text((cx, NAME_Y), name[:16], fill=col, font=_font(sc(17)), anchor="mm")
        else:
            mid = max(1, len(words) // 2)
            draw.text(
                (cx, NAME_Y - sc(9)), " ".join(words[:mid])[:16],
                fill=col, font=_font(sc(15)), anchor="mm"
            )
            draw.text(
                (cx, NAME_Y + sc(9)), " ".join(words[mid:])[:16],
                fill=col, font=_font(sc(13), False), anchor="mm"
            )

    draw_name(LX, home_name)
    draw_name(RX, away_name)

    # Ngày
    if date_str:
        draw.text(
            (MID_X, DATE_Y), f"\U0001f4c5  {date_str}",
            fill=(75, 100, 170), font=_font(sc(14), False), anchor="mm"
        )

    draw.text((W-12, H-14), "giovang.vin", fill=(155, 170, 205), font=_font(10, False), anchor="rm")

    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=83, method=4)
    return buf.getvalue()

# ─── Build channel ───────────────────────────────────────────
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
        sfx = f" {lt}'" if lt and lt not in ("", "0") else ""
        return (
            f"{m['home_team']} {score} {m['away_team']}  \U0001f534{sfx}"
            if score else f"{base}  \U0001f534 LIVE{sfx}"
        )
    if m["status"] == "finished":
        return (
            f"{m['home_team']} {score} {m['away_team']}  \u2705"
            if score else f"{base}  \u2705 KT"
        )
    ti = (
        f"  \U0001f550 {t} | {d}" if t and d else
        f"  \U0001f550 {t}"       if t else
        f"  \U0001f4c5 {d}"       if d else ""
    )
    return f"{base}{ti}"

def build_sources(ch_id: str, streams: list, detail_url: str) -> list:
    stream_list = []
    for i, s in enumerate(streams):
        links = []
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
            stream_list.append({
                "id":           make_id(ch_id, f"st{i}"),
                "name":         s["blv_name"],
                "stream_links": links,
            })

    if not stream_list:
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

def build_channel(m: dict, streams: list, thumb_url: str, idx: int) -> dict:
    ch_id     = make_id("gv", str(idx), slugify(m["base_title"])[:24])
    title     = build_title(m)
    league    = m["league"]
    score     = m["score"]
    blv_names = m.get("blv_names", [])
    multi_blv = len(streams) > 1

    labels = []
    st_map = {
        "live":     ("\u25cf LIVE",         "#C62828"),
        "upcoming": ("\U0001f550 Sắp diễn ra", "#1565C0"),
        "finished": ("\u2705 Kết thúc",     "#424242"),
    }
    st_t, st_c = st_map.get(m["status"], ("\u25cf LIVE", "#C62828"))
    labels.append({"text": st_t, "color": st_c, "text_color": "#ffffff", "position": "top-left"})

    if score and m["status"] == "live":
        lt  = m["live_time"]
        txt = f"{score}  {lt}'" if lt and lt not in ("", "0") else score
        labels.append({"text": txt, "color": "#B71C1C", "text_color": "#ffffff", "position": "bottom-right"})

    if blv_names:
        btxt = (
            f"\U0001f399 {blv_names[0]}" if len(blv_names) == 1
            else f"\U0001f399 {len(blv_names)} BLV"
        )
        labels.append({"text": btxt, "color": "#1B5E20", "text_color": "#ffffff", "position": "bottom-left"})

    img_obj = {
        "padding":          0,
        "background_color": "#F5F8FF",
        "display":          "contain",
        "url":              thumb_url,
        "width":            800,
        "height":           450,
    }
    sources = build_sources(ch_id, streams, m["detail_url"])

    parts = []
    if league:          parts.append(league)
    if m["time_str"]:   parts.append(m["time_str"])
    if m["date_str"]:   parts.append(m["date_str"])
    if m["status"] == "live":
        lt = m["live_time"]
        parts.append(f"\U0001f534 LIVE{' '+lt+chr(39) if lt and lt not in ('','0') else ''}")
        if score: parts.append(score)
    elif m["status"] == "upcoming":
        parts.append("\U0001f550 Sắp diễn ra")
    if blv_names:
        parts.append("\U0001f399 " + " | ".join(blv_names))

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

def build_iptv_json(channels: list, now_str: str) -> dict:
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
            "name":          "\U0001f525 Hot Match",
            "image":         None,
            "display":       "vertical",
            "grid_number":   2,
            "enable_detail": False,
            "channels":      channels,
        }],
    }

# ─── Main ────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Crawler giovang.vin v9")
    ap.add_argument("--no-stream", action="store_true")
    ap.add_argument("--all",       action="store_true")
    ap.add_argument("--output",    default=OUTPUT_FILE)
    args = ap.parse_args()

    log(f"\n{'='*62}")
    log(f"  CRAWLER giovang.vin v9  |  CDN: {_cdn_base() or 'base64 local'}")
    log(f"{'='*62}\n")

    now_str = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M ICT")
    sc = make_scraper()
    init_session(sc)

    matches = fetch_matches(sc, only_hot=not args.all)
    if not matches:
        log("Khong co tran nao.")
        sys.exit(1)

    log(f"\nBuoc 2-4: Logo + Stream + Thumbnail ({len(matches)} tran)...")
    channels = []

    for i, m in enumerate(matches, 1):
        log(f"\n  [{i:02d}/{len(matches):02d}] {m['base_title']}")
        log(f"        {m['status']:8s} | {m['league']:25s} | {m['time_str']} {m['date_str']}")

        logo_a_img = logo_b_img = None
        streams    = []

        if not args.no_stream:
            try:
                log("        WP AJAX logo...")
                hw, aw = fetch_wp_logos(sc, m["id"])
                la = (hw or {}).get("logo", "")
                lb = (aw or {}).get("logo", "")
                if la: m["logo_a"] = la
                if lb: m["logo_b"] = lb
                if m.get("logo_a"): log(f"        A ok {m['logo_a'][-45:]}")
                if m.get("logo_b"): log(f"        B ok {m['logo_b'][-45:]}")
            except Exception as e:
                log(f"        WP AJAX loi: {e}")

            try:
                if m.get("logo_a"): logo_a_img = _dl_logo(m["logo_a"], sc)
                if m.get("logo_b"): logo_b_img = _dl_logo(m["logo_b"], sc)
                log(f"        dl: A={'ok' if logo_a_img else 'x'} B={'ok' if logo_b_img else 'x'}")
            except Exception as e:
                log(f"        Logo loi: {e}")

            try:
                log("        Stream API...")
                streams = fetch_streams(sc, m["id"])
                log(f"        -> {len(streams)} BLV")
            except Exception as e:
                log(f"        Stream loi: {e}")
                streams = []

            time.sleep(0.4)

        # Thumbnail WEBP
        ch_id = make_id("gv", str(i), slugify(m["base_title"])[:24])
        try:
            webp_bytes = make_thumbnail_bytes(
                m["home_team"], m["away_team"],
                logo_a_img, logo_b_img,
                m["time_str"], m["date_str"], m["league"],
                m["status"], m["score"], m["live_time"],
            )
            thumb_url = save_thumbnail(webp_bytes, ch_id)
            is_cdn    = thumb_url.startswith("http")
            log(
                f"        WEBP {len(webp_bytes):,}B -> "
                f"{'CDN: '+thumb_url[-50:] if is_cdn else f'base64 {len(thumb_url):,}c'}"
            )
        except Exception as e:
            log(f"        Thumbnail loi: {e}")
            thumb_url = SITE_ICON

        channels.append(build_channel(m, streams, thumb_url, i))

    # Xoá thumbnail cũ không còn dùng
    cdn = _cdn_base()
    if cdn:
        td = Path(THUMB_DIR)
        if td.exists():
            active_ids = {ch["id"] for ch in channels}
            for fp in td.glob("*.webp"):
                if fp.stem not in active_ids:
                    fp.unlink(missing_ok=True)

    result = build_iptv_json(channels, now_str)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    json_sz = Path(args.output).stat().st_size
    live_n  = sum(1 for m in matches if m["status"] == "live")
    up_n    = sum(1 for m in matches if m["status"] == "upcoming")

    log(f"\n{'='*62}")
    log(f"  Xong! -> {args.output}  ({json_sz // 1024} KB)")
    log(f"  {len(channels)} tran | Live:{live_n} | Sap:{up_n} | {now_str}")
    log(f"{'='*62}\n")


if __name__ == "__main__":
    main()
