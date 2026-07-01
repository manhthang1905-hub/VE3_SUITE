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
_TOKEN_CHROMES = int(os.environ.get("VEO3TOP_IMG_POOL_TOKEN_CHROMES", "10") or "10")


def is_up(timeout=3):
    if requests is None:
        return False
    try:
        return requests.get(f"{BASE}/health", timeout=timeout).status_code == 200
    except Exception:
        return False


def _spawn_service(log=print):
    exe = sys.executable
    script = os.path.join(_HERE, "image_factory.py")
    args = [exe, script, "--port", str(PORT), "--token-chromes", str(_TOKEN_CHROMES)]
    p = subprocess.Popen(args, creationflags=0x08000000, cwd=_HERE)
    try:
        with open(_PIDF, "w") as f:
            f.write(str(p.pid))
    except Exception:
        pass
    log(f"[img-pool-client] đã bật image_factory service (PID {p.pid}, port {PORT})")
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


def generate_image(prompt, out_path, aspect=None, seed=None, image_inputs=None, timeout=600, log=print):
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
