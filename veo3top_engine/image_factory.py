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
import account_manager as am
import project_pool as pp
from auth_cache import AuthCache
from pool_accounts import load_image_pool_accounts

GEN_ATTEMPTS = 40          # grind token mới /1 lượt (ảnh: 429 hay do IP -> IPv6 riêng cứu; recaptcha_quota thì chờ)
BYPASS_RETRY = int(os.environ.get("VEO3TOP_BYPASS_RETRY", "5") or "5")  # bypass trượt liên tiếp bao nhiêu lần mới mở WEB fallback
JOB_MAX_CYCLES = 6         # bound số lần requeue 1 job (tránh treo khi quota/ratelimit kéo dài)
IMG_QUOTA_GIVEUP = int(os.environ.get("VEO3TOP_IMG_QUOTA_GIVEUP", "6") or "6")  # recaptcha_quota liên tiếp/1 model -> coi model đó hết quota
# MODEL ẢNH theo THỨ TỰ CHẤT LƯỢNG cao->thấp. Mỗi account có quota RIÊNG mỗi model -> hết model này
# nhà máy TỰ CHUYỂN model kế (khai thác hết quota, ra nhiều ảnh nhất). Cấu hình qua env (thêm mã Lite khi có).
#   GEM_PIX_2 = Nano Banana Pro | NARWHAL = Nano Banana 2 | HARBOR_SEAL = Nano Banana 2 Lite
#   (mã xác nhận từ response 200 thật: modelNameType). Thứ tự = ưu tiên chất lượng cao->thấp.
IMG_MODELS = [m.strip() for m in os.environ.get("VEO3TOP_IMG_MODELS", "GEM_PIX_2,NARWHAL,HARBOR_SEAL").split(",") if m.strip()]
MODEL_REST_SECS = int(os.environ.get("VEO3TOP_IMG_MODEL_REST", "1800") or "1800")  # 1 model hết quota -> nghỉ (account) 30'
REST_SOFT = 8


def _log(msg):
    print(f"[imagefactory] {msg}", flush=True)


