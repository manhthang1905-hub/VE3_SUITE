"""
image_factory_client — client cho mã gọi NHÀ MÁY ẢNH CHUNG (image_factory.py).
- ensure_service(): tự bật service ảnh 1 lần (singleton qua lock file), port riêng 8789 (khác video 8788).
- generate_image(prompt, out_path, aspect, seed, image_inputs): POST /generate_image, chờ kết quả.
image_inputs: list dict đã có 'rawImageBytes'(b64)+'mimeType' (reference nhân vật) — mã tự build từ file ảnh.
"""
import os, sys, json, time, subprocess
_HERE = os.path.dirname(os.path.abspath(__file__))
_SUITE = os.path.dirname(_HERE)

try:
    import requests
except Exception:
    requests = None

PORT = int(os.environ.get("VEO3TOP_IMG_POOL_PORT", "8789") or "8789")
BASE = f"http://127.0.0.1:{PORT}"
_LOCK = os.path.join(_SUITE, ".veo3top_imgpool_start.lock")
_PIDF = os.path.join(_SUITE, ".veo3top_imgpool.pid")
_TOKEN_CHROMES = int(os.environ.get("VEO3TOP_IMG_POOL_TOKEN_CHROMES", "10") or "10")  # (cũ, không dùng cho browser pool)
_POOL_ACCOUNTS = int(os.environ.get("VEO3TOP_IMG_POOL_ACCOUNTS", "10") or "10")

def _cfg_accounts(default=10):
    """So ma chay song song cua nha may ANH: uu tien settings.yaml (image_pool_accounts), roi env, roi default 10."""
    try:
        import yaml
        cfgp = os.path.join(_SUITE, "tools", "ve3", "config", "settings.yaml")
        cfg = yaml.safe_load(open(cfgp, encoding="utf-8")) or {}
        n = int(cfg.get("image_pool_accounts", _POOL_ACCOUNTS))
        return max(1, min(100, n))   # trần 100 (trước là 50 -> chặn cứng dù settings=96); login bounded 5 nên 96 an toàn
    except Exception:
        return _POOL_ACCOUNTS
# NHÀ MÁY ẢNH: image_pool_browser.py (96 account login + WINNING external mint: token từ chrome-trắng-CLEAN-tươi
# dùng chung + submit ngoài IPv6 = đã đo 75-77%). image_factory.py = bản 10-ultra (chung với video, dùng khi cần).
_USE_BROWSER_POOL = os.environ.get("VEO3TOP_IMG_BROWSER_POOL", "1") != "0"


def _health(timeout=3):
    if requests is None:
        return None
    try:
        r = requests.get(f"{BASE}/health", timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        return None
    return None


def is_up(timeout=3):
    h = _health(timeout)
    if h is None:
        return False
    if not _USE_BROWSER_POOL:
        return True
    # CHỈ coi là "up" khi ĐÚNG service browser pool (health có 'slots'); service ảnh CŨ (curl_cffi) có
    # 'accounts'/'image_per_hour' -> KHÁC -> phải thay. Tránh dùng nhầm service cũ đang chiếm cổng.
    return "slots" in h


def _kill_wrong_service(log=print):
    """Service KHÁC (ảnh cũ) đang chiếm cổng -> kill để nhường cho pool browser. Chạy trong tool (admin) kill được."""
    try:
        # theo pid file
        if os.path.exists(_PIDF):
            pid = open(_PIDF).read().strip()
            if pid.isdigit():
                subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], capture_output=True, creationflags=0x08000000)
        # theo cổng (chắc chắn)
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"$c=Get-NetTCPConnection -LocalPort {PORT} -State Listen -EA SilentlyContinue; "
                        "if($c){$c.OwningProcess|Select-Object -Unique|ForEach-Object{taskkill /PID $_ /T /F}}"],
                       capture_output=True, creationflags=0x08000000)
        log(f"[img-pool-client] đã kill service ảnh CŨ chiếm cổng {PORT} (nhường pool browser)")
    except Exception as e:
        log(f"[img-pool-client] không kill được service cũ: {e}")


