#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   CRAWLER giovang.vin  v9.0                                  ║
║                                                              ║
║   Thay đổi so với v8:                                        ║
║   • Thumbnail lưu file WEBP → upload GitHub → CDN URL       ║
║     (thay vì nhúng base64 vào JSON)                          ║
║   • Scale nội dung card +15%: logo, tên đội, giải, giờ/ngày ║
║                                                              ║
║   Cấu hình GitHub CDN:                                       ║
║     Đặt GITHUB_TOKEN, GITHUB_USERNAME, GITHUB_REPO          ║
║     trong file giovang_config.json hoặc biến môi trường      ║
╚══════════════════════════════════════════════════════════════╝

Cài:  pip install cloudscraper requests pillow
Chạy:
    python crawler_giovang.py
    python crawler_giovang.py --no-stream
    python crawler_giovang.py --all
    python crawler_giovang.py --output out.json
    python crawler_giovang.py --no-cdn   # dùng base64 thay CDN
"""

import argparse, base64, hashlib, io, json, os, re, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

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
THUMB_DIR   = "thumbs"          # thư mục lưu file WEBP local
VN_TZ       = timezone(timedelta(hours=7))
CHROME_UA   = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
SITE_ICON = "https://giovang.vin/wp-content/uploads/2025/04/cropped-favicon-giovang-192x192.png"
SITE_DESC = (
    "Giovang TV là nền tảng phát trực tiếp bóng đá số 1 Việt Nam hiện nay, "
    "chuyên phát sóng trực tiếp các giải đấu từ quốc nội cho đến quốc tế như "
    "Ngoại hạng Anh, La Liga, Serie A, Bundesliga, Champions League và "
    "nhiều sự kiện thể thao khác."
)

LIVE_CODES     = {"1H","2H","HT","PEN","ET","BT","LIVE","INT","SUSP","P"}
FINISHED_CODES = {"FT","AET","AWD","WO","ABD","CANC"}

BLV_MAP = {
    "nha-dai":"Nhà Đài","blv-tho":"BLV Thỏ","blv-perry":"BLV Perry",
    "blv-1":"BLV Tí","blv-3":"BLV Dần","blv-5":"BLV Thìn","blv-6":"BLV Tỵ",
    "blv-10":"BLV Dậu","blv-12":"BLV Hợi","blv-tom":"BLV Tôm","blv-ben":"BLV Ben",
    "blv-cay":"BLV Cầy","blv-bang":"BLV Băng","blv-mason":"BLV Mason",
    "blv-che":"BLV Chè","blv-cam":"BLV Câm","blv-dory":"BLV Dory",
    "blv-chanh":"BLV Chanh","blv-nen":"BLV Nến",
}

# ─── Scale +15% ───────────────────────────────────────────────
S = 1.15   # scale factor

def sc(v: int) -> int:
    """Scale integer value +15%."""
    return int(v * S)

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

# ─── GitHub CDN ───────────────────────────────────────────────
class GitHubCDN:
    """Upload WEBP file lên GitHub repo → trả về raw CDN URL."""

    def __init__(self, token: str, username: str, repo: str, branch: str = "main"):
        self.token    = token
        self.username = username
        self.repo     = repo
        self.branch   = branch
        self.api      = "https://api.github.com"
        self.headers  = {
            "Authorization":        f"Bearer {token}",
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._sha_cache: dict[str, str] = {}

    def _get_sha(self, path: str) -> str:
        """Lấy SHA của file nếu đã tồn tại."""
        if path in self._sha_cache:
            return self._sha_cache[path]
        url = f"{self.api}/repos/{self.username}/{self.repo}/contents/{path}"
        try:
            r = requests.get(url, headers=self.headers,
                             params={"ref": self.branch}, timeout=10)
            if r.status_code == 200:
                sha = r.json().get("sha", "")
                self._sha_cache[path] = sha
                return sha
        except Exception:
            pass
        return ""

    def upload(self, filename: str, content: bytes, subdir: str = "thumbs") -> str:
        """
        Upload file lên GitHub repo.
        Trả về raw CDN URL: https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}
        """
        path    = f"{subdir}/{filename}" if subdir else filename
        encoded = base64.b64encode(content).decode("ascii")
        sha     = self._get_sha(path)
        payload: dict = {
            "message": f"thumb: {filename}",
            "content": encoded,
            "branch":  self.branch,
        }
        if sha:
            payload["sha"] = sha

        url = f"{self.api}/repos/{self.username}/{self.repo}/contents/{path}"
        r   = requests.put(url, headers=self.headers, json=payload, timeout=30)
        r.raise_for_status()
        self._sha_cache[path] = r.json().get("content", {}).get("sha", "")
        raw_url = (
            f"https://raw.githubusercontent.com"
            f"/{self.username}/{self.repo}/{self.branch}/{path}"
        )
        return raw_url

    def raw_url(self, filename: str, subdir: str = "thumbs") -> str:
        path = f"{subdir}/{filename}" if subdir else filename
        return (
            f"https://raw.githubusercontent.com"
            f"/{self.username}/{self.repo}/{self.branch}/{path}"
        )


def load_github_config() -> dict:
    """Đọc config GitHub từ file hoặc env vars."""
    cfg = {
        "github_token":    os.environ.get("GITHUB_TOKEN", ""),
        "github_username": os.environ.get("GITHUB_USERNAME", ""),
        "github_repo":     os.environ.get("GITHUB_REPO", "giovang-iptv"),
        "github_branch":   os.environ.get("GITHUB_BRANCH", "main"),
    }
    config_file = Path("giovang_config.json")
    if config_file.exists():
        try:
            saved = json.loads(config_file.read_text(encoding="utf-8"))
            for k in cfg:
                if saved.get(k): cfg[k] = saved[k]
        except Exception:
            pass
    return cfg

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

def _get(url, sc_, label="", params=None) -> dict:
    for i in range(3):
        try:
            r = sc_.get(url, timeout=20, params=params)
            r.raise_for_status()
            data = r.json()
            n = len(data.get("response", [])) if isinstance(data, dict) else "?"
            log(f"  ✓ {label}  →  {n} items")
            return data
        except Exception as e:
            if i < 2: time.sleep(2**i)
    return {}

def _post(sc_, payload) -> dict:
    for i in range(3):
        try:
            r = sc_.post(WP_AJAX, data=payload, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i < 2: time.sleep(2**i)
    return {}

def _dl_logo(url: str, sc_) -> "Image.Image | None":
    if not url: return None
    try:
        r = sc_.get(url, timeout=8, headers={
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
    raw_t = f.get("time", ""); dm = f.get("day_month", ""); raw_d = f.get("date", "")
    mt    = re.match(r"(\d{1,2}):(\d{2})", raw_t)
    ts    = f"{int(mt.group(1)):02d}:{mt.group(2)}" if mt else ""
    sk    = ""
    if mt:
        p = re.split(r"[-/]", raw_d)
        if len(p) == 3:
            try: sk = f"{int(p[2])}{int(p[1]):02d}{int(p[0]):02d}{int(mt.group(1)):02d}{mt.group(2)}"
            except Exception: pass
    return ts, dm, sk

def get_status(code: str) -> str:
    if code in LIVE_CODES:     return "live"
    if code in FINISHED_CODES: return "finished"
    return "upcoming"

def parse_fixture(f: dict) -> dict:
    fid  = str(f.get("id",""))
    home = (f.get("teams") or {}).get("home") or {}
    away = (f.get("teams") or {}).get("away") or {}
    lg   = f.get("league") or {}
    gls  = f.get("goals")  or {}
    blvs = f.get("blv")    or []
    code = f.get("status_code","NS")
    gh, ga = gls.get("home"), gls.get("away")
    if gh is None:
        ft = ((f.get("score") or {}).get("fulltime") or {})
        gh, ga = ft.get("home"), ft.get("away")
    score = f"{gh}-{ga}" if gh is not None and ga is not None else ""
    ts, ds, sk = parse_time(f)
    hn = home.get("name",""); an = away.get("name","")
    return {
        "id": fid, "base_title": f"{hn} vs {an}",
        "home_team": hn, "away_team": an,
        "logo_a": home.get("logo",""), "logo_b": away.get("logo",""),
        "league": lg.get("title","") or "",
        "score": score, "status": get_status(code), "status_code": code,
        "live_time": str(f.get("live_time","")),
        "time_str": ts, "date_str": ds, "sort_key": sk,
        "detail_url": build_detail_url(hn, an, f.get("day_month",""), fid),
        "blv_keys":  [b for b in blvs if b != "nha-dai"],
        "blv_names": [BLV_MAP.get(b,b) for b in blvs if b != "nha-dai"],
        "is_hot": bool(f.get("is_hot")), "is_hot_top": bool(f.get("is_hot_top")),
        "sport_type": f.get("type","football"),
    }

# ─── Fetch matches ────────────────────────────────────────────
def fetch_matches(sc_, only_hot: bool) -> list[dict]:
    log("\n📡 Bước 1: Fetch live.json + all.json...")
    ts = int(time.time()*1000)
    live = _get(API_LIVE, sc_, "live.json", {"t": ts})
    all_ = _get(API_ALL,  sc_, "all.json",  {"t": ts})
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
def fetch_wp_logos(sc_, fid: str) -> tuple[dict, dict]:
    try:
        resp = _post(sc_, {"action": "load_live_stream", "id": fid})
        if not resp or not isinstance(resp,dict) or not resp.get("success"):
            return {}, {}
        d    = resp.get("data") or {}
        home = d.get("home") or {}
        away = d.get("away") or {}
        return (home if isinstance(home,dict) else {}), (away if isinstance(away,dict) else {})
    except Exception as e:
        log(f"     ⚠ WP AJAX: {e}"); return {}, {}

# ─── Stream API ───────────────────────────────────────────────
def fetch_streams(sc_, fid: str) -> list[dict]:
    url = API_STREAM.format(fid=fid)
    for attempt in range(3):
        try:
            r = sc_.get(url, timeout=15, headers={
                "Accept":  "application/json, */*",
                "Referer": BASE_URL + "/",
                "Origin":  BASE_URL,
            })
            if r.status_code != 200 or not r.content:
                time.sleep(1); continue
            data     = r.json()
            if data.get("code",-1) != 0: return []
            blv_list = (data.get("response") or {}).get("blv") or []
            result   = []
            for blv in blv_list:
                key  = blv.get("blv_key","")
                name = blv.get("blv_name") or BLV_MAP.get(key,key)
                hd   = blv.get("link_stream_hd","")
                sd   = blv.get("link_stream_sd","")
                if hd or sd:
                    result.append({"blv_key":key,"blv_name":name,"url_hd":hd,"url_sd":sd})
                    log(f"     🎙 {name}: {(hd or sd)[-45:]}")
            return result
        except Exception as e:
            log(f"     ⚠ stream {attempt+1}: {e}")
            if attempt < 2: time.sleep(2)
    return []

# ─── Thumbnail WEBP (scale +15%) ──────────────────────────────
def make_thumbnail_bytes(home_name: str, away_name: str,
                         logo_a: "Image|None", logo_b: "Image|None",
                         time_str: str, date_str: str, league: str,
                         status: str = "upcoming", score: str = "",
                         live_time: str = "") -> bytes:
    """
    Tạo thumbnail 800×450 WEBP.
    Tất cả kích thước scale +15% so với v8:
      LOGO_SZ: 130→149, font giải: 17→19, font giờ: 36→41,
      font tên: 17→19, INFO_HALF: 100→115
    Layout (không đè):
      Y=112 : Tên giải + đường kẻ
      Y=205 : Logo A | Giờ/VS | Logo B  (cùng mức)
      Y=299 : Tên đội
      Y=331 : 📅 Ngày
    """
    W, H = 800, 450

    # Kích thước scale +15%
    LOGO_SZ   = sc(130)   # 149
    LOGO_CY   = 205
    NAME_Y    = LOGO_CY + LOGO_SZ//2 + sc(18)  # 205+74+20=299
    DATE_Y    = NAME_Y + sc(28)                 # 299+32=331
    LEAGUE_Y  = 112
    MID_X     = W // 2
    INFO_HALF = sc(100)   # 115
    GAP       = sc(12)    # 13
    LX = MID_X - INFO_HALF - GAP - LOGO_SZ//2
    RX = MID_X + INFO_HALF + GAP + LOGO_SZ//2

    # ── Nền sáng (trắng → xanh nhạt) ────────────────────────
    img  = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        draw.line([(0,y),(W,y)], fill=(int(252-8*t), int(254-6*t), 255))

    # Dải cam giovang
    draw.rectangle([(0,0),(W,8)],   fill=(255,140,0))
    draw.rectangle([(0,H-8),(W,H)], fill=(255,140,0))

    # ── Tên giải + đường kẻ (scale +15%) ─────────────────────
    if league:
        draw.text((MID_X, LEAGUE_Y), league[:26],
                  fill=(55,80,160), font=_f(sc(17), False), anchor="mm")
        line_len = sc(95)   # 109
        draw.line(
            [(MID_X-line_len, LEAGUE_Y+sc(14)),
             (MID_X+line_len, LEAGUE_Y+sc(14))],
            fill=(195,210,235), width=2
        )

    # ── Giờ / Tỉ số (scale +15%) ─────────────────────────────
    if status == "live" and score:
        main_txt, main_col = score.replace("-"," : "), (190,20,20)
        sub_txt  = (f"● {live_time}'" if live_time and live_time not in ("","0")
                    else "● LIVE")
        sub_col  = (190, 20, 20)
    elif status == "live":
        main_txt, main_col = "LIVE", (190,20,20)
        sub_txt  = f"● {live_time}'" if live_time and live_time not in ("","0") else "●"
        sub_col  = (190, 20, 20)
    else:
        main_txt, main_col = (time_str or "VS"), (20,45,130)
        sub_txt  = "VS" if time_str else ""
        sub_col  = (90, 115, 195)

    has_sub = bool(sub_txt)
    draw.text((MID_X, LOGO_CY - (sc(13) if has_sub else 0)),
              main_txt, fill=main_col, font=_f(sc(36)), anchor="mm")  # 41pt
    if has_sub:
        draw.text((MID_X, LOGO_CY + sc(24)),
                  sub_txt, fill=sub_col, font=_f(sc(14), False), anchor="mm")  # 16pt

    # ── Logo ─────────────────────────────────────────────────
    def paste_logo(cx, cy, logo_img, name, col):
        nonlocal img, draw
        if logo_img:
            logo_img = logo_img.convert("RGBA")
            logo_img.thumbnail((LOGO_SZ, LOGO_SZ), Image.LANCZOS)
            canvas = Image.new("RGBA", (LOGO_SZ, LOGO_SZ), (0,0,0,0))
            ox = (LOGO_SZ - logo_img.width)  // 2
            oy = (LOGO_SZ - logo_img.height) // 2
            canvas.paste(logo_img, (ox,oy), logo_img)
            base = img.convert("RGBA")
            base.paste(canvas, (cx-LOGO_SZ//2, cy-LOGO_SZ//2), canvas)
            img  = base.convert("RGB")
            draw = ImageDraw.Draw(img)
        else:
            r2 = LOGO_SZ//2 - 4
            draw.ellipse([(cx-r2+3,cy-r2+3),(cx+r2+3,cy+r2+3)], fill=(185,195,220))
            draw.ellipse([(cx-r2, cy-r2),   (cx+r2,   cy+r2)], fill=col)
            init = "".join(w[0].upper() for w in name.split()[:2]) or "?"
            draw.text((cx,cy), init, fill="white", font=_f(sc(34)), anchor="mm")  # 39pt

    paste_logo(LX, LOGO_CY, logo_a, home_name, (25,70,175))
    paste_logo(RX, LOGO_CY, logo_b, away_name, (175,30,55))

    # ── Tên đội (scale +15%) ─────────────────────────────────
    def draw_name(cx, name):
        words = name.split(); col = (25,50,125)
        if len(name) <= 12 or len(words) <= 1:
            draw.text((cx, NAME_Y), name[:16], fill=col,
                      font=_f(sc(17)), anchor="mm")          # 19pt
        else:
            mid = max(1, len(words)//2)
            draw.text((cx, NAME_Y - sc(9)), " ".join(words[:mid])[:16],
                      fill=col, font=_f(sc(15)), anchor="mm")  # 17pt
            draw.text((cx, NAME_Y + sc(9)), " ".join(words[mid:])[:16],
                      fill=col, font=_f(sc(13), False), anchor="mm")  # 14pt

    draw_name(LX, home_name)
    draw_name(RX, away_name)

    # ── Ngày thi đấu (scale +15%) ─────────────────────────────
    if date_str:
        draw.text((MID_X, DATE_Y), f"📅  {date_str}",
                  fill=(75,100,170), font=_f(sc(14), False), anchor="mm")  # 16pt

    # Watermark
    draw.text((W-12, H-14), "giovang.vin",
              fill=(155,170,205), font=_f(10, False), anchor="rm")

    out = io.BytesIO()
    img.save(out, format="WEBP", quality=83, method=4)
    return out.getvalue()


def thumb_filename(fid: str) -> str:
    """Tên file WEBP cho mỗi trận (theo fixture ID)."""
    return f"thumb_{fid}.webp"


def get_thumb_url(fid: str, cdn: "GitHubCDN | None",
                  webp_bytes: bytes, no_cdn: bool) -> str:
    """
    Nếu có CDN: lưu file local + upload GitHub → trả về raw URL.
    Nếu không có CDN (--no-cdn): trả về data:image/webp;base64,...
    """
    if no_cdn or cdn is None:
        b64 = base64.b64encode(webp_bytes).decode()
        return f"data:image/webp;base64,{b64}"

    fname = thumb_filename(fid)

    # Lưu local
    Path(THUMB_DIR).mkdir(exist_ok=True)
    local_path = Path(THUMB_DIR) / fname
    local_path.write_bytes(webp_bytes)

    # Upload GitHub
    try:
        url = cdn.upload(fname, webp_bytes, subdir=THUMB_DIR)
        log(f"        📤 CDN: {url[-60:]}")
        return url
    except Exception as e:
        log(f"        ⚠ CDN upload lỗi: {e} — dùng base64")
        b64 = base64.b64encode(webp_bytes).decode()
        return f"data:image/webp;base64,{b64}"

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

def build_sources(ch_id: str, streams: list[dict], detail_url: str) -> list[dict]:
    """
    1 source → 1 content → N streams (N = số BLV).
    Mỗi stream: name=tên BLV, stream_links=[HD, SD].
    """
    stream_list = []
    for i, s in enumerate(streams):
        links = []
        if s.get("url_hd"):
            links.append({
                "id": make_id(ch_id,f"l{i}hd"), "name":"HD", "type":"hls",
                "default": True, "url": s["url_hd"],
                "request_headers":[
                    {"key":"Referer",    "value": BASE_URL+"/"},
                    {"key":"User-Agent", "value": CHROME_UA},
                ],
            })
        if s.get("url_sd"):
            links.append({
                "id": make_id(ch_id,f"l{i}sd"), "name":"SD", "type":"hls",
                "default": not bool(s.get("url_hd")), "url": s["url_sd"],
                "request_headers":[
                    {"key":"Referer",    "value": BASE_URL+"/"},
                    {"key":"User-Agent", "value": CHROME_UA},
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
            "id": make_id(ch_id,"st0"), "name":"Trực tiếp",
            "stream_links":[{
                "id": make_id(ch_id,"l0"), "name":"Xem trực tiếp",
                "type":"iframe", "default":True, "url": detail_url,
                "request_headers":[
                    {"key":"Referer","value":BASE_URL+"/"},
                    {"key":"User-Agent","value":CHROME_UA},
                ],
            }],
        }]

    return [{
        "id":   make_id(ch_id,"src0"),
        "name": "GioVang Live",
        "contents": [{
            "id":      make_id(ch_id,"ct0"),
            "name":    "Trực tiếp",
            "streams": stream_list,
        }],
    }]

def build_channel(m: dict, streams: list[dict], thumb_url: str, idx: int) -> dict:
    ch_id     = make_id("gv", str(idx), slugify(m["base_title"])[:24])
    title     = build_title(m)
    league    = m["league"]
    score     = m["score"]
    blv_names = m.get("blv_names", [])
    multi_blv = len(streams) > 1

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

    img_obj = {
        "padding":          0,
        "background_color": "#F5F8FF",
        "display":          "contain",
        "url":              thumb_url,    # CDN URL hoặc base64
        "width":            800,
        "height":           450,
    }

    sources = build_sources(ch_id, streams, m["detail_url"])

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
    ap = argparse.ArgumentParser(description="Crawler giovang.vin v9 — CDN WEBP")
    ap.add_argument("--no-stream", action="store_true", help="Không lấy stream")
    ap.add_argument("--all",       action="store_true", help="Toàn bộ trận")
    ap.add_argument("--no-cdn",    action="store_true",
                    help="Dùng base64 thay CDN (không cần GitHub token)")
    ap.add_argument("--output",    default=OUTPUT_FILE)
    args = ap.parse_args()

    log("\n" + "═"*62)
    log("  🏟  CRAWLER giovang.vin  v9.0")
    log("  🖼  Thumbnail: CDN WEBP URL (upload GitHub)")
    log("  📐  Scale +15%: logo, tên, giải, giờ/ngày")
    log("═"*62 + "\n")

    now_str = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M ICT")
    sc_ = make_scraper()
    init_session(sc_)

    # ── Setup CDN ─────────────────────────────────────────────
    cdn: "GitHubCDN | None" = None
    if not args.no_cdn:
        cfg = load_github_config()
        if cfg.get("github_token") and cfg.get("github_username"):
            cdn = GitHubCDN(
                token    = cfg["github_token"],
                username = cfg["github_username"],
                repo     = cfg["github_repo"],
                branch   = cfg.get("github_branch","main"),
            )
            log(f"  📦 CDN: github.com/{cfg['github_username']}/{cfg['github_repo']}")
        else:
            log("  ⚠ Không có GitHub config → dùng base64")
            log("     Tạo giovang_config.json với github_token, github_username, github_repo")
            log("     Hoặc chạy với --no-cdn")

    # ── Fetch matches ─────────────────────────────────────────
    matches = fetch_matches(sc_, only_hot=not args.all)
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
                hw, aw = fetch_wp_logos(sc_, m["id"])
                la = (hw or {}).get("logo",""); lb = (aw or {}).get("logo","")
                if la: m["logo_a"] = la
                if lb: m["logo_b"] = lb
                if m.get("logo_a"): log(f"        A ✓ {m['logo_a'][-45:]}")
                if m.get("logo_b"): log(f"        B ✓ {m['logo_b'][-45:]}")
            except Exception as e:
                log(f"        ⚠ WP AJAX: {e}")

            try:
                if m.get("logo_a"): logo_a_img = _dl_logo(m["logo_a"], sc_)
                if m.get("logo_b"): logo_b_img = _dl_logo(m["logo_b"], sc_)
                log(f"        dl: A={'✓' if logo_a_img else '✗'} B={'✓' if logo_b_img else '✗'}")
            except Exception as e:
                log(f"        ⚠ Logo dl: {e}")

            try:
                log("        🎬 Stream API...")
                streams = fetch_streams(sc_, m["id"])
                log(f"        → {len(streams)} BLV")
            except Exception as e:
                log(f"        ⚠ Stream: {e}"); streams = []

            time.sleep(0.4)

        # Tạo thumbnail WEBP
        try:
            webp_bytes = make_thumbnail_bytes(
                m["home_team"], m["away_team"],
                logo_a_img, logo_b_img,
                m["time_str"], m["date_str"], m["league"],
                m["status"], m["score"], m["live_time"],
            )
            thumb_url = get_thumb_url(m["id"], cdn, webp_bytes, args.no_cdn)
            is_cdn    = thumb_url.startswith("http")
            log(f"        🖼  WEBP {len(webp_bytes):,}B → {'CDN URL' if is_cdn else f'base64 {len(thumb_url):,}c'}")
        except Exception as e:
            log(f"        ⚠ Thumbnail: {e}"); thumb_url = SITE_ICON

        channels.append(build_channel(m, streams, thumb_url, i))

    result = build_iptv_json(channels, now_str)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # Thống kê dung lượng
    json_size = Path(args.output).stat().st_size
    live_n = sum(1 for m in matches if m["status"]=="live")
    up_n   = sum(1 for m in matches if m["status"]=="upcoming")

    log(f"\n{'═'*62}")
    log(f"  ✅  Xong!  →  {args.output}")
    log(f"  📦  JSON size: {json_size:,} bytes ({json_size//1024} KB)")
    log(f"  📊  {len(channels)} trận | 🔴 {live_n} | 🕐 {up_n} | {now_str}")
    log("═"*62 + "\n")


if __name__ == "__main__":
    main()