class Account:
    """1 account ultra trong pool + IPv6 RIÊNG (mỗi account 1 IP -> quota reCAPTCHA độc lập)."""
    def __init__(self, name, email, chrome_path, cache, bundle=""):
        self.name = name; self.email = email; self.chrome_path = chrome_path
        self.cache = cache
        self.bundle = bundle          # email|password|totp -> auto login password khi profile hết đăng nhập
        self.ipv6 = None
        self.resume_at = 0.0; self.rest_streak = 0
        self.busy = False; self.wins = 0; self.fails = 0
        self.egress_wins = {}; self.last_kind = ""
        self.model_wins = {}           # model -> số ảnh ra (thống kê khai thác quota)
        self.model_rest = {}           # model -> ts hết nghỉ (model đó hết quota tạm -> bỏ qua tới ts)
        self._proj = pp.ProjectRotator()   # XOAY + TỰ CHỮA project (dùng chung ảnh/video) — chống gãy 24/7
        self._lock = threading.Lock()
        self._recovering = False       # đang được RecoveryManager chữa (nền) -> worker không submit trùng
        import re as _re
        m = _re.search(r"Copy \((\d+)\)", str(chrome_path or ""))
        self._copyn = int(m.group(1)) if m else (abs(hash(email or "")) % 80)
        # ẢNH dùng dải port RIÊNG với VIDEO (warm 992x, login_wid 18x) -> 2 factory không đụng cổng
        self.relogin_port = 9920 + self._copyn
        self._login_wid = 180 + self._copyn

    def recover(self, log=lambda *_: None):
        """Chạy NỀN bởi RecoveryManager: clear+login(password)+warm+cookie. Thành công+validate -> clear rest
        (account trở lại NGAY). Thất bại -> nghỉ dài + báo (account có thể bị chặn Flow / cần xem thủ công).
        KHÔNG chặn worker (worker chỉ submit + park + requeue)."""
        import account_manager as am
        self._recovering = True
        try:
            nd = am.full_recover(self.email, self.bundle, self.chrome_path, self.cache, self._copyn,
                                 name=self.name, login_worker_id=self._login_wid, warm_port=self.relogin_port, log=log)
            if nd and nd.get("bearer") and am.validate_auth(nd):
                self.clear_rest()
                log(f"✅ ẢNH: {self.email} ĐÃ CHỮA XONG (login+warm+cookie) -> hoạt động lại.")
                return nd
            self.rest(3600)   # chịu thua lâu hơn (1h) rồi thử lại; job đã requeue sang account sống
            log(f"⚠️ ẢNH: {self.email} chữa KHÔNG thành (có thể bị chặn Flow / cần kiểm tra thủ công) -> nghỉ 1h.")
            return None
        finally:
            self._recovering = False

    def rest_remaining(self):
        return max(0.0, self.resume_at - time.time())

    def rest(self, secs):
        with self._lock:
            self.rest_streak += 1
            self.resume_at = time.time() + secs

    def clear_rest(self):
        with self._lock:
            self.rest_streak = 0; self.resume_at = 0.0

    def next_project(self, cookie, fallback=None):
        """XOAY VÒNG + TỰ CHỮA project (ProjectRotator). Tránh dùng mãi 1 project bị đốt (chống gãy 24/7)."""
        return self._proj.next(cookie, fallback=fallback, log=lambda m: _log(f"{self.email}: {m}"))

    def mark_project_bad(self, pid):
        self._proj.mark_bad(pid, log=lambda m: _log(f"{self.email}: {m}"))

    def auth(self, force=False):
        """Auth NHẸ (không mở chrome -> KHÔNG chặn worker): cache còn hạn -> refresh TỪ COOKIE.
        Cookie chết/không project -> None (recovery mở chrome chạy NỀN qua RecoveryManager)."""
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

    def pick_model(self, models):
        """Chọn model CHẤT LƯỢNG CAO NHẤT còn quota (chưa nghỉ) cho account này. None nếu mọi model đang nghỉ."""
        now = time.time()
        for m in models:                      # models đã theo thứ tự cao->thấp
            if now >= self.model_rest.get(m, 0):
                return m
        return None

    def rest_model(self, model, secs):
        """1 model hết quota -> cho nghỉ (account này) tới khi thử lại; job dùng model kế."""
        with self._lock:
            self.model_rest[model] = time.time() + secs

    def note_model_win(self, model):
        self.model_wins[model] = self.model_wins.get(model, 0) + 1


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
        self.recovery = am.RecoveryManager(log=self.log)   # chữa account lỗi ở NỀN (không chặn tiến độ)

    def _startup_check(self):
        """Đầu giờ: CHECK nhanh từng account (cookie -> validate 1 POST). Account nào lỗi -> đưa vào
        RecoveryManager chữa NỀN (clear+login+warm+cookie). KHÔNG chặn: workers vẫn chạy account SỐNG ngay."""
        healthy = []; broken = []
        lock = threading.Lock()
        def _check(a):
            try:
                auth = a.auth()
                ok = bool(auth) and am.validate_auth(auth)
            except Exception:
                ok = False
            with lock:
                (healthy if ok else broken).append(a)
            if not ok:
                a.rest(1800)   # tạm nghỉ tới khi chữa xong (worker bỏ qua) — job vẫn chạy account sống
                self.recovery.submit(a.email, lambda a=a: a.recover(self.log))
        ths = [threading.Thread(target=_check, args=(a,), daemon=True) for a in self.accounts]
        for t in ths: t.start()
        for t in ths: t.join(timeout=60)
        self.log(f"pool ẢNH: {len(self.accounts)} account -> {len(healthy)} SỐNG, {len(broken)} lỗi đang chữa nền "
                 f"({', '.join(a.name for a in broken) if broken else 'không có'})")

    # ---------- khởi tạo ----------
    def start(self):
        pool = load_image_pool_accounts()   # TÁCH RIÊNG với video (mai đổi sang pro accounts ảnh)
        self.accounts = [Account(a["name"], a["email"], a["chrome_path"], self.cache, a.get("bundle", "")) for a in pool]
        self._startup_check()

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

        # ĐƯỜNG CHÍNH giờ = android_bypass (submit curl thuần, KHÔNG mint captcha -> KHÔNG cần token factory).
        # Token factory (mint WEB token thật qua Chrome) chỉ là FALLBACK khi bypass bị từ chối -> khởi động LAZY
        # (chỉ mở Chrome khi thực sự cần), đỡ tốn RAM/CPU khi bypass chạy tốt (~100%).
        self.factory = None
        self._factory_lock = threading.Lock()

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
            if self.recovery: self.recovery.stop()
        except Exception: pass
        try:
            if self.factory: self.factory.stop()
        except Exception: pass
        for a in self.accounts:
            try:
                if a.ipv6: a.ipv6.stop()
            except Exception: pass

    def _ensure_factory(self):
        """LAZY: mở token factory WEB (Chrome mint token thật) LẦN ĐẦU khi bypass fail cần fallback.
        Trả factory (đã _started) hoặc None. Chạy tốt thì KHÔNG bao giờ gọi -> không mở Chrome."""
        if self.factory and getattr(self.factory, "_started", False):
            return self.factory
        with self._factory_lock:
            if self.factory and getattr(self.factory, "_started", False):
                return self.factory
            try:
                self.log("bypass fail -> KHỞI ĐỘNG token factory WEB (fallback, mở Chrome mint token thật)…")
                self.factory = tf.get_image_factory(mode="blank", n_chromes=self.token_chromes,
                                                    base_port=self.token_port, log=self.log,
                                                    ipv6=True, clean=True, visible=True)
            except Exception as e:
                self.log(f"không khởi động được token factory WEB: {e}")
                self.factory = None
        return self.factory if (self.factory and getattr(self.factory, "_started", False)) else None

    # ---------- xử lý 1 job ảnh trên 1 account ----------
    def _process(self, account, job):
        """Trả 'success' | 'retry_soft' | ('fail', reason). Ảnh: KHÔNG poll, download fifeUrl luôn."""
        auth = account.auth()
        if not auth or not auth.get("bearer") or not auth.get("project"):
            return "retry_soft"
        bearer = auth["bearer"]; cookie = auth.get("cookie")
        project = account.next_project(cookie, fallback=auth["project"])   # XOAY project mỗi job (tránh đốt 1 cái)
        seed = job.get("seed") or (int(time.time() * 1000) % 900000)
        aspect = job.get("aspect") or "IMAGE_ASPECT_RATIO_LANDSCAPE"
        image_inputs = job.get("image_inputs") or None
        auth_fails = 0
        bypass_fails = 0   # bypass trượt LIÊN TIẾP -> mới mở WEB fallback (hạn chế mở Chrome)
        quota_streak = 0   # recaptcha_quota LIÊN TIẾP trên 1 MODEL -> coi model đó hết quota, chuyển model kế

        # MODEL: chọn chất lượng cao nhất account này còn quota (Pro->Nano2->Lite). Hết mọi model -> nghỉ + requeue.
        model = account.pick_model(IMG_MODELS)
        if not model:
            account.rest(300)   # mọi model đang nghỉ (hết quota tạm) -> account nghỉ ngắn, job sang account khác
            return "retry_soft"

        for attempt in range(GEN_ATTEMPTS):
            # submit qua IPv6 RIÊNG của account (ảnh 429 hay do per-IP -> IP riêng giúp nhiều)
            eproxy = account.ipv6.proxy_url() if account.ipv6 else None
            ename = "ipv6" if account.ipv6 else "ipmay"
            # ĐƯỜNG CHÍNH: android_bypass (giải mã từ tst) — bỏ qua reCAPTCHA hoàn toàn, submit curl thuần,
            # KHÔNG mint token (đó là nguồn UNUSUAL). Token='android_bypass', applicationType=ANDROID.
            payload = fc.build_image_payload(job["prompt"], project, fc.BYPASS_TOKEN, seed,
                                             aspect=aspect, image_inputs=image_inputs, model=model,
                                             app_type=fc.APP_TYPE_ANDROID)
            kind, data = fc.generate_image(bearer, project, payload, proxy=eproxy, bypass=True)
            # bypass hiếm khi trượt. Nếu 'unusual'/'other' -> RETRY bypass vài lần (rẻ, không mở Chrome) TRƯỚC;
            # chỉ khi trượt LIÊN TIẾP >= BYPASS_RETRY mới FALLBACK mint WEB token (mở Chrome — nặng, hạn chế).
            # KHÔNG fallback với ratelimit/quota/auth (đó là vấn đề IP/quota/bearer, mint token không cứu được).
            if kind in ("unusual", "other"):
                bypass_fails += 1
                if bypass_fails < BYPASS_RETRY:
                    time.sleep(0.4 + random.uniform(0, 0.4)); continue   # thử lại bypass
                fac = self._ensure_factory()
                tok = fac.get() if fac else None
                if tok:
                    payload = fc.build_image_payload(job["prompt"], project, tok, seed,
                                                     aspect=aspect, image_inputs=image_inputs, model=model,
                                                     app_type=fc.APP_TYPE_WEB)
                    kind, data = fc.generate_image(bearer, project, payload, proxy=eproxy, cookie=cookie)
            if kind == "ok":
                account.note_egress_win(ename); account.note_model_win(model)
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
                                             "account": account.email, "egress": ename, "model": model}, "")
                    return "success"
                except Exception as e:
                    time.sleep(1); continue   # download lỗi -> thử lại
            elif kind == "auth":
                # 401 UNAUTHENTICATED. (1) thử refresh cookie NHANH (inline, không mở chrome); nếu vẫn 401
                # -> KHÔNG grind: đưa account vào chữa NỀN (clear+login+warm+cookie qua RecoveryManager),
                # PARK + requeue job sang account SỐNG. Worker KHÔNG mở chrome -> không chặn, tool không khựng.
                auth_fails += 1
                if auth_fails == 1:
                    a2 = account.auth(force=True)
                    if a2 and a2.get("bearer"):
                        bearer = a2["bearer"]; project = a2.get("project") or project
                        continue
                account.last_kind = "auth_recovering"
                self.log(f"ẢNH: {account.email} 401 -> chữa NỀN (login+warm+cookie), PARK + requeue (account khác chạy tiếp).")
                account.rest(1800)
                self.recovery.submit(account.email, lambda a=account: a.recover(self.log))
                return "retry_soft"
            else:
                account.last_kind = f"{kind}@{ename}"
                if kind in ("ratelimit", "ip_block"):
                    quota_streak = 0
                    # per-IP -> xoay IPv6 RIÊNG của account
                    if account.ipv6:
                        try: account.ipv6.rotate()
                        except Exception: pass
                    time.sleep(1.5 + random.uniform(0, 1.0))
                elif kind == "recaptcha_quota":
                    # RESOURCE_EXHAUSTED cho MODEL hiện tại. Đủ liên tiếp -> coi model này HẾT QUOTA (account) ->
                    # cho model nghỉ 30' rồi CHUYỂN sang model kế (chất lượng thấp hơn) để KHAI THÁC tiếp quota.
                    # Hết MỌI model -> account tạm cạn -> nghỉ ngắn + requeue sang account khác (không treo).
                    quota_streak += 1
                    try: self.factory.note_error()
                    except Exception: pass
                    if quota_streak >= IMG_QUOTA_GIVEUP:
                        account.rest_model(model, MODEL_REST_SECS)
                        nxt = account.pick_model(IMG_MODELS)
                        if nxt and nxt != model:
                            self.log(f"ẢNH: {account.email} model {model} hết quota -> chuyển {nxt} (khai thác tiếp).")
                            model = nxt; quota_streak = 0
                            continue
                        account.rest(180)   # mọi model của account này tạm cạn -> nghỉ, job sang account khác
                        return ("fail", f"tất cả model ({','.join(IMG_MODELS)}) hết quota - thử lại lượt sau")
                    time.sleep(1.0 + random.uniform(0, 0.8))
                else:
                    quota_streak = 0
                    if pp.is_project_error(kind, data):
                        # LỖI CẤP-PROJECT (PERMISSION_DENIED/NOT_FOUND) -> loại project + ĐỔI project ngay (self-heal 24/7)
                        account.mark_project_bad(project)
                        project = account.next_project(cookie, fallback=project)
                    else:
                        # 'unusual' = token reCAPTCHA xấu -> reset chrome chai (adaptive), KHÔNG đụng project
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
                          "busy": a.busy, "wins": a.wins, "fails": a.fails, "egress_wins": a.egress_wins,
                          "model_wins": a.model_wins,
                          "models_resting": [m for m in IMG_MODELS if time.time() < a.model_rest.get(m, 0)]}
                         for a in self.accounts],
            "models": IMG_MODELS,
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
