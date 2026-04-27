#!/usr/bin/env python3
"""
Crawler giovang.vin v10
CDN WEBP: lưu thumbnails/*.webp → commit GitHub → CDN URL
Logo: TheSportsDB + ESPN CDN thay thế keovip88.net
pip install cloudscraper requests pillow
"""

import argparse, base64, hashlib, io, json, os, re, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote as url_quote

import cloudscraper, requests
from PIL import Image, ImageDraw, ImageFont

BASE_URL   = "https://giovang.vin"
API_LIVE   = "https://live-api.keovip88.net/storage/livestream/live.json"
API_ALL    = "https://live-api.keovip88.net/storage/livestream/all.json"
API_STREAM = "https://live-api.keovip88.net/api/fixtures/{fid}"
WP_AJAX    = "https://giovang.vin/wp-admin/admin-ajax.php"
THUMB_DIR  = "thumbnails"
OUTPUT_FILE = "giovang_iptv.json"
VN_TZ = timezone(timedelta(hours=7))

CHROME_UA = (
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
    "nha-dai": "Nhà Đài",  "blv-tho": "BLV Thỏ",   "blv-perry": "BLV Perry",
    "blv-1":   "BLV Tí",   "blv-3":   "BLV Dần",   "blv-5":     "BLV Thìn",
    "blv-6":   "BLV Tỵ",   "blv-10":  "BLV Dậu",   "blv-12":    "BLV Hợi",
    "blv-tom": "BLV Tôm",  "blv-ben": "BLV Ben",   "blv-cay":   "BLV Cầy",
    "blv-bang":"BLV Băng", "blv-mason":"BLV Mason","blv-che":   "BLV Chè",
    "blv-cam": "BLV Câm",  "blv-dory": "BLV Dory", "blv-chanh": "BLV Chanh",
    "blv-nen": "BLV Nến",
}

S = 1.15  # scale +15%

def sc(v: int) -> int:
    return int(v * S)

# ─── Logo Alternative Sources ────────────────────────────────

# Nguồn 1: TheSportsDB – miễn phí, dữ liệu phong phú
SPORTSDB_SEARCH  = "https://www.thesportsdb.com/api/v1/json/3/searchteams.php"
# Nguồn 2: ESPN CDN – stable, đầy đủ giải đấu quốc tế
ESPN_TEAMS_API   = "https://site.api.espn.com/apis/site/v2/sports/soccer/all/teams"
# Nguồn 3: Sofascore (fallback CDN pattern khi đã có id)
SOFASCORE_LOGO   = "https://api.sofascore.com/api/v1/team/{team_id}/image"

# Cache tên đội → logo URL để tránh gọi API lặp lại
_LOGO_CACHE: dict[str, str] = {}

KEOVIP_DOMAINS = ("keovip88.net", "keovip88.com", "live-api.keovip88")


def _is_keovip_url(url: str) -> bool:
    return any(d in url for d in KEOVIP_DOMAINS)


def _resolve_logo_sportsdb(team_name: str, session) -> str:
    """Tìm logo từ TheSportsDB theo tên đội."""
    try:
        r = session.get(
            SPORTSDB_SEARCH,
            params={"t": team_name},
            timeout=8,
            headers={"User-Agent": CHROME_UA},
        )
        r.raise_for_status()
        data = r.json()
        teams = data.get("teams") or []
        if teams:
            badge = teams[0].get("strTeamBadge") or ""
            if badge:
                # TheSportsDB trả về URL dạng /medium → lấy bản lớn
                return badge.replace("/medium", "") if "/medium" in badge else badge
    except Exception:
        pass
    return ""


def _resolve_logo_espn(team_name: str, session) -> str:
    """Tìm logo từ ESPN API theo tên đội (tìm kiếm gần đúng)."""
    try:
        r = session.get(
            ESPN_TEAMS_API,
            params={"limit": 500},
            timeout=10,
            headers={"User-Agent": CHROME_UA},
        )
        r.raise_for_status()
        sports = r.json().get("sports") or []
        name_lower = team_name.lower()
        for sport in sports:
            for league in (sport.get("leagues") or []):
                for team in (league.get("teams") or []):
                    t = team.get("team") or {}
                    if name_lower in (t.get("displayName") or "").lower():
                        logos = t.get("logos") or []
                        if logos:
                            return logos[0].get("href", "")
    except Exception:
        pass
    return ""


