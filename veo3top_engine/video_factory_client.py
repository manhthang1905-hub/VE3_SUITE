"""
video_factory_client — client cho mã (subprocess) gọi NHÀ MÁY VIDEO CHUNG (video_factory.py).
- ensure_service(): tự bật service 1 lần (singleton qua lock file) nếu chưa chạy.
- generate(image_path, prompt, out_path, aspect): POST /generate, chờ kết quả.
Nhiều mã cùng gọi -> chỉ 1 service bật (lock O_EXCL), các mã khác chờ nó lên.
"""
import os, sys, json, time, subprocess, socket
_HERE = os.path.dirname(os.path.abspath(__file__))
_SUITE = os.path.dirname(_HERE)

try:
    import requests
except Exception:
    requests = None

PORT = int(os.environ.get("VEO3TOP_POOL_PORT", "8788") or "8788")
BASE = f"http://127.0.0.1:{PORT}"
_LOCK = os.path.join(_SUITE, ".veo3top_pool_start.lock")
_PIDF = os.path.join(_SUITE, ".veo3top_pool.pid")
_TOKEN_CHROMES = int(os.environ.get("VEO3TOP_POOL_TOKEN_CHROMES", "3") or "3")  # CPU (i9 8 nhân) là trần: ảnh 6 + video 3 = 9 token chrome ~CPU 70-85%. Video async cần ít token (render song song). Tăng nếu CPU dư.


def is_up(timeout=3):
    if requests is None:
        return False
    try:
        r = requests.get(f"{BASE}/health", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


_LOGF = os.path.join(_SUITE, ".veo3top_pool.log")


def _spawn_service(log=print):
    exe = sys.executable
    # OPT-IN: VEO3TOP_VIDEO_BROWSER_POOL=1 -> dùng nhà máy video BROWSER (in-page, để dành khi video lỗi nhiều).
    # Mặc định TẮT -> giữ video_factory curl_cffi ĐANG CHẠY TỐT (không đụng đường kiếm tiền chính).
    if os.environ.get("VEO3TOP_VIDEO_BROWSER_POOL", "0") == "1":
        script = os.path.join(_HERE, "video_pool_browser.py")
        args = [exe, script, "--port", str(PORT),
                "--accounts", os.environ.get("VEO3TOP_VID_POOL_ACCOUNTS", "8")]
    else:
        script = os.path.join(_HERE, "video_factory.py")
        args = [exe, script, "--port", str(PORT), "--token-chromes", str(_TOKEN_CHROMES)]
    # Chỉnh theo máy qua settings.yaml (1 chỗ): video_token_chromes + video_workers_per_account.
    _env = os.environ.copy()
    try:
        import yaml
        _cfgp = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "tools", "ve3", "config", "settings.yaml")
        _cfg = yaml.safe_load(open(_cfgp, encoding="utf-8")) or {}
        if _cfg.get("video_token_chromes") and "--token-chromes" in args:
            args[args.index("--token-chromes") + 1] = str(int(_cfg["video_token_chromes"]))
        if _cfg.get("video_workers_per_account"):
            _env["VEO3TOP_POOL_WORKERS_PER_ACCOUNT"] = str(int(_cfg["video_workers_per_account"]))
        if _cfg.get("mint_interval"):
            _env["VEO3TOP_MINT_INTERVAL"] = str(float(_cfg["mint_interval"]))   # sustainable: mint chậm -> đốt quota reCAPTCHA chậm
        # CÁCH LY 429 (hết quota ngày account) -> nghỉ N giờ. Chỉnh qua GUI settings (pool_isolation_hours). Mặc định 6h.
        _env["VEO3TOP_VID_QUOTA_REST"] = str(int(float(_cfg.get("pool_isolation_hours", 6) or 6) * 3600))
        # KHÔNG ẩn login video: ĐO THẬT offscreen phá login ('Password page NOT detected' + PageDisconnected).
        # Video chỉ mở chrome lúc login (hiếm) -> để hiện cho login chạy được.
    except Exception:
        pass
    CF = 0x08000000  # CREATE_NO_WINDOW
    # GHI log service ra FILE (CREATE_NO_WINDOW nuốt stdout -> trước đây không thấy token/generate lỗi gì).
    try:
        lf = open(_LOGF, "a", buffering=1, encoding="utf-8", errors="replace")
        lf.write(f"\n===== video_factory START (port {PORT}, {time.strftime('%Y-%m-%d %H:%M:%S')}) =====\n"); lf.flush()
        out = lf
    except Exception:
        out = subprocess.DEVNULL
    p = subprocess.Popen(args, creationflags=CF, cwd=_HERE, stdout=out, stderr=subprocess.STDOUT, env=_env)
    try:
        with open(_PIDF, "w") as f:
            f.write(str(p.pid))
    except Exception:
        pass
    log(f"[pool-client] đã bật video_factory service (PID {p.pid}, port {PORT}) — log: {_LOGF}")
    return p


def ensure_service(log=print, wait_up=90):
    """Bảo đảm service chạy. Singleton: chỉ 1 mã bật (lock O_EXCL), mã khác chờ."""
    if is_up():
        return True
    # thử giành quyền bật
    got_lock = False
    try:
        fd = os.open(_LOCK, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, str(os.getpid()).encode()); os.close(fd)
        got_lock = True
    except FileExistsError:
        # mã khác đang bật -> chỉ chờ; nhưng dọn lock cũ nếu quá hạn (service chết giữa chừng)
        try:
            if time.time() - os.path.getmtime(_LOCK) > 120:
                os.remove(_LOCK)
        except Exception:
            pass
    if got_lock:
        try:
            if not is_up():
                _spawn_service(log)
        finally:
            pass  # giữ lock tới khi service lên (tránh mã khác bật trùng), xoá sau
    # chờ service lên
    end = time.time() + wait_up
    while time.time() < end:
        if is_up():
            if got_lock:
                try: os.remove(_LOCK)
                except Exception: pass
            return True
        time.sleep(2)
    if got_lock:
        try: os.remove(_LOCK)
        except Exception: pass
    return is_up()


def generate(image_path, prompt, out_path, aspect=None, seed=None, timeout=1600, log=print):
    """Gửi 1 job video tới nhà máy. Trả (success, info, error) — KHỚP _submit_video."""
    if requests is None:
        return False, {}, "pool-client: thiếu requests"
    if not ensure_service(log=log):
        return False, {}, "pool-client: video_factory service không bật được"
    payload = {"image_path": str(image_path), "prompt": prompt, "out_path": str(out_path),
               "aspect": aspect, "seed": seed}
    try:
        r = requests.post(f"{BASE}/generate", json=payload, timeout=timeout)
        if r.status_code != 200:
            return False, {}, f"pool-client: HTTP {r.status_code}"
        j = r.json()
        return bool(j.get("success")), j.get("info") or {}, j.get("error") or ""
    except Exception as e:
        return False, {}, f"pool-client: {type(e).__name__}: {e}"


def health(log=print):
    if not is_up():
        return None
    try:
        return requests.get(f"{BASE}/health", timeout=5).json()
    except Exception:
        return None


def shutdown():
    if requests is None:
        return
    try:
        requests.post(f"{BASE}/shutdown", json={}, timeout=5)
    except Exception:
        pass