_LOGF = os.path.join(_SUITE, ".veo3top_imgpool.log")


def _spawn_service(log=print):
    exe = sys.executable
    if _USE_BROWSER_POOL:
        # NHÀ MÁY ẢNH browser-based: account gmail + chrome warmed + IN-PAGE generate (ra ảnh thật)
        script = os.path.join(_HERE, "image_pool_browser.py")
        args = [exe, script, "--port", str(PORT), "--accounts", str(_cfg_accounts())]
    else:
        script = os.path.join(_HERE, "image_factory.py")
        args = [exe, script, "--port", str(PORT), "--token-chromes", str(_TOKEN_CHROMES)]
    # GHI log service ra FILE (trước đây CREATE_NO_WINDOW nuốt hết stdout -> không biết vì sao ảnh không ra).
    # LOG ROTATION (chạy nhiều THÁNG -> log phình): nếu > 20MB, giữ ~2000 dòng cuối rồi ghi tiếp.
    try:
        if os.path.exists(_LOGF) and os.path.getsize(_LOGF) > 20 * 1024 * 1024:
            with open(_LOGF, "r", encoding="utf-8", errors="replace") as _f:
                _tail = _f.readlines()[-2000:]
            with open(_LOGF, "w", encoding="utf-8", errors="replace") as _f:
                _f.write(f"===== LOG ROTATED (giữ 2000 dòng cuối, {time.strftime('%Y-%m-%d %H:%M:%S')}) =====\n")
                _f.writelines(_tail)
    except Exception:
        pass
    try:
        lf = open(_LOGF, "a", buffering=1, encoding="utf-8", errors="replace")
        lf.write(f"\n===== image_factory START (port {PORT}, {time.strftime('%Y-%m-%d %H:%M:%S')}) =====\n"); lf.flush()
        out = lf
    except Exception:
        out = subprocess.DEVNULL
    # Truyền env cho service: ẨN/HIỆN chrome ảnh theo settings.yaml (image_hide_chrome, mặc định ẩn).
    env = os.environ.copy()
    # THROUGHPUT ẢNH: token factory recycle CAO (ít relaunch = nhiều token) + nhiều token chrome (account cookie-based
    # không mở chrome -> dư tài nguyên). Chỉ áp cho tiến trình ẢNH (video là process riêng, giữ recycle riêng).
    env.setdefault("VEO3TOP_TOKEN_RECYCLE", "10")
    try:
        import yaml
        cfgp = os.path.join(_SUITE, "tools", "ve3", "config", "settings.yaml")
        cfg = yaml.safe_load(open(cfgp, encoding="utf-8")) or {}
        env["VEO3TOP_IMG_HIDE"] = "1" if cfg.get("image_hide_chrome", True) else "0"
        env["VEO3TOP_HIDE_CHROME"] = env["VEO3TOP_IMG_HIDE"]   # đồng bộ: ẩn cả login chrome (google_login đọc env này)
        if cfg.get("image_token_chromes"):   # cho phép chỉnh số token chrome ẢNH qua settings.yaml
            env["VEO3TOP_IMG_TOKEN_CHROMES"] = str(int(cfg.get("image_token_chromes")))
        if cfg.get("image_login_concurrency"):   # SỐ CHROME LOGIN đồng thời tối đa (mặc định 5); máy yếu -> hạ xuống
            env["VEO3TOP_IMG_LOGIN_CONCURRENCY"] = str(int(cfg.get("image_login_concurrency")))
        # TRẦN ảnh/lượt/account: 0 = KHÔNG giới hạn (chạy tới hết quota thật -> đổi model -> Ultra). Luôn truyền (kể cả 0).
        env["VEO3TOP_IMG_MAX_PER_ACCT"] = str(int(cfg.get("image_max_per_account", 0) or 0))
        if cfg.get("image_token_recycle"):
            env["VEO3TOP_TOKEN_RECYCLE"] = str(int(cfg.get("image_token_recycle")))
        if cfg.get("mint_interval"):
            env["VEO3TOP_MINT_INTERVAL"] = str(float(cfg.get("mint_interval")))   # sustainable: mint chậm -> đốt quota reCAPTCHA chậm (bền)
        # núm tinh chỉnh còn dùng: cách ly account đốt sau N lượt reCAPTCHA lì
        env["VEO3TOP_IMG_SWAP_GIVEUP"] = str(int(cfg.get("image_swap_giveup", 3)))
        # CÁCH LY 429 (hết quota ngày cả 3 model) -> nghỉ N giờ. Chỉnh qua GUI settings (pool_isolation_hours). Mặc định 6h.
        env["VEO3TOP_IMG_ALLMODEL_REST"] = str(int(float(cfg.get("pool_isolation_hours", 6) or 6) * 3600))
        # EGRESS ẢNH: 'direct' = IP máy + Fake DNS Google (đúng veo3top: điểm reCAPTCHA cao, hết IPv6 403+treo);
        # 'ipv6' = pool IPv6 (hợp VIDEO chống 429, nhưng ẢNH bị 403 flood). Mặc định direct. Đổi ở settings.yaml.
        # MẶC ĐỊNH 'direct' = IP MÁY (bỏ IPv6): android_bypass KHÔNG reCAPTCHA + quota 429 theo MODEL/account (không theo IP)
        # -> IPv6 hết tác dụng, chỉ gây churn 'tạo project lỗi'/PageDisconnected khi login. 'ipv6' = giữ pool IPv6 (cũ).
        _egress = str(cfg.get("image_egress", "direct")).strip().lower()
        env["VEO3TOP_IMG_NATIVE"] = "1" if _egress == "direct" else "0"
        env["VEO3TOP_DOH"] = "1" if cfg.get("image_fake_dns_google", True) else "0"  # Fake DNS Google (bí quyết veo3top)
    except Exception:
        pass
    p = subprocess.Popen(args, creationflags=0x08000000, cwd=_HERE, stdout=out, stderr=subprocess.STDOUT, env=env)
    try:
        with open(_PIDF, "w") as f:
            f.write(str(p.pid))
    except Exception:
        pass
    log(f"[img-pool-client] đã bật image_factory service (PID {p.pid}, port {PORT}) — log: {_LOGF}")
    return p


