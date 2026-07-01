"""
image_factory — NHÀ MÁY ẢNH CHUNG (pool 10 account ultra). MIRROR video_factory.py.
Coi mọi ảnh cần tạo là 1 HÀNG ĐỢI CHUNG; 10 account là WORKER; mỗi account 1 IPv6 RIÊNG + nhiều luồng.

KHÁC video: ảnh ĐỒNG BỘ — generate_image 200 là CÓ ẢNH luôn (fifeUrl) -> download trực tiếp, KHÔNG poll.
Reference (nhân vật) truyền dạng rawImageBytes (embed) -> KHÔNG cần upload per-account.

API service:
  POST /generate_image {prompt, out_path, aspect, seed?, image_inputs?} -> {success, info, error}
  GET  /health ; POST /shutdown
"""
import os, sys, json, time, threading, queue, argparse, random, base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Nhà máy ẢNH pick IPv6 bound từ CUỐI list -> KHÔNG đụng subnet nhà máy VIDEO (pick từ đầu).
# Đặt TRƯỚC khi import ipv6_transport để _pick_bound_ip đọc đúng.
os.environ.setdefault("VEO3TOP_IPV6_FROM_END", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import flow_client as fc
import token_factory as tf
import ipv6_transport as ip6t
from auth_cache import AuthCache
from pool_accounts import load_image_pool_accounts

GEN_ATTEMPTS = 40          # grind token mới /1 lượt (ảnh: 429 hay do IP -> IPv6 riêng cứu; recaptcha_quota thì chờ)
JOB_MAX_CYCLES = 30
REST_SOFT = 8


def _log(msg):
    print(f"[imagefactory] {msg}", flush=True)


class Account:
    """1 account ultra trong pool + IPv6 RIÊNG (mỗi account 1 IP -> quota reCAPTCHA độc lập)."""
    def __init__(self, name, email, chrome_path, cache):
        self.name = name; self.email = email; self.chrome_path = chrome_path
        self.cache = cache
        self.ipv6 = None
        self.resume_at = 0.0; self.rest_streak = 0
        self.busy = False; self.wins = 0; self.fails = 0
        self.egress_wins = {}; self.last_kind = ""
        self._lock = threading.Lock()

    def rest_remaining(self):
        return max(0.0, self.resume_at - time.time())

    def rest(self, secs):
        with self._lock:
            self.rest_streak += 1
            self.resume_at = time.time() + secs

    def clear_rest(self):
        with self._lock:
            self.rest_streak = 0; self.resume_at = 0.0

    def auth(self, force=False):
        d = self.cache._load(self.email)
        if not force and self.cache._fresh(d):
            return d
        if d and d.get("cookie") and d.get("project"):
            nd = self.cache._refresh_from_cookie(self.email, d)
            if nd:
                return nd
        return None

    def note_egress_win(self, egress):
        self.egress_wins[egress] = self.egress_wins.get(egress, 0) + 1


class ImageFactory:
    def __init__(self, token_chromes=10, token_port=9720, ipv6_port=9800, log=_log):
        self.log = log
        self.q = queue.Queue()
        self.cache = AuthCache(log=lambda *_: None)
        self.accounts = []
        self.factory = None
        self.token_chromes = token_chromes
        self.token_port = token_port
        self.ipv6_port = ipv6_port
        self._running = False
        self._workers = []
        self.total_done = 0; self.total_fail = 0; self.started_ts = time.time()
        self._clock = threading.Lock()

    # ---------- khởi tạo ----------
    def start(self):
        pool = load_image_pool_accounts()   # TÁCH RIÊNG với video (mai đổi sang pro accounts ảnh)
        self.accounts = [Account(a["name"], a["email"], a["chrome_path"], self.cache) for a in pool]
        ready = sum(1 for a in self.accounts if a.auth())
        self.log(f"pool ẢNH: {len(self.accounts)} account, {ready} sẵn sàng (cookie ok)")

        # mỗi account 1 IPv6 RIÊNG (như video) -> quota reCAPTCHA per-IP độc lập
        if os.environ.get("VEO3TOP_POOL_USE_IPV6", "1") == "1":
            ok6 = 0
            for i, a in enumerate(self.accounts):
                try:
                    tr = ip6t.IPv6Transport(f"imgf_{a.name}", port=self.ipv6_port + i, log=lambda *_: None)
                    if tr.start():
                        a.ipv6 = tr; ok6 += 1
                except Exception:
                    pass
            self.log(f"IPv6 RIÊNG mỗi account (ảnh): {ok6}/{len(self.accounts)}")
        else:
            self.log("egress ảnh = DIRECT IP máy")

        # token factory ẢNH (action IMAGE_GENERATION) — RIÊNG factory video
        self.factory = tf.get_image_factory(mode="blank", n_chromes=self.token_chromes,
                                            base_port=self.token_port, log=self.log)
        if not (self.factory and self.factory._started):
            self.log("CẢNH BÁO: token factory ảnh chưa sẵn sàng")

        # workers — 7 luồng/account (như video)
        self._running = True
        wpa = int(os.environ.get("VEO3TOP_POOL_WORKERS_PER_ACCOUNT", "7") or "7")
        for a in self.accounts:
            for _ in range(wpa):
                t = threading.Thread(target=self._worker, args=(a,), daemon=True)
                t.start(); self._workers.append(t)
        self.log(f"đã khởi động {len(self._workers)} worker ảnh ({len(self.accounts)} account x {wpa} luồng)")
        return True

    def stop(self):
        self._running = False
        try:
            if self.factory: self.factory.stop()
        except Exception: pass
        for a in self.accounts:
            try:
                if a.ipv6: a.ipv6.stop()
            except Exception: pass

    # ---------- xử lý 1 job ảnh trên 1 account ----------
    def _process(self, account, job):
        """Trả 'success' | 'retry_soft' | ('fail', reason). Ảnh: KHÔNG poll, download fifeUrl luôn."""
        auth = account.auth()
        if not auth or not auth.get("bearer") or not auth.get("project"):
            return "retry_soft"
        bearer = auth["bearer"]; project = auth["project"]
        seed = job.get("seed") or (int(time.time() * 1000) % 900000)
        aspect = job.get("aspect") or "IMAGE_ASPECT_RATIO_LANDSCAPE"
        image_inputs = job.get("image_inputs") or None

        for attempt in range(GEN_ATTEMPTS):
            tok = self.factory.get()
            if not tok:
                time.sleep(1); continue
            # submit qua IPv6 RIÊNG của account (ảnh 429 hay do per-IP -> IP riêng giúp nhiều)
            eproxy = account.ipv6.proxy_url() if account.ipv6 else None
            ename = "ipv6" if account.ipv6 else "ipmay"
            payload = fc.build_image_payload(job["prompt"], project, tok, seed,
                                             aspect=aspect, image_inputs=image_inputs)
            kind, data = fc.generate_image(bearer, project, payload, proxy=eproxy)
            if kind == "ok":
                account.note_egress_win(ename)
                name, fife, b64, rseed = fc.image_result(data)
                if not (fife or b64):
                    continue   # 200 nhưng chưa có ảnh -> token mới
                op = job["out_path"]
                try:
                    if os.path.dirname(op):
                        os.makedirs(os.path.dirname(op), exist_ok=True)
                    if fife:
                        n = fc.download_image_url(fife, op)
                    else:
                        b = base64.b64decode(b64)
                        with open(op, "wb") as f:
                            f.write(b)
                        n = len(b)
                    job["_result"] = (True, {"media_name": name, "bytes": n, "seed": rseed,
                                             "account": account.email, "egress": ename}, "")
                    return "success"
                except Exception as e:
                    time.sleep(1); continue   # download lỗi -> thử lại
            elif kind == "auth":
                a2 = account.auth(force=True)
                if a2 and a2.get("bearer"):
                    bearer = a2["bearer"]; project = a2.get("project") or project
                continue
            else:
                account.last_kind = f"{kind}@{ename}"
                if kind in ("ratelimit", "ip_block"):
                    # per-IP -> xoay IPv6 RIÊNG của account
                    if account.ipv6:
                        try: account.ipv6.rotate()
                        except Exception: pass
                    time.sleep(1.5 + random.uniform(0, 1.0))
                else:
                    # recaptcha_quota/unusual -> token mới, backoff ngắn; tích lỗi -> reset chrome (so le)
                    try: self.factory.note_error()
                    except Exception: pass
                    time.sleep(1.0 + random.uniform(0, 0.8))
                if attempt and attempt % 10 == 0:
                    self.log(f"account {account.email} [{account.last_kind}] retry token ảnh {attempt}/{GEN_ATTEMPTS}")
                continue
        return "retry_soft"

    # ---------- vòng lặp 1 worker ----------
    def _worker(self, account):
        while self._running:
            w = account.rest_remaining()
            if w > 0:
                time.sleep(min(w, 5)); continue
            try:
                job = self.q.get(timeout=2)
            except queue.Empty:
                continue
            if job.get("_cycles", 0) > JOB_MAX_CYCLES:
                job["_result"] = (False, {}, "quá nhiều lượt - bỏ ảnh (chạy lại sau tự làm tiếp)")
                job["_event"].set(); continue
            job["_cycles"] = job.get("_cycles", 0) + 1
            account.busy = True
            try:
                outcome = self._process(account, job)
            except Exception as e:
                outcome = ("fail", f"exception: {e}")
            account.busy = False
            if outcome == "success":
                account.wins += 1; account.clear_rest()
                with self._clock: self.total_done += 1
                job["_event"].set()
            elif outcome in ("retry_soft", "ratelimit"):
                account.fails += 1
                self.q.put(job)   # requeue, worker grind job kế tiếp (không block account)
            else:  # ('fail', reason)
                if "_result" not in job:
                    reason = outcome[1] if isinstance(outcome, tuple) and len(outcome) > 1 else str(outcome)
                    job["_result"] = (False, {}, reason)
                with self._clock: self.total_fail += 1
                job["_event"].set()

    # ---------- submit ----------
    def submit(self, prompt, out_path, aspect=None, seed=None, image_inputs=None, timeout=600):
        job = {"prompt": prompt, "out_path": out_path, "aspect": aspect, "seed": seed,
               "image_inputs": image_inputs, "_event": threading.Event()}
        self.q.put(job)
        if not job["_event"].wait(timeout=timeout):
            return False, {}, "image_factory: timeout chờ job"
        return job.get("_result", (False, {}, "no result"))

    def health(self):
        up = time.time() - self.started_ts
        iph = (self.total_done / up * 3600) if up > 5 else 0
        return {
            "accounts": [{"name": a.name, "email": a.email, "resting_in": round(a.rest_remaining(), 1),
                          "busy": a.busy, "wins": a.wins, "fails": a.fails, "egress_wins": a.egress_wins}
                         for a in self.accounts],
            "queue": self.q.qsize(), "done": self.total_done, "fail": self.total_fail,
            "image_per_hour": round(iph, 1), "uptime_s": round(up),
        }


# ---------------- HTTP ----------------
_FACTORY = None

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, _FACTORY.health())
        else:
            self._send(404, {"error": "nf"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        except Exception:
            self._send(400, {"error": "bad json"}); return
        if self.path == "/shutdown":
            self._send(200, {"ok": True}); threading.Thread(target=_FACTORY.stop, daemon=True).start(); return
        if self.path == "/generate_image":
            pr = data.get("prompt"); outp = data.get("out_path")
            if not outp:
                self._send(400, {"error": "thieu out_path"}); return
            ok, info, err = _FACTORY.submit(pr, outp, aspect=data.get("aspect"), seed=data.get("seed"),
                                            image_inputs=data.get("image_inputs"))
            self._send(200, {"success": bool(ok), "info": info, "error": err})
            return
        self._send(404, {"error": "nf"})


def main():
    global _FACTORY
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8789)
    ap.add_argument("--token-chromes", type=int, default=10)
    args = ap.parse_args()
    _FACTORY = ImageFactory(token_chromes=args.token_chromes)
    _FACTORY.start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    _log(f"HTTP ảnh nghe 127.0.0.1:{args.port} (POST /generate_image)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _FACTORY.stop()


if __name__ == "__main__":
    main()
