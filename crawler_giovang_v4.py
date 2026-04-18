#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   CRAWLER giovang.vin  v4.0  —  HOÀN CHỈNH                 ║
║                                                              ║
║   Luồng hoạt động:                                          ║
║   1. Fetch live.json + all.json → danh sách trận Hot Match  ║
║   2. WP AJAX load_live_stream   → logo, tên đội, data detail║
║   3. keovip88 /api/fixtures/ID  → link_stream_hd / sd (HLS) ║
║   4. Build IPTV JSON với 2 logo đội bóng                    ║
╚══════════════════════════════════════════════════════════════╝

Cài:  pip install cloudscraper requests
Chạy:
    python crawler_giovang.py                  # đầy đủ
    python crawler_giovang.py --no-stream      # không lấy stream
    python crawler_giovang.py --all            # toàn bộ trận
    python crawler_giovang.py --output out.json
"""

import argparse, hashlib, json, re, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import cloudscraper, requests
except ImportError:
    print("Cài: pip install cloudscraper requests")
    sys.exit(1)

# ─── Constants ────────────────────────────────────────────────
BASE_URL       = "https://giovang.vin"
API_LIVE       = "https://live-api.keovip88.net/storage/livestream/live.json"
API_ALL        = "https://live-api.keovip88.net/storage/livestream/all.json"
API_STREAM     = "https://live-api.keovip88.net/api/fixtures/{fid}"   # → blv[].link_stream_hd/sd
WP_AJAX        = "https://giovang.vin/wp-admin/admin-ajax.php"
OUTPUT_FILE    = "giovang_iptv.json"
VN_TZ          = timezone(timedelta(hours=7))
CHROME_UA      = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_LOGO   = "https://giovang.vin/wp-content/uploads/2025/01/logo.png"

# Status codes
LIVE_CODES     = {"1H", "2H", "HT", "PEN", "ET", "BT", "LIVE", "INT", "SUSP", "P"}
FINISHED_CODES = {"FT", "AET", "AWD", "WO", "ABD", "CANC"}

# BLV map
BLV_MAP = {
    "nha-dai": "Nhà Đài", "blv-tho": "BLV Thỏ", "blv-perry": "BLV Perry",
    "blv-1":   "BLV Tí",  "blv-3":   "BLV Dần", "blv-5":    "BLV Thìn",
    "blv-6":   "BLV Tỵ",  "blv-10":  "BLV Dậu", "blv-12":   "BLV Hợi",
    "blv-tom": "BLV Tôm", "blv-ben": "BLV Ben",  "blv-cay":  "BLV Cầy",
    "blv-bang":"BLV Băng","blv-mason":"BLV Mason","blv-che":  "BLV Chè",
    "blv-cam": "BLV Câm", "blv-dory":"BLV Dory", "blv-chanh":"BLV Chanh",
    "blv-nen": "BLV Nến",
}

def log(msg: str): print(msg, flush=True)

# ─── HTTP client ──────────────────────────────────────────────
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

def get_json(url: str, sc, label: str = "", params: dict = None) -> dict:
    for i in range(3):
        try:
            r = sc.get(url, timeout=20, params=params)
            r.raise_for_status()
            data = r.json()
            n = len(data.get("response", [])) if isinstance(data, dict) else "?"
            log(f"  ✓ {label or url.split('/')[-1]}  →  {n} items")
            return data
        except Exception as e:
            if i < 2: time.sleep(2 ** i)
    return {}

def post_ajax(sc, data: dict) -> dict:
    for i in range(3):
        try:
            r = sc.post(WP_AJAX, data=data, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i < 2: time.sleep(2 ** i)
    return {}

# ─── Slug builder (port từ JS giovang.vin) ────────────────────
_FROM = "àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđç·/_,:;"
_TO   = "aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooooouuuuuuuuuuuyyyyyddc------"

def slugify(s: str) -> str:
    s = s.strip().lower()
    for f, t in zip(_FROM, _TO):
        s = s.replace(f, t)
    s = re.sub(r"[^a-z0-9 -]", "", s)
    s = re.sub(r"\s+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")

def build_detail_url(home: str, away: str, day_month: str, fid: str) -> str:
    raw  = f"truc tiep {home} vs {away}-{day_month}--{fid}"
    return f"{BASE_URL}/{slugify(raw)}/"

# ─── Parse giờ từ API ─────────────────────────────────────────
def parse_time(f: dict) -> tuple[str, str, str]:
    """
    API trả về:
      f['time']      = "HH:MM"  (giờ địa phương GMT+7)
      f['day_month'] = "DD/MM"
      f['date']      = "DD-MM-YYYY"
    Trả về (time_str, date_str, sort_key).
    """
    raw_t = f.get("time", "")
    dm    = f.get("day_month", "")
    raw_d = f.get("date", "")

    mt = re.match(r"(\d{1,2}):(\d{2})", raw_t)
    time_str = f"{int(mt.group(1)):02d}:{mt.group(2)}" if mt else ""
    date_str = dm

    sort_key = ""
    if raw_d and mt:
        p = re.split(r"[-/]", raw_d)
        if len(p) == 3:
            try:
                dd, mm, yy = int(p[0]), int(p[1]), int(p[2])
                hh, mn     = int(mt.group(1)), int(mt.group(2))
                sort_key   = f"{yy}{mm:02d}{dd:02d}{hh:02d}{mn:02d}"
            except Exception:
                pass
    return time_str, date_str, sort_key

# ─── Status ───────────────────────────────────────────────────
def get_status(sc: str) -> str:
    if sc in LIVE_CODES:     return "live"
    if sc in FINISHED_CODES: return "finished"
    return "upcoming"

# ─── Parse fixture từ live/all API ───────────────────────────
def parse_fixture(f: dict) -> dict:
    fid    = str(f.get("id", ""))
    home   = f.get("teams", {}).get("home", {})
    away   = f.get("teams", {}).get("away", {})
    league = f.get("league", {}) or {}
    goals  = f.get("goals", {}) or {}
    blvs   = f.get("blv", []) or []
    sc     = f.get("status_code", "NS")

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
        "detail_url":  build_detail_url(home_name, away_name,
                                        f.get("day_month", ""), fid),
        "blv_keys":    [b for b in blvs if b != "nha-dai"],
        "blv_names":   blv_names,
        "is_hot":      bool(f.get("is_hot")),
        "is_hot_top":  bool(f.get("is_hot_top")),
        "sport_type":  f.get("type", "football"),
        "thumbnail":   "",
    }

# ─── Step 1: Fetch danh sách trận từ API ──────────────────────
def fetch_matches(sc, only_hot: bool) -> list[dict]:
    log("\n📡 Fetch live.json + all.json...")
    live_data = get_json(API_LIVE, sc, "live.json",
                         params={"t": int(time.time() * 1000)})
    all_data  = get_json(API_ALL,  sc, "all.json",
                         params={"t": int(time.time() * 1000)})

    matches: list[dict] = []
    seen:    set[str]   = set()

    # Đang diễn ra (từ live.json)
    for f in (live_data.get("response") or []):
        if get_status(f.get("status_code", "NS")) == "finished":
            continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")):
            continue
        m = parse_fixture(f)
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"])
            matches.append(m)
            log(f"  🔴 {m['status_code']:3s} | {m['base_title']} | {m['league']} | {m['time_str']}")

    # Sắp diễn ra (từ all.json, status NS)
    for f in (all_data.get("response") or []):
        if f.get("status_code", "NS") != "NS":
            continue
        if only_hot and not (f.get("is_hot") or f.get("is_hot_top")):
            continue
        m = parse_fixture(f)
        if m["id"] and m["id"] not in seen:
            seen.add(m["id"])
            matches.append(m)
            log(f"  🕐 NS  | {m['base_title']} | {m['league']} | {m['time_str']}")

    prio = {"live": 0, "upcoming": 1, "finished": 2}
    matches.sort(key=lambda x: (prio.get(x["status"], 9), x["sort_key"]))

    live_n = sum(1 for m in matches if m["status"] == "live")
    up_n   = sum(1 for m in matches if m["status"] == "upcoming")
    log(f"\n  ✅ {len(matches)} trận — 🔴 {live_n} live | 🕐 {up_n} sắp tới")
    return matches

# ─── Step 2: WP AJAX load_live_stream → logo + detail ────────
def fetch_wp_detail(sc, fid: str) -> dict:
    """
    POST admin-ajax.php action=load_live_stream&id={fid}
    → { success, data: { id, home:{name,logo}, away:{name,logo},
                          date, data_detail:{league,type,time,status_code,...} } }
    """
    resp = post_ajax(sc, {"action": "load_live_stream", "id": fid})
    if not resp.get("success"):
        return {}
    return resp.get("data", {})

# ─── Step 3: Stream API → link_stream_hd / link_stream_sd ─────
def fetch_streams(sc, fid: str) -> list[dict]:
    """
    GET https://live-api.keovip88.net/api/fixtures/{fid}
    → { success, data: { response: { is_live, blv: [{blv_key, blv_name,
                                                      link_stream_hd,
                                                      link_stream_sd}] } } }
    Trả về list stream dicts: [{name, url, type, referer}]
    """
    url  = API_STREAM.format(fid=fid)
    data = get_json(url, sc, f"stream/{fid[:8]}")
    if not data.get("success"):
        return []

    resp   = (data.get("data") or {}).get("response") or {}
    is_live = resp.get("is_live", False)
    blv_list = resp.get("blv") or []

    if not is_live or not blv_list:
        return []

    streams = []
    for blv in blv_list:
        key  = blv.get("blv_key", "")
        name = blv.get("blv_name") or BLV_MAP.get(key, key)
        hd   = blv.get("link_stream_hd", "")
        sd   = blv.get("link_stream_sd", "")
        if hd:
            streams.append({"name": f"{name} – HD", "url": hd,
                            "type": "hls", "blv": name})
        if sd:
            streams.append({"name": f"{name} – SD", "url": sd,
                            "type": "hls", "blv": name})

    return streams

# ─── Build IPTV JSON ─────────────────────────────────────────
def make_id(*parts) -> str:
    raw    = "-".join(str(p) for p in parts if p)
    slug   = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    digest = hashlib.md5(raw.encode()).hexdigest()[:8]
    return slug[:48] + "-" + digest if len(slug) > 56 else slug

def build_title(m: dict) -> str:
    base  = m["base_title"]
    score = m["score"]
    t, d  = m["time_str"], m["date_str"]

    if m["status"] == "live":
        lt = m["live_time"]
        sfx = f" {lt}'" if lt and lt != "0" else ""
        if score:
            return f"{m['home_team']} {score} {m['away_team']}  🔴{sfx}"
        return f"{base}  🔴 LIVE{sfx}"

    if m["status"] == "finished":
        if score: return f"{m['home_team']} {score} {m['away_team']}  ✅"
        return f"{base}  ✅ KT"

    ti = ""
    if t and d: ti = f"  🕐 {t} | {d}"
    elif t:     ti = f"  🕐 {t}"
    elif d:     ti = f"  📅 {d}"
    return f"{base}{ti}"

def build_image_obj(logo_a: str, logo_b: str, fallback: str = DEFAULT_LOGO) -> dict:
    """
    Tạo image object với 2 logo đội bóng cạnh nhau.
    Dùng HTML inline base64 SVG để ghép 2 logo.
    """
    la = logo_a or fallback
    lb = logo_b or fallback

    if logo_a and logo_b:
        # SVG ghép 2 logo bên trái và bên phải
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" '
            'width="800" height="450" viewBox="0 0 800 450">'
            '<rect width="800" height="450" fill="#071a2e"/>'
            # Logo home (trái)
            f'<image href="{la}" x="80" y="125" width="200" height="200" '
            'preserveAspectRatio="xMidYMid meet"/>'
            # Logo away (phải)
            f'<image href="{lb}" x="520" y="125" width="200" height="200" '
            'preserveAspectRatio="xMidYMid meet"/>'
            # VS text giữa
            '<text x="400" y="235" text-anchor="middle" '
            'font-family="Arial,sans-serif" font-size="48" '
            'font-weight="bold" fill="white" opacity="0.9">VS</text>'
            '</svg>'
        )
        import base64
        svg_b64 = base64.b64encode(svg.encode()).decode()
        return {
            "padding":          0,
            "background_color": "#071a2e",
            "display":          "contain",
            "url":              f"data:image/svg+xml;base64,{svg_b64}",
            "width":            800,
            "height":           450,
        }

    # Fallback: chỉ 1 logo
    return {
        "padding":          4,
        "background_color": "#071a2e",
        "display":          "contain",
        "url":              la,
        "width":            800,
        "height":           450,
    }

def build_channel(m: dict, streams: list[dict], idx: int) -> dict:
    ch_id  = make_id("gv", str(idx), slugify(m["base_title"])[:24])
    title  = build_title(m)
    league = m["league"]
    score  = m["score"]

    # ── Labels ───────────────────────────────────────────────
    labels = []

    # Trạng thái top-left
    st_map = {
        "live":     ("● LIVE",        "#C62828"),
        "upcoming": ("🕐 Sắp diễn ra","#1565C0"),
        "finished": ("✅ Kết thúc",   "#424242"),
    }
    st_text, st_color = st_map.get(m["status"], ("● LIVE", "#C62828"))
    labels.append({"text": st_text, "color": st_color,
                   "text_color": "#ffffff", "position": "top-left"})

    # Giải đấu top-right
    if league:
        labels.append({"text": league[:28], "color": "#0D47A1",
                       "text_color": "#ffffff", "position": "top-right"})

    # Tỉ số live bottom-right
    if score and m["status"] == "live":
        lt  = m["live_time"]
        txt = f"{score}  {lt}'" if lt and lt != "0" else score
        labels.append({"text": txt, "color": "#B71C1C",
                       "text_color": "#ffffff", "position": "bottom-right"})

    # BLV bottom-left
    blv_names = m.get("blv_names", [])
    if blv_names:
        btxt = f"🎙 {blv_names[0]}" if len(blv_names) == 1 else f"🎙 {len(blv_names)} BLV"
        labels.append({"text": btxt, "color": "#1B5E20",
                       "text_color": "#ffffff", "position": "bottom-left"})

    # ── Stream links ─────────────────────────────────────────
    links = []
    for i, s in enumerate(streams):
        links.append({
            "id":      make_id(ch_id, f"l{i}"),
            "name":    s.get("name", f"Link {i+1}"),
            "type":    s.get("type", "hls"),
            "default": i == 0,
            "url":     s["url"],
            "request_headers": [
                {"key": "Referer",    "value": BASE_URL + "/"},
                {"key": "User-Agent", "value": CHROME_UA},
            ],
        })

    if not links:
        links.append({
            "id": make_id(ch_id, "lnk0"), "name": "Xem trực tiếp",
            "type": "iframe", "default": True,
            "url": m["detail_url"],
            "request_headers": [
                {"key": "Referer",    "value": BASE_URL + "/"},
                {"key": "User-Agent", "value": CHROME_UA},
            ],
        })

    # ── Image: 2 logo đội bóng ───────────────────────────────
    img_obj = build_image_obj(m.get("logo_a", ""), m.get("logo_b", ""))

    # ── Description ──────────────────────────────────────────
    parts = []
    if league:             parts.append(league)
    if m["time_str"]:      parts.append(m["time_str"])
    if m["date_str"]:      parts.append(m["date_str"])
    if m["status"] == "live":
        lt = m["live_time"]
        parts.append(f"🔴 LIVE{' '+lt+chr(39) if lt and lt!='0' else ''}")
        if score: parts.append(score)
    elif m["status"] == "upcoming":
        parts.append("🕐 Sắp diễn ra")
    if blv_names:          parts.append("🎙 " + ", ".join(blv_names[:3]))

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

def build_iptv_json(channels: list[dict], now_str: str) -> dict:
    n_live = sum(1 for c in channels
                 if any("LIVE" in lb["text"] for lb in c.get("labels", [])))
    return {
        "id":          "giovang-iptv",
        "name":        "GioVang TV",
        "url":         BASE_URL + "/",
        "description": (f"Trực tiếp bóng đá — {n_live} đang live, "
                        f"{len(channels)-n_live} sắp tới. Cập nhật {now_str}"),
        "disable_ads": True,
        "color":       "#f5a623",
        "grid_number": 3,
        "image": {"type": "cover", "url": DEFAULT_LOGO},
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
                    help="Không lấy stream link (nhanh hơn)")
    ap.add_argument("--all",       action="store_true",
                    help="Lấy toàn bộ trận, không chỉ Hot Match")
    ap.add_argument("--output",    default=OUTPUT_FILE)
    args = ap.parse_args()

    log("\n" + "═" * 62)
    log("  🏟  CRAWLER giovang.vin  v4.0")
    log("  📡  live.json + all.json → WP AJAX → keovip stream API")
    log("  🖼  Thumbnail = 2 logo đội bóng cạnh nhau")
    log("═" * 62 + "\n")

    now_str = datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M ICT")
    sc = make_scraper()

    # ── Bước 1: Danh sách trận ───────────────────────────────
    matches = fetch_matches(sc, only_hot=not args.all)
    if not matches:
        log("❌ Không có trận nào. Thêm --all để lấy toàn bộ.")
        sys.exit(1)

    # ── Bước 2 & 3: WP AJAX + Stream mỗi trận ───────────────
    log(f"\n🔄 Bước 2+3: Fetch detail + stream ({len(matches)} trận)...")
    channels = []
    for i, m in enumerate(matches, 1):
        log(f"\n  [{i:02d}/{len(matches):02d}] {m['base_title']}")
        log(f"        {m['status']:8s} | {m['league']:25s} | {m['time_str']} {m['date_str']}")

        streams = []

        if not args.no_stream:
            # WP AJAX → cập nhật logo chính xác từ flashscore
            log(f"        📋 WP AJAX load_live_stream...")
            wp = fetch_wp_detail(sc, m["id"])
            if wp:
                # Cập nhật logo từ WP response (chính xác hơn)
                h = wp.get("home", {})
                a = wp.get("away", {})
                if h.get("logo"): m["logo_a"] = h["logo"]
                if a.get("logo"): m["logo_b"] = a["logo"]
                # Cập nhật tên nếu chính xác hơn
                if h.get("name") and not m["home_team"]: m["home_team"] = h["name"]
                if a.get("name") and not m["away_team"]: m["away_team"] = a["name"]
                log(f"        logo_a: {m['logo_a'][-40:]}")
                log(f"        logo_b: {m['logo_b'][-40:]}")

            # Stream API → link_stream_hd / sd
            log(f"        🎬 Fetch streams...")
            streams = fetch_streams(sc, m["id"])
            log(f"        → {len(streams)} streams: {[s['name'] for s in streams]}")
            time.sleep(0.4)

        channels.append(build_channel(m, streams, i))

    # ── Bước 4: Ghi file ─────────────────────────────────────
    result = build_iptv_json(channels, now_str)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    live_n = sum(1 for m in matches if m["status"] == "live")
    up_n   = sum(1 for m in matches if m["status"] == "upcoming")

    log(f"\n{'═' * 62}")
    log(f"  ✅  Xong!  →  {args.output}")
    log(f"  📊  {len(channels)} trận | 🔴 {live_n} live | 🕐 {up_n} sắp tới | {now_str}")
    log("═" * 62 + "\n")


if __name__ == "__main__":
    main()