def ensure_service(log=print, wait_up=120):
    if is_up():
        return True
    got_lock = False
    try:
        fd = os.open(_LOCK, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, str(os.getpid()).encode()); os.close(fd); got_lock = True
    except FileExistsError:
        try:
            if time.time() - os.path.getmtime(_LOCK) > 150:
                os.remove(_LOCK)
        except Exception:
            pass
    if got_lock and not is_up():
        # nếu có service KHÁC (ảnh cũ curl_cffi) đang chiếm cổng nhưng không phải browser pool -> kill rồi bật mới
        if _USE_BROWSER_POOL and _health() is not None:
            _kill_wrong_service(log)
            for _ in range(8):
                if _health() is None:
                    break
                time.sleep(1)
        _spawn_service(log)
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


def generate_image(prompt, out_path, aspect=None, seed=None, image_inputs=None, timeout=900, log=print):
    """Gửi 1 job ảnh tới nhà máy. Trả (success, info, error)."""
    if requests is None:
        return False, {}, "img-pool-client: thiếu requests"
    if not ensure_service(log=log):
        return False, {}, "img-pool-client: image_factory service không bật được"
    payload = {"prompt": prompt, "out_path": str(out_path), "aspect": aspect, "seed": seed,
               "image_inputs": image_inputs}
    try:
        r = requests.post(f"{BASE}/generate_image", json=payload, timeout=timeout)
        if r.status_code != 200:
            return False, {}, f"img-pool-client: HTTP {r.status_code}"
        j = r.json()
        return bool(j.get("success")), j.get("info") or {}, j.get("error") or ""
    except Exception as e:
        return False, {}, f"img-pool-client: {type(e).__name__}: {e}"


def health():
    if not is_up():
        return None
    try:
        return requests.get(f"{BASE}/health", timeout=5).json()
    except Exception:
        return None