def resolve_logo(url: str, team_name: str, session) -> str:
    """
    Nếu URL logo từ keovip88.net → thay bằng nguồn uy tín hơn.
    Thứ tự ưu tiên: TheSportsDB → ESPN → giữ nguyên URL gốc.
    """
    if not url or not _is_keovip_url(url):
        return url  # URL đã ổn, giữ nguyên

    cache_key = team_name.strip().lower()
    if cache_key in _LOGO_CACHE:
        return _LOGO_CACHE[cache_key] or url

    log(f"    🔄 Resolve logo [{team_name}] ...")
    alt = _resolve_logo_sportsdb(team_name, session)
    if not alt:
        alt = _resolve_logo_espn(team_name, session)

    _LOGO_CACHE[cache_key] = alt
    if alt:
        log(f"    ✅ Logo mới: {alt[-60:]}")
    else:
        log(f"    ⚠ Không tìm được logo thay thế, giữ URL gốc")
    return alt if alt else url


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
    suffix     = "-Bold"  if bold else ""
    suffix_lib = "Bold"   if bold else "Regular"
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
    s = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    s.headers.update({
        "User-Agent": CHROME_UA,
        "Accept-Language": "vi-VN,vi;q=0.9",
        "Referer": BASE_URL + "/",
    })
    return s


def init_session(s):
    try:
        r = s.get(BASE_URL + "/", timeout=20)
        log(f"  🍪 Session {r.status_code} | cookies={list(s.cookies.keys())}")
    except Exception as e:
        log(f"  ⚠ Session: {e}")


def _get(url: str, s, label: str = "", params: dict = None) -> dict:
    for i in range(3):
        try:
            r = s.get(url, timeout=20, params=params)
            r.raise_for_status()
            data = r.json()
            n = len(data.get("response", [])) if isinstance(data, dict) else "?"
            log(f"  ✓ {label} → {n} items")
            return data
        except Exception as e:
            if i < 2:
                time.sleep(2 ** i)
    return {}


