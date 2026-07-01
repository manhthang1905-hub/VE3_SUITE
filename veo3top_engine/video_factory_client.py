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
_TOKEN_CHROMES = int(os.environ.get("VEO3TOP_POOL_TOKEN_CHROMES", "10") or "10")  # ~1 chrome/account: 7 luồng/account cần ~7.3 token/s -> 10 chrome (như veo3top-b mỗi mã 1 chrome)


def is_up(timeout=3):
    if requests is None:
        return False
    try:
        r = requests.get(f"{BASE}/health", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def _spawn_service(log=print):
    exe = sys.executable
    script = os.path.join(_HERE, "video_factory.py")
    args = [exe, script, "--port", str(PORT), "--token-chromes", str(_TOKEN_CHROMES)]
    CF = 0x08000000  # CREATE_NO_WINDOW
    p = subprocess.Popen(args, creationflags=CF, cwd=_HERE)
    try:
        with open(_PIDF, "w") as f:
            f.write(str(p.pid))
    except Exception:
        pass
    log(f"[pool-client] đã bật video_factory service (PID {p.pid}, port {PORT})")
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


def generate(image_path, prompt, out_path, aspect=None, seed=None, timeout=1300, log=print):
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
