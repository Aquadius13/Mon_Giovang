#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   AUTO UPDATER — giovang.vin → GitHub  v1.0                 ║
║   Tự động crawl → push JSON lên GitHub cứ 30 phút/lần      ║
╚══════════════════════════════════════════════════════════════╝

Cách dùng:
    # Cấu hình lần đầu:
    python auto_updater_giovang.py --setup

    # Chạy 1 lần (cập nhật ngay):
    python auto_updater_giovang.py --once

    # Chạy liên tục, cứ 30 phút cập nhật 1 lần:
    python auto_updater_giovang.py --interval 1800

    # Dùng GitHub Actions (--once, env GITHUB_TOKEN):
    python auto_updater_giovang.py --once

Cài đặt:
    pip install cloudscraper beautifulsoup4 lxml requests pillow
"""

import argparse, hashlib, json, os, signal, subprocess, sys, threading, time
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────
CONFIG_FILE = "giovang_config.json"

DEFAULT_CONFIG = {
    # ── GitHub ────────────────────────────────────────────────
    "github_token":    "",          # Personal Access Token (repo scope)
    "github_username": "",          # GitHub username
    "github_repo":     "giovang-iptv",  # Tên repo
    "github_branch":   "main",
    "github_pages":    False,       # True = dùng GitHub Pages URL

    # ── File JSON trong repo ───────────────────────────────────
    "json_filename":   "giovang.json",

    # ── Crawler ───────────────────────────────────────────────
    "crawler_script":  "crawler_giovang.py",
    "crawler_no_stream": False,     # True = không crawl stream (nhanh hơn)
    "local_json":      "giovang_iptv.json",

    # ── Schedule ──────────────────────────────────────────────
    "interval_seconds": 1800,       # Cập nhật mỗi 30 phút
    "skip_if_no_change": True,
}


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    if Path(CONFIG_FILE).exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg.update(json.load(f))
    # Override từ env vars (dùng trong GitHub Actions)
    env_map = {
        "GITHUB_TOKEN":    "github_token",
        "GITHUB_USERNAME": "github_username",
        "GITHUB_REPO":     "github_repo",
        "JSON_FILENAME":   "json_filename",
        "GITHUB_PAGES":    "github_pages",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            if cfg_key == "github_pages":
                cfg[cfg_key] = val.lower() in ("true", "1", "yes")
            else:
                cfg[cfg_key] = val
    return cfg


def save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"  💾 Đã lưu cấu hình: {CONFIG_FILE}")


# ── GitHub Publisher ──────────────────────────────────────────
class GitHubPublisher:
    """Đẩy file lên GitHub repo qua API."""

    def __init__(self, token: str, username: str, repo: str,
                 branch: str = "main", use_pages: bool = False):
        self.token     = token
        self.username  = username
        self.repo      = repo
        self.branch    = branch
        self.use_pages = use_pages
        self.api_base  = "https://api.github.com"
        self.headers   = {
            "Authorization": f"Bearer {token}",
            "Accept":        "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        import requests as _req
        self._req = _req

    def _url(self, path: str) -> str:
        return f"{self.api_base}{path}"

    def test_token(self) -> dict:
        r = self._req.get(self._url("/user"), headers=self.headers, timeout=15)
        r.raise_for_status()
        return r.json()

    def ensure_repo(self, description: str = "") -> bool:
        """Kiểm tra/tạo repo. Trả về True nếu vừa tạo mới."""
        r = self._req.get(
            self._url(f"/repos/{self.username}/{self.repo}"),
            headers=self.headers, timeout=15
        )
        if r.status_code == 200:
            return False
        if r.status_code == 404:
            payload = {
                "name":        self.repo,
                "description": description,
                "private":     False,
                "auto_init":   True,
            }
            r2 = self._req.post(
                self._url("/user/repos"),
                headers=self.headers,
                json=payload, timeout=15
            )
            r2.raise_for_status()
            time.sleep(3)
            return True
        r.raise_for_status()
        return False

    def enable_pages(self) -> str:
        """Bật GitHub Pages từ branch main."""
        payload = {"source": {"branch": self.branch, "path": "/"}}
        r = self._req.post(
            self._url(f"/repos/{self.username}/{self.repo}/pages"),
            headers=self.headers, json=payload, timeout=15
        )
        if r.status_code in (201, 409):
            return f"https://{self.username}.github.io/{self.repo}/"
        return ""

    def repo_url(self) -> str:
        return f"https://github.com/{self.username}/{self.repo}"

    def raw_url(self, filename: str) -> str:
        if self.use_pages:
            return f"https://{self.username}.github.io/{self.repo}/{filename}"
        return f"https://raw.githubusercontent.com/{self.username}/{self.repo}/{self.branch}/{filename}"

    def upload(self, filename: str, content: bytes, message: str) -> str:
        """Upload/update file lên repo. Trả về raw URL."""
        import base64 as _b64
        encoded = _b64.b64encode(content).decode("ascii")
        url     = self._url(f"/repos/{self.username}/{self.repo}/contents/{filename}")

        # Lấy SHA nếu file đã tồn tại
        r = self._req.get(url, headers=self.headers,
                          params={"ref": self.branch}, timeout=15)
        sha = r.json().get("sha", "") if r.status_code == 200 else ""

        payload: dict = {
            "message": message,
            "content": encoded,
            "branch":  self.branch,
        }
        if sha:
            payload["sha"] = sha

        r2 = self._req.put(url, headers=self.headers, json=payload, timeout=30)
        r2.raise_for_status()
        return self.raw_url(filename)


# ── Setup Wizard ──────────────────────────────────────────────
def setup_wizard() -> dict:
    print("\n" + "═" * 60)
    print("  🔧  SETUP — Cấu hình GitHub Publisher (GioVang)")
    print("═" * 60)
    print()
    print("  Cần tạo GitHub Personal Access Token:")
    print("  → https://github.com/settings/tokens")
    print("  → Chọn: repo (Full control of private repositories)")
    print()

    cfg = load_config()

    def ask(prompt, key, default=None):
        cur = cfg.get(key) or default or ""
        val = input(f"  {prompt} [{cur}]: ").strip()
        return val if val else cur

    cfg["github_token"]    = ask("GitHub Token (ghp_...)", "github_token")
    cfg["github_username"] = ask("GitHub Username",         "github_username")
    cfg["github_repo"]     = ask("Tên repo",                "github_repo", "giovang-iptv")
    cfg["json_filename"]   = ask("Tên file JSON",           "json_filename", "giovang.json")

    pages_in = input("  Bật GitHub Pages? [y/N]: ").strip().lower()
    cfg["github_pages"] = pages_in in ("y", "yes")

    ns = input("  Crawl stream? (n = nhanh hơn nhưng không có link) [y/N]: ").strip().lower()
    cfg["crawler_no_stream"] = ns not in ("y", "yes")

    save_config(cfg)

    print()
    print("  🔍 Kiểm tra token...")
    try:
        pub  = GitHubPublisher(cfg["github_token"], cfg["github_username"],
                               cfg["github_repo"], cfg["github_branch"], cfg["github_pages"])
        user = pub.test_token()
        print(f"  ✅ Token OK — Xin chào {user.get('login','?')}!")
    except Exception as e:
        print(f"  ❌ Token lỗi: {e}")
        return cfg

    print()
    print("  📦 Khởi tạo repository...")
    try:
        created = pub.ensure_repo("GioVang IPTV — Live Sports JSON")
        print(f"  {'✅ Đã tạo repo' if created else 'ℹ Repo đã tồn tại'}: {pub.repo_url()}")
        if cfg["github_pages"]:
            pages_url = pub.enable_pages()
            print(f"  🌐 Pages URL: {pages_url}")
    except Exception as e:
        print(f"  ❌ Lỗi repo: {e}")

    raw_url = pub.raw_url(cfg["json_filename"])
    print()
    print("  ── URL THÊM VÀO MONPLAYER / IPTV APP ────────────────")
    print(f"  👉 {raw_url}")
    print()
    print("═" * 60 + "\n")

    cfg["_public_url"] = raw_url
    save_config(cfg)
    return cfg


# ── Crawler runner ────────────────────────────────────────────
def run_crawler(cfg: dict) -> bool:
    script = cfg.get("crawler_script", "crawler_giovang.py")

    candidates = [script, "crawler_giovang.py"]
    crawler_path = None
    for name in candidates:
        if Path(name).exists():
            crawler_path = name
            break

    if not crawler_path:
        log(f"❌ Không tìm thấy script: {script}")
        return False

    cmd = [sys.executable, crawler_path, "--output", cfg["local_json"]]
    if cfg.get("crawler_no_stream"):
        cmd.append("--no-stream")

    log(f"🔄 Chạy crawler: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, timeout=180)
        if result.returncode != 0:
            log(f"⚠ Crawler thoát code {result.returncode}")
            return False
        return True
    except subprocess.TimeoutExpired:
        log("❌ Crawler timeout (>180s)")
        return False
    except Exception as e:
        log(f"❌ Crawler lỗi: {e}")
        return False


# ── Push to GitHub ────────────────────────────────────────────
def push_to_github(cfg: dict, pub: GitHubPublisher, last_hash: list) -> tuple[bool, str]:
    local_path = cfg["local_json"]
    if not Path(local_path).exists():
        log(f"❌ File không tồn tại: {local_path}")
        return False, pub.raw_url(cfg["json_filename"])

    content  = Path(local_path).read_bytes()
    new_hash = hashlib.md5(content).hexdigest()

    if cfg.get("skip_if_no_change") and new_hash == last_hash[0]:
        log("  ℹ Không có thay đổi — bỏ qua push")
        return False, pub.raw_url(cfg["json_filename"])

    try:
        data     = json.loads(content)
        channels = sum(len(g.get("channels", [])) for g in data.get("groups", []))
    except Exception:
        channels = 0

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"Update {cfg['json_filename']} — {channels} trận — {now_str}"

    log(f"📤 Push lên GitHub: {cfg['json_filename']} ({channels} trận)...")
    try:
        raw_url        = pub.upload(cfg["json_filename"], content, message)
        last_hash[0]   = new_hash
        log(f"  ✅ Thành công → {raw_url}")
        return True, raw_url
    except Exception as e:
        log(f"  ❌ Push thất bại: {e}")
        return False, ""


# ── Update cycle ──────────────────────────────────────────────
def do_update_cycle(cfg: dict, pub: GitHubPublisher, last_hash: list, stats: dict) -> str:
    stats["cycles"] += 1
    log(f"\n{'═' * 55}")
    log(f"  🔄 Chu kỳ #{stats['cycles']} — {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    log(f"{'═' * 55}")

    ok = run_crawler(cfg)
    if not ok:
        stats["errors"] += 1
        log(f"  ❌ Crawler thất bại ({stats['errors']} lỗi)")
        return pub.raw_url(cfg["json_filename"])

    changed, raw_url = push_to_github(cfg, pub, last_hash)
    if changed:
        stats["pushes"]   += 1
        stats["last_push"] = datetime.now().strftime("%H:%M:%S")
        stats["errors"]    = 0
    else:
        stats["skipped"] += 1

    log(f"  📊 {stats['pushes']} push | {stats['skipped']} bỏ qua | {stats['errors']} lỗi")
    return raw_url


# ── Main ──────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Auto-updater: GioVang → GitHub JSON")
    ap.add_argument("--setup",    action="store_true", help="Cấu hình lần đầu")
    ap.add_argument("--once",     action="store_true", help="Chạy 1 lần rồi thoát")
    ap.add_argument("--interval", type=int, default=None, help="Giây giữa các lần cập nhật")
    ap.add_argument("--no-crawl", action="store_true", help="Chỉ push, không crawl")
    args = ap.parse_args()

    print("\n" + "═" * 60)
    print("  📡  GIOVANG AUTO UPDATER — giovang.vin → GitHub")
    print("═" * 60 + "\n")

    if args.setup:
        setup_wizard()
        return

    cfg = load_config()
    if not cfg.get("github_token") or not cfg.get("github_username"):
        print("❌ Chưa có cấu hình. Chạy: python auto_updater_giovang.py --setup")
        sys.exit(1)

    if args.interval:
        cfg["interval_seconds"] = args.interval

    pub = GitHubPublisher(
        token     = cfg["github_token"],
        username  = cfg["github_username"],
        repo      = cfg["github_repo"],
        branch    = cfg.get("github_branch", "main"),
        use_pages = cfg.get("github_pages", False),
    )

    log("🔍 Kiểm tra repository...")
    try:
        pub.ensure_repo("GioVang IPTV — Live Sports JSON")
    except Exception as e:
        print(f"❌ Lỗi GitHub: {e}")
        sys.exit(1)

    raw_url = pub.raw_url(cfg["json_filename"])

    print()
    print("  ── THÔNG TIN ─────────────────────────────────────────")
    print(f"  📁 Repo      : {pub.repo_url()}")
    print(f"  🌐 Raw URL   : {raw_url}")
    print(f"  ⏱ Interval  : {cfg['interval_seconds']}s ({cfg['interval_seconds']//60} phút)")
    print()
    print("  ── URL THÊM VÀO IPTV APP ─────────────────────────────")
    print(f"  👉 {raw_url}")
    print()

    if args.no_crawl:
        _this = sys.modules[__name__]
        _this.run_crawler = lambda cfg: (
            log("  ⏭ --no-crawl: dùng JSON local") or Path(cfg["local_json"]).exists()
        )

    last_hash = [""]
    stats     = {"cycles": 0, "pushes": 0, "skipped": 0, "errors": 0, "last_push": "-"}

    stop_event = threading.Event()
    def _signal_handler(sig, frame):
        print(f"\n\n  🛑 Dừng... (Ctrl+C)")
        print(f"  📊 {stats['cycles']} chu kỳ | {stats['pushes']} push | lần cuối: {stats['last_push']}")
        stop_event.set()
    signal.signal(signal.SIGINT, _signal_handler)

    if args.once:
        do_update_cycle(cfg, pub, last_hash, stats)
        print(f"\n  ✅ Hoàn tất.")
        print(f"  🌐 URL: {raw_url}")
    else:
        log(f"🚀 Bắt đầu vòng lặp — cứ {cfg['interval_seconds']}s cập nhật 1 lần")
        log(f"   Ctrl+C để dừng\n")
        while not stop_event.is_set():
            do_update_cycle(cfg, pub, last_hash, stats)
            interval = cfg["interval_seconds"]
            log(f"  💤 Chờ {interval}s ({interval//60} phút)...")
            for _ in range(interval):
                if stop_event.is_set():
                    break
                time.sleep(1)


if __name__ == "__main__":
    main()