def _post(s, payload: dict) -> dict:
    for i in range(3):
        try:
            r = s.post(WP_AJAX, data=payload, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i < 2:
                time.sleep(2 ** i)
    return {}


def _dl_logo(url: str, s) -> "Image.Image | None":
    if not url:
        return None
    try:
        r = s.get(url, timeout=8, headers={
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

_FROM = "àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđç·/_;,:"
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
    if code in LIVE_CODES:     return "live"
    if code in FINISHED_CODES: return "finished"
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

def fetch_matches(s, only_hot: bool) -> list:
    log("\n📡 Bước 1: Fetch live.json + all.json...")
    ts   = int(time.time() * 1000)
    live = _get(API_LIVE, s, "live.json", {"t": ts})
    all_ = _get(API_ALL,  s, "all.json",  {"t": ts})

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

def fetch_wp_logos(s, fid: str) -> tuple:
    try:
        resp = _post(s, {"action": "load_live_stream", "id": fid})
        if not resp or not isinstance(resp, dict) or not resp.get("success"):
            return {}, {}
        d    = resp.get("data") or {}
        home = d.get("home") or {}
        away = d.get("away") or {}
        if not isinstance(home, dict): home = {}
        if not isinstance(away, dict): away = {}
        return home, away
    except Exception as e:
        log(f"  ⚠ WP AJAX: {e}")
        return {}, {}


# ─── Stream API ──────────────────────────────────────────────

def fetch_streams(s, fid: str) -> list:
    url = API_STREAM.format(fid=fid)
    for attempt in range(3):
        try:
            r = s.get(url, timeout=15, headers={
                "Accept":  "application/json, */*",
                "Referer": BASE_URL + "/",
                "Origin":  BASE_URL,
            })
            if r.status_code != 200 or not r.content:
                time.sleep(1)
                continue
            data = r.json()
            if data.get("code", -1) != 0:
                return []
            blv_list = (data.get("response") or {}).get("blv") or []
            result = []
            for blv in blv_list:
                key  = blv.get("blv_key", "")
                name = blv.get("blv_name") or BLV_MAP.get(key, key)
                hd   = blv.get("link_stream_hd", "")
                sd   = blv.get("link_stream_sd", "")
                if hd or sd:
                    result.append({"blv_key": key, "blv_name": name, "url_hd": hd, "url_sd": sd})
                    log(f"  🎙 {name}: {(hd or sd)[-45:]}")
            return result
        except Exception as e:
            log(f"  ⚠ stream {attempt+1}: {e}")
            if attempt < 2:
                time.sleep(2)
    return []


# ─── Thumbnail WEBP ──────────────────────────────────────────

def _fmt_date(date_str: str) -> str:
    """Bỏ năm khỏi chuỗi ngày: '26/04/2025' → '26/04', '26-04-2025' → '26/04'."""
    if not date_str:
        return ""
    for sep in ("/", "-"):
        parts = date_str.split(sep)
        if len(parts) >= 2:
            return f"{parts[0].zfill(2)}/{parts[1].zfill(2)}"
    return date_str


def _crop_logo_content(im: "Image.Image") -> "Image.Image":
    """Cắt viền trong suốt VÀ viền trắng/sáng xung quanh logo.
    Xử lý cả 2 loại logo: nền trong suốt và nền trắng đục.
    Đảm bảo mọi logo được scale đồng đều, không có vùng trắng thừa."""
    from PIL import ImageOps
    im = im.convert("RGBA")

    # Bước 1: Cắt viền trong suốt bằng alpha channel
    r, g, b, a = im.split()
    alpha_bbox = a.getbbox()
    if alpha_bbox:
        # Chỉ crop nếu cắt được ít nhất 5% diện tích
        orig_area  = im.width * im.height
        crop_area  = (alpha_bbox[2] - alpha_bbox[0]) * (alpha_bbox[3] - alpha_bbox[1])
        if crop_area < orig_area * 0.95:
            im = im.crop(alpha_bbox)

    # Bước 2: Cắt viền trắng / sáng (nền trắng đục — alpha=255 nhưng toàn trắng)
    # Chuyển sang grayscale → invert → nền trắng = 0, nội dung logo = sáng
    gray        = im.convert("RGB").convert("L")
    inv         = ImageOps.invert(gray)
    # Pixel có giá trị invert > 12 → là nội dung thực (ngưỡng thấp để giữ màu sáng)
    content_msk = inv.point(lambda p: 255 if p > 12 else 0)
    content_box = content_msk.getbbox()
    if content_box:
        x0, y0, x1, y1 = content_box
        # Thêm 1px padding để tránh cắt sát cạnh
        x0 = max(0, x0 - 1)
        y0 = max(0, y0 - 1)
        x1 = min(im.width,  x1 + 1)
        y1 = min(im.height, y1 + 1)
        im = im.crop((x0, y0, x1, y1))

    return im


def make_thumbnail_bytes(
    home_name: str, away_name: str,
    logo_a: "Image.Image | None", logo_b: "Image.Image | None",
    time_str: str, date_str: str, league: str,
    status: str = "upcoming", score: str = "", live_time: str = "",
) -> bytes:
    W, H      = 800, 450
    MID_X     = W // 2

    # ── Kích thước logo +20% so với gốc sc(130) ──────────────
    LOGO_SZ   = sc(156)        # = 179 px

    # ── Font sizes ────────────────────────────────────────────
    LEAGUE_FS = sc(31)             # sc(26) × 1.20 ≈ sc(31) (+20%) → 35 px
    TIME_FS   = sc(36)             # giờ thi đấu = 41 px
    DATE_FS   = TIME_FS * 2 // 3   # ngày = 2/3 giờ ≈ 27 px, không hiện năm
    NAME_FS   = sc(25)             # sc(22) × 1.15 ≈ sc(25) (+15%), không đậm → 28 px

    # ── Layout dọc ───────────────────────────────────────────
    # Viền cam: 8px trên + 8px dưới
    # League: sát viền trên (y=8 → text center tại y=26)
    # Separator: y=42
    # Logo: nâng lên 80px → center tại y=152 (top≈63, bot≈241)
    LEAGUE_CY = 26             # trên cùng, sát viền cam
    SEP_Y     = 42             # đường kẻ bên dưới tên giải
    LOGO_CY   = 192            # 152 + 40 = 192  ← hạ xuống 40px
    NAME_Y    = LOGO_CY + LOGO_SZ // 2 + 19  # +5px so với trước (14→19)

    # ── Vị trí logo trái/phải ────────────────────────────────
    INFO_HALF = sc(95)
    GAP       = sc(10)
    LX        = MID_X - INFO_HALF - GAP - LOGO_SZ // 2
    RX        = MID_X + INFO_HALF + GAP + LOGO_SZ // 2

    # ── Vị trí giờ/ngày căn giữa cùng nhau tại LOGO_CY ─────
    # TIME center = LOGO_CY - 16  →  152 - 16 = 136
    # DATE center = LOGO_CY + 23  →  152 + 23 = 175
    TIME_Y    = LOGO_CY - 16
    DATE_Y    = LOGO_CY + 23

    # ── Canvas ───────────────────────────────────────────────
    img  = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    # Gradient nền trắng → xanh nhạt
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)], fill=(int(252 - 8*t), int(254 - 6*t), 255))

    # Viền cam trên + dưới
    draw.rectangle([(0, 0),   (W, 8)],  fill=(255, 140, 0))
    draw.rectangle([(0, H-8), (W, H)],  fill=(255, 140, 0))

    # ── 1. Tên giải đấu: trên cùng, font LEAGUE_FS ───────────
    if league:
        draw.text(
            (MID_X, LEAGUE_CY), league[:30],
            fill=(35, 65, 160), font=_font(LEAGUE_FS, False), anchor="mm"
        )
        ll = sc(115)
        draw.line([(MID_X - ll, SEP_Y), (MID_X + ll, SEP_Y)],
                  fill=(190, 210, 240), width=2)

    # ── 2. Vùng trung tâm: giờ/ngày hoặc tỉ số LIVE ─────────
    if status == "live" and score:
        # Tỉ số lớn + phút thi đấu nhỏ bên dưới
        draw.text((MID_X, TIME_Y), score.replace("-", " : "),
                  fill=(190, 20, 20), font=_font(TIME_FS), anchor="mm")
        live_lbl = (f"● {live_time}'"
                    if live_time and live_time not in ("", "0")
                    else "● LIVE")
        draw.text((MID_X, DATE_Y), live_lbl,
                  fill=(190, 20, 20), font=_font(DATE_FS, False), anchor="mm")

    elif status == "live":
        draw.text((MID_X, TIME_Y), "LIVE",
                  fill=(190, 20, 20), font=_font(TIME_FS), anchor="mm")
        live_lbl = (f"● {live_time}'"
                    if live_time and live_time not in ("", "0")
                    else "●")
        draw.text((MID_X, DATE_Y), live_lbl,
                  fill=(190, 20, 20), font=_font(DATE_FS, False), anchor="mm")

    else:
        # UPCOMING: giờ trên, ngày sát dưới, KHÔNG chữ VS
        if time_str:
            draw.text((MID_X, TIME_Y), time_str,
                      fill=(20, 45, 130), font=_font(TIME_FS), anchor="mm")
        if date_str:
            draw.text((MID_X, DATE_Y), f"📅 {_fmt_date(date_str)}",
                      fill=(50, 85, 175), font=_font(DATE_FS, False), anchor="mm")
        if not time_str and not date_str:
            draw.text((MID_X, LOGO_CY), "—",
                      fill=(160, 170, 200), font=_font(TIME_FS), anchor="mm")

    # ── 3. Logo hai đội (crop viền trắng + scale đồng đều) ─────
    def paste_logo(cx: int, cy: int, logo_img, name: str, fallback_col: tuple):
        nonlocal img, draw
        if logo_img:
            try:
                logo_img = _crop_logo_content(logo_img)   # bỏ viền trắng/trong suốt
                w, h     = logo_img.size
                if w > 0 and h > 0:
                    # Scale theo chiều LỚN NHẤT → luôn điền đầy LOGO_SZ
                    # (khác thumbnail: thumbnail chỉ thu nhỏ, không phóng to)
                    scale   = LOGO_SZ / max(w, h)
                    new_w   = max(1, int(w * scale))
                    new_h   = max(1, int(h * scale))
                    logo_img = logo_img.resize((new_w, new_h), Image.LANCZOS)
                canvas = Image.new("RGBA", (LOGO_SZ, LOGO_SZ), (0, 0, 0, 0))
                ox = (LOGO_SZ - logo_img.width)  // 2
                oy = (LOGO_SZ - logo_img.height) // 2
                canvas.paste(logo_img, (ox, oy), logo_img)
                base = img.convert("RGBA")
                base.paste(canvas, (cx - LOGO_SZ // 2, cy - LOGO_SZ // 2), canvas)
                img  = base.convert("RGB")
                draw = ImageDraw.Draw(img)
                return
            except Exception:
                pass
        # Fallback: vòng tròn màu + chữ viết tắt
        r2 = LOGO_SZ // 2 - 4
        draw.ellipse([(cx-r2+3, cy-r2+3), (cx+r2+3, cy+r2+3)], fill=(185, 195, 220))
        draw.ellipse([(cx-r2,   cy-r2),   (cx+r2,   cy+r2)],   fill=fallback_col)
        init = "".join(w[0].upper() for w in name.split()[:2]) or "?"
        draw.text((cx, cy), init, fill="white", font=_font(sc(34)), anchor="mm")

    paste_logo(LX, LOGO_CY, logo_a, home_name, (25,  70, 175))
    paste_logo(RX, LOGO_CY, logo_b, away_name, (175, 30,  55))

    # ── 4. Tên đội: 1 hàng duy nhất, không đậm, NAME_FS ─────
    for cx, name in [(LX, home_name), (RX, away_name)]:
        draw.text((cx, NAME_Y), name[:20],
                  fill=(22, 50, 125), font=_font(NAME_FS, False), anchor="mm")

    # Watermark
    draw.text((W - 12, H - 14), "giovang.vin",
              fill=(155, 170, 205), font=_font(10, False), anchor="rm")

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
            f"{m['home_team']} {score} {m['away_team']} 🔴{sfx}"
            if score else f"{base} 🔴 LIVE{sfx}"
        )
    if m["status"] == "finished":
        return (
            f"{m['home_team']} {score} {m['away_team']} ✅"
            if score else f"{base} ✅ KT"
        )
    ti = (
        f" 🕐 {t} | {d}" if t and d else
        f" 🕐 {t}"       if t       else
        f" 📅 {d}"       if d       else ""
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
        # Fallback: KHÔNG dùng iframe vì iframe nhúng giovang.vin sẽ chạy JS
        # pushState() nhiều lần → WebView app bị kẹt trong history stack của site,
        # back từ player không về được màn hình chính mà phải reload.
        # Dùng "link" thay thế → mở trình duyệt ngoài, tránh hoàn toàn vấn đề này.
        stream_list = [{
            "id":   make_id(ch_id, "st0"),
            "name": "Trực tiếp",
            "stream_links": [{
                "id":      make_id(ch_id, "l0"),
                "name":    "Xem trực tiếp",
                "type":    "link",        # mở external browser, không nhúng WebView
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
    ch_id      = make_id("gv", str(idx), slugify(m["base_title"])[:24])
    title      = build_title(m)
    league     = m["league"]
    score      = m["score"]
    blv_names  = m.get("blv_names", [])
    multi_blv  = len(streams) > 1

    labels = []

    # ── Status label ──────────────────────────────────────────
    # - LIVE   → badge đỏ top-left
    # - KẾT THÚC → badge xám top-left
    # - UPCOMING → hiện giờ thi đấu ở top-left để ghi đè label
    #              "Sắp diễn ra" mà player tự sinh ra
    if m["status"] == "live":
        labels.append({
            "text": "● LIVE", "color": "#C62828",
            "text_color": "#ffffff", "position": "top-left",
        })
    elif m["status"] == "finished":
        labels.append({
            "text": "✅ Kết thúc", "color": "#424242",
            "text_color": "#ffffff", "position": "top-left",
        })
    # upcoming → không hiện label top-left

    # ── Score + live time ─────────────────────────────────────
    if score and m["status"] == "live":
        lt  = m["live_time"]
        txt = f"{score} {lt}'" if lt and lt not in ("", "0") else score
        labels.append({"text": txt, "color": "#B71C1C", "text_color": "#ffffff", "position": "bottom-right"})

    # ── BLV ──────────────────────────────────────────────────
    if blv_names:
        btxt = (
            f"🎙 {blv_names[0]}" if len(blv_names) == 1
            else f"🎙 {len(blv_names)} BLV"
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
        parts.append(f"🔴 LIVE{' '+lt+chr(39) if lt and lt not in ('','0') else ''}")
        if score: parts.append(score)
    if blv_names:
        parts.append("🎙 " + " | ".join(blv_names))

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
        "id":              "giovang-iptv",
        "name":            "GioVang TV",
        "url":             BASE_URL + "/",
        "description":     SITE_DESC,
        "disable_ads":     True,
        "color":           "#FF8C00",
        "grid_number":     3,
        "image":           {"type": "cover", "url": SITE_ICON},
        # ── Khắc phục lỗi back từ player không load được trang ─────────────
        # reload_on_resume: app tự reload JSON khi quay lại màn hình chính
        # auto_refresh:     làm mới danh sách mỗi 5 phút (300 giây)
        # cache:            tắt cache để tránh phục vụ dữ liệu cũ sau khi back
        "reload_on_resume": True,
        "auto_refresh":     300,
        "cache":            False,
        # ────────────────────────────────────────────────────────────────────
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
    ap = argparse.ArgumentParser(description="Crawler giovang.vin v10")
    ap.add_argument("--no-stream", action="store_true")
    ap.add_argument("--all",       action="store_true")
    ap.add_argument("--output",    default=OUTPUT_FILE)
    args = ap.parse_args()

    log(f"\n{'='*62}")
    log(f"  CRAWLER giovang.vin v10 | CDN: {_cdn_base() or 'base64 local'}")
    log(f"  Logo sources: TheSportsDB → ESPN (thay thế keovip88.net)")
    log(f"{'='*62}\n")

    now_str = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M ICT")
    s       = make_scraper()
    init_session(s)

    matches = fetch_matches(s, only_hot=not args.all)
    if not matches:
        log("Không có trận nào.")
        sys.exit(1)

    log(f"\nBước 2-4: Logo + Stream + Thumbnail ({len(matches)} trận)...")

    channels = []
    for i, m in enumerate(matches, 1):
        log(f"\n  [{i:02d}/{len(matches):02d}] {m['base_title']}")
        log(f"  {m['status']:8s} | {m['league']:25s} | {m['time_str']} {m['date_str']}")

        logo_a_img = logo_b_img = None
        streams    = []

        if not args.no_stream:
            # ── WP AJAX logo ──────────────────────────────────
            try:
                log("  WP AJAX logo...")
                hw, aw = fetch_wp_logos(s, m["id"])
                la = (hw or {}).get("logo", "")
                lb = (aw or {}).get("logo", "")
                if la: m["logo_a"] = la
                if lb: m["logo_b"] = lb
            except Exception as e:
                log(f"  WP AJAX lỗi: {e}")

            # ── Giải quyết logo từ nguồn thay thế ────────────
            # Nếu URL logo từ keovip88.net → thay bằng TheSportsDB / ESPN
            try:
                m["logo_a"] = resolve_logo(m.get("logo_a", ""), m["home_team"], s)
                m["logo_b"] = resolve_logo(m.get("logo_b", ""), m["away_team"], s)
                if m.get("logo_a"): log(f"  A: {m['logo_a'][-60:]}")
                if m.get("logo_b"): log(f"  B: {m['logo_b'][-60:]}")
            except Exception as e:
                log(f"  Logo resolve lỗi: {e}")

            # ── Tải logo về để vẽ thumbnail ───────────────────
            try:
                if m.get("logo_a"): logo_a_img = _dl_logo(m["logo_a"], s)
                if m.get("logo_b"): logo_b_img = _dl_logo(m["logo_b"], s)
                log(f"  dl: A={'ok' if logo_a_img else 'x'} B={'ok' if logo_b_img else 'x'}")
            except Exception as e:
                log(f"  Logo tải lỗi: {e}")

            # ── Stream ────────────────────────────────────────
            try:
                log("  Stream API...")
                streams = fetch_streams(s, m["id"])
                log(f"  -> {len(streams)} BLV")
            except Exception as e:
                log(f"  Stream lỗi: {e}")
                streams = []

        time.sleep(0.4)

        # ── Thumbnail WEBP ────────────────────────────────────
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
                f"  WEBP {len(webp_bytes):,}B → "
                f"{'CDN: '+thumb_url[-50:] if is_cdn else f'base64 {len(thumb_url):,}c'}"
            )
        except Exception as e:
            log(f"  Thumbnail lỗi: {e}")
            thumb_url = SITE_ICON

        channels.append(build_channel(m, streams, thumb_url, i))

    # ── Xoá thumbnail cũ ─────────────────────────────────────
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
    log(f"  Xong! → {args.output} ({json_sz // 1024} KB)")
    log(f"  {len(channels)} trận | Live:{live_n} | Sắp:{up_n} | {now_str}")
    log(f"{'='*62}\n")


if __name__ == "__main__":
    main()
