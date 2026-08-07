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
import roster_reload as rr          # NẠP LẠI NÓNG roster (xem chú thích dài trong roster_reload.py)
import phien_kiem as pk             # KIỂM PHIÊN ba trạng thái (xem chú thích dài trong phien_kiem.py)
import kho_phien as kp              # KÉO PHIÊN TỪ KHO SERVER (xem chú thích dài trong kho_phien.py)
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
        # NẠP LẠI NÓNG: account bị gỡ khỏi roster -> `retired=True`. Luồng thợ của
        # NÓ tự thoát ở đầu vòng lặp kế tiếp — tức là SAU khi job đang chạy xong,
        # không cắt ngang. Đây là cách duy nhất dừng đúng một account mà không
        # đụng `_running` (thứ dừng CẢ nhà máy).
        self.retired = False
        # Phiên của account này ở trạng thái nào — BA giá trị, không phải hai.
        # Cập nhật ở `_startup_check`, ở nhánh 401, và mỗi lần ra ảnh. Giữ dạng
        # cờ trong bộ nhớ thay vì đọc file ở `/health`: `/health` bị gọi rất dày.
        #
        # Mặc định là CHƯA RÕ chứ KHÔNG phải "chết": lúc vừa dựng object thì chưa
        # ai kiểm gì cả, và "chưa kiểm" với "đã kiểm và chết" là hai chuyện khác
        # hẳn nhau (07/08/2026 chính vì trộn hai cái này mà `/health` báo 8/96).
        self.trang_thai_phien = pk.KHONG_KIEM_DUOC
        import re as _re
        m = _re.search(r"Copy \((\d+)\)", str(chrome_path or ""))
        self._copyn = int(m.group(1)) if m else (abs(hash(email or "")) % 80)
        # ẢNH dùng dải port RIÊNG với VIDEO (warm 992x, login_wid 18x) -> 2 factory không đụng cổng
        self.relogin_port = 9920 + self._copyn
        self._login_wid = 180 + self._copyn

    # ── phien_ok: giữ TÊN CŨ cho mã cũ, nhưng nghĩa giờ chặt hơn ───────────
    # Đọc `phien_ok` = "ĐÃ CHỨNG MINH được là sống", chứ không phải "không bị coi
    # là chết". Ghi `phien_ok = True/False` vẫn chạy như cũ (đường ra ảnh 200 và
    # đường 401 vẫn ghi kiểu này) — nó chỉ dịch sang một trong ba trạng thái.
    @property
    def phien_ok(self):
        return self.trang_thai_phien == pk.SONG

    @phien_ok.setter
    def phien_ok(self, v):
        self.trang_thai_phien = pk.SONG if v else pk.CHET_CHAC_CHAN

    def recover(self, log=lambda *_: None):
        """Chạy NỀN bởi RecoveryManager: clear+login(password)+warm+cookie. Thành công+validate -> clear rest
        (account trở lại NGAY). Thất bại -> nghỉ dài + báo (account có thể bị chặn Flow / cần xem thủ công).
        KHÔNG chặn worker (worker chỉ submit + park + requeue).

        ⚠ ĐÂY LÀ HÀNH ĐỘNG ĐẮT NHẤT VÀ RỦI RO NHẤT TRONG CẢ HỆ: nó tiêu một lượt
        đăng nhập bằng mật khẩu của Google. Chỉ được gọi khi CÓ BẰNG CHỨNG phiên
        chết (401/403-phiên, hoặc kho server trả lời là không có phiên), và luôn
        phải đi qua `phien_kiem.HanMucChua`. Xem `phien_kiem.py` và `kho_phien.py`."""
        import account_manager as am
        self._recovering = True
        try:
            nd = am.full_recover(self.email, self.bundle, self.chrome_path, self.cache, self._copyn,
                                 name=self.name, login_worker_id=self._login_wid, warm_port=self.relogin_port, log=log)
            if nd and nd.get("bearer") and am.validate_auth(nd):
                self.clear_rest()
                self.phien_ok = True
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
        """Auth NHẸ (không mở chrome -> KHÔNG chặn worker). BỐN BƯỚC, RẺ TỚI ĐẮT.

        1. cache cục bộ còn hạn                -> dùng luôn
        2. cache cục bộ có cookie              -> làm mới thẻ TỪ COOKIE
        3. KHO SERVER có phiên -> KÉO VỀ, ghi cache, dùng   ← thêm 07/08/2026
        4. hết cách -> trả None (đường đăng nhập bằng mật khẩu, do người gọi quyết)

        ═══ VÌ SAO CÓ BƯỚC 3 ═══

        Trước 07/08/2026 hàm này nhảy thẳng từ bước 2 sang `return None`, và
        `_startup_check` đọc None thành "phiên chết" rồi đi ĐĂNG NHẬP LẠI BẰNG
        MẬT KHẨU. Đo hôm đó: server giữ 96 tài khoản `veo3_image` trạng thái
        `ready` (đủ cookie + project), máy chỉ có 65 bản ghi trong `_auth_cache`
        — 31 tài khoản có phiên hợp lệ TRÊN SERVER mà không có file nào trên
        máy, nên bị gõ lại mật khẩu 31 lần cho một thứ nằm cách đó đúng một lời
        gọi HTTP. Đăng nhập hàng loạt là thứ đẻ ra CAPTCHA.

        Gốc: hai kho phiên chảy MỘT CHIỀU (worker đẩy LÊN server, không có đường
        về cho một tài khoản lẻ). Bước 3 là đường về đó.
        """
        d = self.cache._load(self.email)
        if not force and self.cache._fresh(d):
            return d
        if d and d.get("cookie") and d.get("project"):
            nd = self.cache._refresh_from_cookie(self.email, d)
            if nd:
                return nd
        if not (d and d.get("cookie")):
            # Máy KHÔNG có cookie -> hỏi kho server TRƯỚC khi chịu thua.
            # `keo_phien_ve` tự có van chống hỏi dồn (hàm này được gọi mỗi job).
            nd, _kq = kp.keo_phien_ve(self.email, self.cache,
                                      log=lambda m: _log(f"{self.email}: {m}"))
            if nd and nd.get("cookie") and nd.get("project"):
                return self.cache._refresh_from_cookie(self.email, nd)
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
        # HẠN MỨC đăng nhập lại bằng mật khẩu. `RecoveryManager` chỉ bảo đảm MỖI
        # LÚC MỘT Chrome; nó vẫn chạy hết hàng đợi nối đuôi nhau, nên 50 account
        # hỏng cùng lúc vẫn thành 50 lượt đăng nhập trong ít phút — đúng mẫu hành
        # vi Google trả lời bằng CAPTCHA. Van này rải chúng ra theo thời gian.
        self.han_muc_chua = pk.HanMucChua(log=self.log)
        # ── NẠP LẠI NÓNG (07/08/2026) — xem chú thích dài ở `reload_accounts` ──
        self._nap_lai_lock = rr.KhoaNapLai()
        self._wpa = int(os.environ.get("VEO3TOP_POOL_WORKERS_PER_ACCOUNT", "7") or "7")
        self._ipv6_slot = 0            # cấp port IPv6 tăng dần, KHÔNG tái dùng port của account cũ
        self.n_kho_san_sang = 0        # số account trong KHO có cookie dùng được
        self.last_reload_ts = 0.0
        self.reload_count = 0
        self._da_canh_bao_lech = False

    def _startup_check(self, accounts=None):
        """Đầu giờ: kiểm nhanh từng account. Toàn bộ luật nằm ở `phien_kiem.kiem_roster`.

        `accounts` — chỉ kiểm một PHẦN roster. Đường nạp lại nóng dùng tham số này
        để kiểm riêng account VỪA THÊM: kiểm lại cả roster sẽ bắn một loạt POST
        validate cho những account đang chạy job của khách, hoàn toàn vô ích.

        ═══ BẢN CŨ SAI THẾ NÀO (07/08/2026) — ĐỪNG QUAY LẠI ═══

            try:    ok = bool(a.auth()) and am.validate_auth(auth)
            except Exception:  ok = False          # mạng chập = "phiên chết"
            if not ok: a.rest(1800); self.recovery.submit(...)   # + đăng nhập lại

        Ba cái sai chồng lên nhau, và cái thứ ba nuôi cái thứ nhất:
          1. `except Exception` gộp "chưa kiểm được" vào "chết" (xem `phien_kiem`).
          2. Một lần trượt kiểm -> nghỉ 30 phút + đăng nhập lại bằng mật khẩu.
          3. 64 lượt kiểm bắn CÙNG LÚC -> Google chặn tốc độ -> quay lại (1).

        Kết quả đo được: `logged_in` bò từ 8 lên 41 trong 30 phút (nó là BỘ ĐẾM
        TIẾN TRÌNH KIỂM, không phải sức khoẻ), ảnh mất 600–780 giây thay vì ~90,
        trong khi 98 hồ sơ Chrome trên đĩa vẫn còn nguyên cookie và không hồ sơ
        nào sót khoá `Singleton*` — tức KHÔNG một phiên nào thật sự mất.
        """
        accounts = self.accounts if accounts is None else accounts
        kq = pk.kiem_roster(
            accounts,
            submit_chua=lambda a: self.recovery.submit(a.email, lambda a=a: a.recover(self.log)),
            log=self.log, han_muc=self.han_muc_chua, nhan="ẢNH",
        )
        return kq["song"]

    # ---------- khởi tạo ----------
    def start(self):
        pool = load_image_pool_accounts()   # TÁCH RIÊNG với video (mai đổi sang pro accounts ảnh)
        self.accounts = [Account(a["name"], a["email"], a["chrome_path"], self.cache, a.get("bundle", "")) for a in pool]
        self._startup_check()

        # mỗi account 1 IPv6 RIÊNG (như video) -> quota reCAPTCHA per-IP độc lập
        ok6 = self._cap_ipv6(self.accounts)
        if ok6 is not None:
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
        wpa = self._wpa
        self._mo_tho(self.accounts)
        self.log(f"đã khởi động {len(self._workers)} worker ảnh ({len(self.accounts)} account x {wpa} luồng)")
        return True

    # ---------- tiện ích dùng chung cho start() VÀ nạp lại nóng ----------
    def _cap_ipv6(self, accounts):
        """Cấp IPv6 riêng cho một NHÓM account. Trả số cấp được, hoặc None nếu tắt IPv6.

        Port lấy từ bộ đếm tăng dần `_ipv6_slot` chứ KHÔNG phải `enumerate` như
        trước: account mới thêm lúc nạp nóng mà tái dùng port của account đang
        chạy thì hai transport tranh nhau một cổng, và cái đang phục vụ khách là
        cái thua."""
        if os.environ.get("VEO3TOP_POOL_USE_IPV6", "1") != "1":
            return None
        ok6 = 0
        for a in accounts:
            if a.ipv6:
                ok6 += 1; continue
            try:
                tr = ip6t.IPv6Transport(f"imgf_{a.name}", port=self.ipv6_port + self._ipv6_slot,
                                        log=lambda *_: None)
                self._ipv6_slot += 1
                if tr.start():
                    a.ipv6 = tr; ok6 += 1
            except Exception:
                pass
        return ok6

    def _mo_tho(self, accounts):
        """Mở `_wpa` luồng thợ cho mỗi account trong nhóm. Luồng nào cũng tự thoát
        khi `account.retired` -> gỡ account không cần đụng tới `_running`."""
        n = 0
        for a in accounts:
            for _ in range(self._wpa):
                t = threading.Thread(target=self._worker, args=(a,), daemon=True)
                t.start(); self._workers.append(t); n += 1
        return n

    def stop(self):
        self._running = False
        try:
            if self.recovery: self.recovery.stop()
        except Exception: pass
        try:
            if self.han_muc_chua: self.han_muc_chua.stop()
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
                account.phien_ok = True   # ra 200 = phiên còn sống (dùng cho cảnh báo kho lệch)
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
                # 401 TRÊN MỘT LƯỢT GỌI THẬT — đây MỚI là bằng chứng phiên chết,
                # thứ mà phép kiểm đầu giờ không bao giờ có quyền kết luận thay.
                # Account "chưa rõ" vẫn được giao việc chính là để tới được đây.
                account.trang_thai_phien = pk.CHET_CHAC_CHAN
                giay = pk.nghi_tang_dan(account)   # NGẮN + tăng dần, không phải 1800s cứng
                kq = self.han_muc_chua.xep_hang(
                    account.email, lambda a=account: self.recovery.submit(a.email, lambda a=a: a.recover(self.log)))
                self.log(f"ẢNH: {account.email} 401 lúc chạy thật -> nghỉ {int(giay)}s + "
                         f"{'chữa NỀN ngay' if kq == 'chay' else 'XẾP HÀNG chờ lượt chữa'}, "
                         f"PARK + requeue (account khác chạy tiếp).")
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
        # `account.retired` được kiểm ở ĐẦU vòng, tức là chỉ sau khi job hiện tại
        # đã xong. Gỡ một account KHÔNG BAO GIỜ cắt ngang job đang chạy.
        while self._running and not account.retired:
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

    # ================================================================
    # NẠP LẠI NÓNG roster — thêm/bỏ account mà KHÔNG khởi động lại
    # ================================================================
    #
    # ═══ SỐ ĐO LÀM BẰNG CHỨNG, ĐỪNG GỠ KHỐI NÀY ═══
    #
    # 07/08/2026 nhà máy ảnh :8789 chạy 8/64 account đăng nhập được trong khi
    # kho có 96/96 sẵn sàng; ảnh mất 600-780 giây thay vì ~90 và một khách thật
    # có 4 job bò ở tốc độ đó. Gốc: roster đọc ĐÚNG MỘT LẦN lúc khởi động, và
    # cách duy nhất nạp phiên mới là khởi động lại — tức giết job của khách.
    # Đường dưới đây bỏ cái giá đó đi.

    def _co_phien(self, email):
        """Account này có cookie dùng được trong `_auth_cache` chưa?

        Đây là điều kiện `Account.auth()` cần để chạy mà KHÔNG mở Chrome. Đếm
        đúng nó thì con số `kho_san_sang` mới nói được sự thật "kho có phiên
        nhưng nhà máy không dùng"."""
        try:
            d = self.cache._load(email)
            return bool(d and d.get("cookie"))
        except Exception:
            return False

    def _so_lieu_roster(self):
        accounts = list(self.accounts)   # chụp một lần: `self.accounts` bị thay nguyên khối
        # `logged_in`/`not_logged_in` ĐÃ BỊ BỎ ở đây (07/08/2026): hai khoá đó
        # mang hai nghĩa cùng lúc và chính chúng làm người vận hành báo sai cho
        # chủ dự án. Thay bằng bộ khoá của `phien_kiem.dem_trang_thai`, nơi mỗi
        # con số chỉ trả lời đúng một câu hỏi.
        so = pk.dem_trang_thai(accounts)
        so["dang_ban"] = sum(1 for a in accounts if a.busy)
        so["kho_san_sang"] = self.n_kho_san_sang
        return so

    def canh_bao_lech(self):
        # So sánh bằng `phien_dung_duoc` (SỐNG + CHƯA RÕ) chứ KHÔNG phải chỉ số
        # đã chứng minh sống: account "chưa rõ" VẪN được worker giao việc, nên
        # đếm nó vào "không dùng được" là tự dựng lại đúng con số dối trá cũ.
        return rr.canh_bao_kho_lech(
            pk.dem_trang_thai(list(self.accounts))["phien_dung_duoc"], self.n_kho_san_sang,
            uptime_s=time.time() - self.started_ts,
        )

    def reload_accounts(self):
        """Đọc lại `load_image_pool_accounts()` rồi cập nhật roster ĐANG CHẠY.

        KHÔNG đụng account đang bận: account rời roster mà `busy` thì được giữ
        lại tới lượt nạp sau (lúc đó nó đã xong job và việc gỡ không giết ai).
        Account còn trong roster thì GIỮ NGUYÊN object cũ — dựng lại là vứt
        cookie/bearer/IPv6 và job đang chạy trên đó chết theo."""
        with self._nap_lai_lock:
            pool = load_image_pool_accounts()
            truoc = self._so_lieu_roster()
            if not pool:
                self.log("⚠️ nạp lại nóng: KHO đọc ra RỖNG -> giữ nguyên roster đang chạy "
                         "(settings.yaml đang được ghi dở?), KHÔNG gỡ account nào")
                bao = rr.dung_bao_cao(truoc, truoc, rr.KetQuaHopNhat([], [], [], [], []),
                                      self.canh_bao_lech())
                bao["ok"] = False; bao["loi"] = "kho rỗng - giữ nguyên roster"
                return bao
            self.n_kho_san_sang = sum(1 for a in pool if self._co_phien(a["email"]))

            cu = list(self.accounts)
            kq = rr.hop_nhat_roster(
                cu, pool,
                lay_khoa=lambda a: a.email,
                lay_khoa_mong_muon=lambda d: d["email"],
                # "Bận" = đang chạy job (`busy`) HOẶC đang được RecoveryManager
                # chữa ở nền (`_recovering`): gỡ giữa lúc chữa thì luồng chữa
                # ghi cookie cho một object không còn ai dùng — mất công vô ích
                # và để lại Chrome mồ côi.
                dang_ban=lambda a: bool(a.busy) or bool(getattr(a, "_recovering", False)),
                tao_moi=lambda d: Account(d["name"], d["email"], d["chrome_path"],
                                          self.cache, d.get("bundle", "")),
            )
            moi = [a for a in kq.danh_sach if a not in cu]
            # Gán NGUYÊN KHỐI: luồng đọc (`health`, log) luôn thấy DANH SÁCH CŨ
            # trọn vẹn hoặc DANH SÁCH MỚI trọn vẹn, không bao giờ thấy nửa vời.
            # (Sửa tại chỗ `self.accounts` là chỗ sinh lỗi đua kinh điển ở đây.)
            self.accounts = kq.danh_sach
            for a in cu:
                if a in kq.danh_sach:
                    continue
                a.retired = True     # luồng thợ của nó tự thoát SAU job hiện tại
                try:
                    if a.ipv6: a.ipv6.stop()
                except Exception:
                    pass
            self.last_reload_ts = time.time(); self.reload_count += 1

            if moi:
                self._cap_ipv6(moi)
                if self._running:
                    self._mo_tho(moi)          # account mới có thợ riêng, account cũ không bị đụng
                try:
                    self._startup_check(moi)   # chỉ kiểm account MỚI (xem chú thích ở đó)
                except Exception as e:
                    self.log(f"nạp lại nóng: kiểm account mới lỗi ({e}) — vẫn cho chạy")

            sau = self._so_lieu_roster()
            canh_bao = self.canh_bao_lech()
            self.log(
                f"♻️ nạp lại nóng roster ẢNH: {truoc['accounts']} -> {sau['accounts']} account "
                f"(+{len(kq.them)} / -{len(kq.bo)}), giữ lại {len(kq.giu_lai_vi_ban)} vì ĐANG BẬN; "
                f"dùng được {sau['phien_dung_duoc']}/{sau['kho_san_sang']} (kho) "
                f"[đã kiểm sống {sau['phien_song']}, chết chắc chắn {sau['phien_chet_chac_chan']}, "
                f"chưa kiểm {sau['phien_chua_kiem']}]"
            )
            if canh_bao:
                self.log("⚠️ " + canh_bao)
            return rr.dung_bao_cao(truoc, sau, kq, canh_bao)

    def health(self):
        up = time.time() - self.started_ts
        iph = (self.total_done / up * 3600) if up > 5 else 0
        ds = list(self.accounts)
        out = {
            "accounts": [{"name": a.name, "email": a.email, "resting_in": round(a.rest_remaining(), 1),
                          "busy": a.busy, "wins": a.wins, "fails": a.fails, "egress_wins": a.egress_wins,
                          "model_wins": a.model_wins, "trang_thai_phien": a.trang_thai_phien,
                          "models_resting": [m for m in IMG_MODELS if time.time() < a.model_rest.get(m, 0)]}
                         for a in ds],
            "models": IMG_MODELS,
            "queue": self.q.qsize(), "done": self.total_done, "fail": self.total_fail,
            "image_per_hour": round(iph, 1), "uptime_s": round(up),
            # ── nạp lại nóng + cảnh báo kho lệch (07/08/2026) ──────────────
            # Các khoá này tồn tại vì hôm đó KHÔNG khoá nào trong `/health` nói
            # được rằng nhà máy đang chạy 8/96 tài khoản. Mọi chỉ số đều xanh,
            # chỉ có ảnh chậm gấp 8 lần.
            #
            # ⚠ KHOÁ `logged_in` ĐÃ BỊ XOÁ HẲN, ĐỪNG THÊM LẠI. Nó là BỘ ĐẾM TIẾN
            # TRÌNH KIỂM (đo được: bò từ 8 lên 41 trong 30 phút) mang một cái tên
            # khiến người đọc hiểu thành "số account đăng nhập được" — và người
            # vận hành đã báo sai cho chủ dự án vì đúng nó. Bốn khoá `phien_*`
            # dưới đây thay nó: mỗi con số trả lời ĐÚNG MỘT câu hỏi.
            #   phien_song           = đã kiểm, CÓ bằng chứng sống
            #   phien_chet_chac_chan = đã kiểm, CÓ bằng chứng chết (401/403-phiên)
            #   phien_chua_kiem      = CHƯA có kết luận (vẫn được giao việc!)
            #   phien_dung_duoc      = song + chua_kiem = số account thật sự chạy
            "kho_san_sang": self.n_kho_san_sang,
            # Số account đang XẾP HÀNG chờ tới lượt đăng nhập lại (hạn mức).
            # Con số này lớn = có nhiều phiên chết thật, KHÔNG phải hệ đang kẹt.
            "chua_cho_luot": self.han_muc_chua.cho_doi(),
            "roster_canh_bao": self.canh_bao_lech(),
            "reload_count": self.reload_count,
            "last_reload_ts": round(self.last_reload_ts),
        }
        # `accounts` ở trên là DANH SÁCH; `dem_trang_thai` cũng trả khoá
        # `accounts` (số lượng) nên phải gộp SAU và bỏ khoá trùng đi, không thì
        # một cái tên lại mang hai nghĩa — đúng thứ cả bản vá này đi chữa.
        so = pk.dem_trang_thai(ds)
        so.pop("accounts", None)
        out.update(so)
        return out


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
        if self.path == "/reload_accounts":
            # NẠP LẠI NÓNG — đường thay thế cho "khởi động lại để đổi account".
            # Khởi động lại giết mọi job đang chạy dở; đường này KHÔNG đụng
            # account đang bận (xem `ImageFactory.reload_accounts`).
            try:
                self._send(200, _FACTORY.reload_accounts())
            except Exception as e:
                _FACTORY.log(f"nạp lại nóng LỖI: {type(e).__name__}: {e}")
                self._send(200, {"ok": False, "loi": f"{type(e).__name__}: {e}"})
            return
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
