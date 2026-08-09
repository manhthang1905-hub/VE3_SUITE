"""
video_factory — NHÀ MÁY VIDEO CHUNG (veo3top-b-pool).
Coi mọi video cần tạo là 1 HÀNG ĐỢI CHUNG; cả 10 account ultra là WORKER; mỗi job được xử lý
bởi account RẢNH nhất -> cân bằng tải, ra video nhanh nhất (giống veo3top). Account bị rate-limit
thì NGHỈ, account khác gánh tiếp.

Mỗi job TRỌN GÓI trên 1 account (vì reference media_id gắn với project của account đó):
  lease account -> upload ảnh (account đó) -> generate I2V (EGRESS LADDER) -> poll -> download.

EGRESS LADDER (dùng ALL, log tầng nào lọt): IP máy -> WARP(40000) -> IPv6 pool. Xoay theo mỗi lần thử.

Chạy như 1 service HTTP cục bộ (GUI start). API:
  POST /generate {image_path, prompt, out_path, aspect, seed?} -> {success, media_id, error, account, egress}
  GET  /health -> trạng thái pool
  POST /shutdown
"""
import os, sys, json, time, threading, queue, argparse, random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import flow_client as fc
import token_factory as tf
import ipv6_transport as ip6t
import rate_coordinator
import account_manager as am
import project_pool as pp
import roster_reload as rr          # NẠP LẠI NÓNG roster (xem chú thích dài trong roster_reload.py)
import phien_kiem as pk             # KIỂM PHIÊN ba trạng thái — DÙNG CHUNG với nhà máy ẢNH, đừng chép bản thứ hai
import kho_phien as kp              # KÉO PHIÊN TỪ KHO SERVER (xem chú thích dài trong kho_phien.py)
from auth_cache import AuthCache
from pool_accounts import load_pool_accounts

WARP_PROXY = "socks5://127.0.0.1:40000"

# --- tinh chỉnh ---
# LỖI THẬT = "reCAPTCHA evaluation failed" (recaptcha_quota): token bị đánh giá trượt -> phải RETRY TOKEN MỚI
# KIÊN NHẪN (như veo3top ~50 lần) tới khi 1 token qua. KHÔNG nghỉ account (không phải lỗi account).
GEN_ATTEMPTS = 40          # số lần bắn token MỚI/1 lượt lease (kiên nhẫn như veo3top) trước khi trả job về hàng đợi
BYPASS_RETRY = int(os.environ.get("VEO3TOP_BYPASS_RETRY", "5") or "5")  # bypass trượt liên tiếp bao nhiêu lần mới mint WEB token fallback
# CHỈ ANDROID (mặc định BẬT): bypass trượt (unusual/other) -> RETRY BYPASS kiên nhẫn, TUYỆT ĐỐI KHÔNG mint WEB token.
# WEB token = app_type WEB = dính reCAPTCHA -> đốt recaptcha_quota (881 lần đo được). android_bypass KHÔNG reCAPTCHA
# nên chỉ cần kiên nhẫn retry bypass. Tắt (0) -> cho phép fallback WEB như cũ.
VID_ANDROID_ONLY = os.environ.get("VEO3TOP_VID_ANDROID_ONLY", "1") == "1"
JOB_MAX_CYCLES = 40        # 1 job được chuyền/thử tối đa bao nhiêu lượt trước khi bỏ
REST_SOFT = 8              # sau 1 lượt 40 lần vẫn chưa qua -> nghỉ NGẮN 8s cho account thở rồi worker pull tiếp
# 429 recaptcha_quota = HẾT QUOTA VIDEO của account (per-account) -> grind token VÔ ÍCH. Thử VID_QUOTA_GIVEUP lần
# (phòng thoáng qua), chắc chắn 429 -> NGHỈ 6h + đổi account (chỉ 10 account Ultra -> nghỉ dài, khỏi đốt token factory).
# SUBMIT ĐỒNG THỜI/ACCOUNT: bắn dồn > ~4-5 submit cùng lúc/account -> kích THROTTLE (đã đo: 40 dồn -> 5 pass, 35 throttled).
# KHÔNG cứng 4: mỗi account TỰ ĐIỀU CHỈNH trần submit (AIMD như TCP) -> dính throttle giảm, chạy mượt tăng dần,
# tự dò trần tối đa của account/thời điểm đó -> khai thác TỐI ĐA mà vẫn không tự kích throttle.
SUBMIT_START = int(os.environ.get("VEO3TOP_VID_SUBMIT_START", "4") or "4")   # trần khởi đầu/account
SUBMIT_MIN   = int(os.environ.get("VEO3TOP_VID_SUBMIT_MIN", "1") or "1")     # sàn khi bị throttle liên tục
SUBMIT_MAX   = int(os.environ.get("VEO3TOP_VID_SUBMIT_MAX", "8") or "8")     # trần tối đa dò lên (>~5 đo thật, đủ room)
SUBMIT_UP_AFTER = int(os.environ.get("VEO3TOP_VID_SUBMIT_UP_AFTER", "6") or "6")  # N submit mượt liên tiếp -> +1 trần
SUBMIT_CONCURRENCY = SUBMIT_START   # (giữ tên cũ cho tương thích log/health)
# THROTTLE (USER_REQUESTS_THROTTLED) hồi trong vài giây -> nghỉ NGẮN, KHÔNG cách ly (khác hết-quota).
VID_THROTTLE_REST = float(os.environ.get("VEO3TOP_VID_THROTTLE_REST", "4") or "4")   # ~4s (+jitter)
VID_QUOTA_GIVEUP = int(os.environ.get("VEO3TOP_VID_QUOTA_GIVEUP", "5") or "5")
# Chỉnh qua GUI settings (pool_isolation_hours) -> env. Mặc định 6h (đồng bộ với ảnh).
VID_QUOTA_REST = int(os.environ.get("VEO3TOP_VID_QUOTA_REST", "21600") or "21600")   # 6h
REST_BASE = 20             # (chỉ dùng cho lỗi per-IP/account thật sự)
REST_MAX = 180
BACKOFF = 2.0              # chờ giữa 2 lần thử generate (nhẹ, + jitter)
POLL_MAX = 48
JOB_WAIT_TIMEOUT = 1200    # client chờ 1 job tối đa (s)


def _log(msg):
    print(f"[videofactory] {msg}", flush=True)


class AdaptiveLimiter:
    """Trần submit ĐỒNG THỜI TỰ ĐIỀU CHỈNH per-account (AIMD như điều khiển tắc nghẽn TCP).
    - Chạy mượt (submit 200) SUBMIT_UP_AFTER lần liên tiếp -> +1 trần (additive increase, dò lên từ từ).
    - Dính THROTTLE (429 rate) -> trần /=2 (multiplicative decrease, lùi nhanh) + reset streak.
    Kết quả: mỗi account tự hội tụ về trần TỐI ĐA mà không kích throttle -> khai thác tối đa, tự thích nghi theo
    account/thời điểm/độ nóng của Google (không cần chỉnh tay)."""
    def __init__(self, start=SUBMIT_START, lo=SUBMIT_MIN, hi=SUBMIT_MAX):
        self._lo = max(1, int(lo)); self._hi = max(self._lo, int(hi))
        self._limit = float(min(max(start, self._lo), self._hi))
        self._active = 0
        self._ok_streak = 0
        self._cond = threading.Condition()

    def acquire(self):
        with self._cond:
            while self._active >= int(self._limit):
                self._cond.wait(timeout=1.0)
            self._active += 1

    def release(self):
        with self._cond:
            self._active = max(0, self._active - 1)
            self._cond.notify()

    def on_ok(self):
        with self._cond:
            self._ok_streak += 1
            if self._ok_streak >= SUBMIT_UP_AFTER and self._limit < self._hi:
                self._limit = min(self._hi, self._limit + 1)
                self._ok_streak = 0
                self._cond.notify()      # đánh thức 1 chờ vì vừa nới trần

    def on_throttle(self):
        with self._cond:
            self._limit = max(self._lo, self._limit / 2.0)
            self._ok_streak = 0

    def snapshot(self):
        with self._cond:
            return int(self._limit), self._active


class Account:
    """1 account ultra trong pool + trạng thái runtime (nghỉ, thắng/thua, egress)."""
    def __init__(self, name, email, chrome_path, cache, bundle=""):
        self.name = name; self.email = email; self.chrome_path = chrome_path
        self.cache = cache
        self.bundle = bundle      # email|password|totp -> auto login password khi profile hết đăng nhập
        self.ipv6 = None          # IPv6 transport RIÊNG của account (như veo3top-b: mỗi account 1 IP)
        self.resume_at = 0.0
        self.rest_streak = 0
        self.busy = False
        self.limiter = AdaptiveLimiter()   # trần submit ĐỒNG THỜI/account TỰ ĐIỀU CHỈNH (AIMD) -> khai thác tối đa
        self.wins = 0; self.fails = 0
        self.egress_wins = {}     # egress name -> count
        self.last_kind = ""       # loại lỗi gần nhất (ratelimit/recaptcha_quota/unusual...)
        self._lock = threading.Lock()
        self._recovering = False       # đang được RecoveryManager chữa (nền)
        # NẠP LẠI NÓNG: account rời roster -> `retired=True`. Luồng thợ của NÓ tự
        # thoát ở ĐẦU vòng lặp kế, tức SAU khi job đang chạy xong — không cắt
        # ngang. Đây là cách dừng đúng một account mà không đụng `_running`
        # (thứ dừng CẢ nhà máy).
        self.retired = False
        # Phiên ở trạng thái nào — BA giá trị, không phải hai. Mặc định CHƯA RÕ
        # chứ không phải "chết": lúc vừa dựng object thì chưa ai kiểm gì cả.
        # Xem `phien_kiem.py` (sự cố 07/08/2026) — nhà máy ảnh và video dùng
        # CHUNG một bộ luật, đừng để hai bên trôi khác nhau.
        self.trang_thai_phien = pk.KHONG_KIEM_DUOC
        self._proj = pp.ProjectRotator()   # XOAY + TỰ CHỮA project — chống gãy 24/7 (dùng chung với ảnh)
        import re as _re
        m = _re.search(r"Copy \((\d+)\)", str(chrome_path or ""))
        self._copyn = int(m.group(1)) if m else (abs(hash(email or "")) % 80)
        # VIDEO dùng dải port RIÊNG với ẢNH (warm 988x, login_wid 14x) -> 2 factory không đụng cổng
        self.relogin_port = 9880 + self._copyn
        self._login_wid = 140 + self._copyn

    def recover(self, log=lambda *_: None):
        """Chạy NỀN bởi RecoveryManager: clear+login(password)+warm+cookie. Thành công+validate -> clear rest.
        Thất bại -> nghỉ dài + báo. KHÔNG chặn worker (worker chỉ submit + park + requeue)."""
        import account_manager as am
        self._recovering = True
        try:
            nd = am.full_recover(self.email, self.bundle, self.chrome_path, self.cache, self._copyn,
                                 name=self.name, login_worker_id=self._login_wid, warm_port=self.relogin_port, log=log)
            if nd and nd.get("bearer") and am.validate_auth(nd):
                self.clear_rest()
                self.phien_ok = True
                log(f"✅ VIDEO: {self.email} ĐÃ CHỮA XONG (login+warm+cookie) -> hoạt động lại.")
                return nd
            self.rest(3600)
            log(f"⚠️ VIDEO: {self.email} chữa KHÔNG thành (có thể bị chặn Flow / cần kiểm tra thủ công) -> nghỉ 1h.")
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
        """XOAY VÒNG + TỰ CHỮA project (chống gãy 24/7). LƯU Ý video I2V: media_id gắn project -> cache media kèm project."""
        return self._proj.next(cookie, fallback=fallback, log=lambda m: _log(f"{self.email}: {m}"))

    def mark_project_bad(self, pid):
        self._proj.mark_bad(pid, log=lambda m: _log(f"{self.email}: {m}"))

    # `phien_ok` giữ TÊN CŨ cho mã cũ (đường ra 200 và đường 401 vẫn ghi kiểu
    # `= True/False`), nhưng đọc nó nghĩa là "ĐÃ CHỨNG MINH được là sống".
    @property
    def phien_ok(self):
        return self.trang_thai_phien == pk.SONG

    @phien_ok.setter
    def phien_ok(self, v):
        self.trang_thai_phien = pk.SONG if v else pk.CHET_CHAC_CHAN

    def auth(self, force=False):
        """Auth NHẸ (không mở chrome -> KHÔNG chặn worker). BỐN BƯỚC, RẺ TỚI ĐẮT.

        1. cache cục bộ còn hạn -> dùng · 2. có cookie -> làm mới thẻ từ cookie ·
        3. KHO SERVER có phiên -> KÉO VỀ + ghi cache · 4. hết cách -> None.

        Bước 3 thêm 07/08/2026, giống hệt nhà máy ảnh: hôm đó server giữ 96 tài
        khoản `ready` mà máy chỉ có 65 bản ghi `_auth_cache`, nên 31 tài khoản có
        phiên hợp lệ TRÊN SERVER bị đọc thành "phiên chết" và bị gõ lại mật khẩu.
        Xem chú thích dài ở `kho_phien.py`.
        """
        d = self.cache._load(self.email)
        if not force and self.cache._fresh(d):
            return d
        if d and d.get("cookie") and d.get("project"):
            nd = self.cache._refresh_from_cookie(self.email, d)
            if nd:
                return nd
        if not (d and d.get("cookie")):
            nd, _kq = kp.keo_phien_ve(self.email, self.cache,
                                      log=lambda m: _log(f"{self.email}: {m}"))
            if nd and nd.get("cookie") and nd.get("project"):
                return self.cache._refresh_from_cookie(self.email, nd)
        return None

    def note_egress_win(self, egress):
        self.egress_wins[egress] = self.egress_wins.get(egress, 0) + 1


class VideoFactory:
    def __init__(self, token_chromes=2, token_port=9700, ipv6_port=9960, log=_log):
        self.log = log
        self.q = queue.Queue()
        self.cache = AuthCache(log=lambda *_: None)
        self.accounts = []
        self.factory = None
        self.transport = None
        self.egress = [("ipmay", None)]     # luôn có IP máy; WARP/IPv6 thêm nếu dùng được
        self.token_chromes = token_chromes
        self.token_port = token_port
        self.ipv6_port = ipv6_port
        self._running = False
        self._workers = []
        # cache media_id theo (email, ảnh) — học veo3top: job retry/chuyền account không upload lại
        self._media_cache = {}
        self._media_lock = threading.Lock()
        # counters
        self.total_done = 0; self.total_fail = 0; self.started_ts = time.time()
        self._clock = threading.Lock()
        self.recovery = am.RecoveryManager(log=self.log)   # chữa account lỗi ở NỀN (không chặn tiến độ)
        # HẠN MỨC đăng nhập lại bằng mật khẩu — xem `phien_kiem.HanMucChua`.
        # ⚠ Ảnh và video là HAI TIẾN TRÌNH RỜI NHAU, nên đây là hai van rời nhau;
        # tổng số lượt đăng nhập/giờ mà Google thấy là TỔNG của hai bên. Chỉnh
        # `VEO3TOP_CHUA_MOI_LUOT` phải nhớ điều đó.
        self.han_muc_chua = pk.HanMucChua(log=self.log)
        # ── NẠP LẠI NÓNG (07/08/2026) — xem chú thích dài ở `reload_accounts` ──
        self._nap_lai_lock = rr.KhoaNapLai()
        self._wpa = int(os.environ.get("VEO3TOP_POOL_WORKERS_PER_ACCOUNT", "7") or "7")
        self._ipv6_slot = 0
        self.n_kho_san_sang = 0
        self.last_reload_ts = 0.0
        self.reload_count = 0
        self._da_canh_bao_lech = False

    def _startup_check(self, accounts=None):
        """Đầu giờ: kiểm nhanh từng account. Luật nằm ở `phien_kiem.kiem_roster` —
        CHUNG một hàm với nhà máy ẢNH, cố ý không chép bản thứ hai (bản cũ là hai
        bản chép tay của cùng một lỗi, và sửa một bên là chắc chắn quên bên kia).

        `accounts` — chỉ kiểm một PHẦN roster. Đường nạp lại nóng dùng nó để kiểm
        riêng account VỪA THÊM; kiểm cả roster sẽ bắn POST validate vào những
        account đang chạy job của khách, hoàn toàn vô ích.

        Bản cũ: `except Exception: ok=False` + `rest(1800)` + đăng nhập lại ngay
        cho MỌI lượt kiểm trượt, 64 lượt bắn cùng lúc. Xem `phien_kiem.py` cho
        toàn bộ số đo ngày 07/08/2026 và vì sao nó tự nuôi chính nó."""
        accounts = self.accounts if accounts is None else accounts
        kq = pk.kiem_roster(
            accounts,
            submit_chua=lambda a: self.recovery.submit(a.email, lambda a=a: a.recover(self.log)),
            log=self.log, han_muc=self.han_muc_chua, nhan="VIDEO",
        )
        return kq["song"]

    def _cached_media(self, email, image_path, project=None):
        # KÈM project: media_id gắn với project -> đổi project phải upload lại (tránh dùng media_id sai project)
        with self._media_lock:
            return self._media_cache.get((email, image_path, project))

    def _put_media(self, email, image_path, media_id, project=None):
        with self._media_lock:
            self._media_cache[(email, image_path, project)] = media_id

    # ---------- khởi tạo ----------
    def start(self):
        # 1) accounts
        pool = load_pool_accounts()
        self.accounts = [Account(a["name"], a["email"], a["chrome_path"], self.cache, a.get("bundle", "")) for a in pool]
        self._startup_check()

        # 2) egress: android_bypass chạy 100% trên IP MÁY (đã đo video 13/13) -> KHÔNG cần IPv6.
        #    IPv6Transport gọi netsh cho MỖI account -> netsh chất đống -> KẸT + NẶNG MÁY (giống ảnh).
        #    -> MẶC ĐỊNH TẮT IPv6 (VEO3TOP_POOL_USE_IPV6=1 để bật lại nếu muốn phân tán per-IP volume lớn).
        self.transport = None
        if os.environ.get("VEO3TOP_POOL_USE_IPV6", "0") != "1":
            self.log("egress = DIRECT IP máy (IPv6 TẮT — bypass chạy IP máy, tránh netsh nặng)")
            return self._start_rest()
        ok6 = self._cap_ipv6(self.accounts)
        self.log(f"IPv6 RIÊNG mỗi account: {ok6}/{len(self.accounts)} account có IP riêng (như veo3top-b)")
        return self._start_rest()

    # ---------- tiện ích dùng chung cho start() VÀ nạp lại nóng ----------
    def _cap_ipv6(self, accounts):
        """Cấp IPv6 riêng cho một NHÓM account. Port lấy từ bộ đếm tăng dần chứ
        KHÔNG phải `enumerate`: account thêm lúc nạp nóng mà tái dùng port của
        account đang chạy thì hai transport tranh nhau một cổng, và cái thua là
        cái đang phục vụ khách."""
        if os.environ.get("VEO3TOP_POOL_USE_IPV6", "0") != "1":
            return 0
        ok6 = 0
        for a in accounts:
            if a.ipv6:
                ok6 += 1; continue
            try:
                tr = ip6t.IPv6Transport(f"vf_{a.name}", port=self.ipv6_port + self._ipv6_slot,
                                        log=lambda *_: None)
                self._ipv6_slot += 1
                if tr.start():
                    a.ipv6 = tr; ok6 += 1
            except Exception:
                pass
        return ok6

    def _mo_tho(self, accounts):
        """Mở `_wpa` luồng thợ cho mỗi account trong nhóm. Luồng tự thoát khi
        `account.retired` -> gỡ account không cần đụng `_running`."""
        n = 0
        for a in accounts:
            for _ in range(self._wpa):
                t = threading.Thread(target=self._worker, args=(a,), daemon=True)
                t.start(); self._workers.append(t); n += 1
        return n

    def _start_rest(self):
        self.log(f"EGRESS: {[e[0] for e in self.egress]}")
        # 3) ĐƯỜNG CHÍNH giờ = android_bypass (submit curl thuần, KHÔNG mint captcha) -> KHÔNG cần token factory.
        #    Token factory (mint WEB token thật qua Chrome) chỉ là FALLBACK khi bypass trượt -> khởi động LAZY
        #    (chỉ mở Chrome khi thực sự cần), đỡ tốn Chrome/CPU khi bypass chạy tốt (~100%).
        self.factory = None
        self._factory_lock = threading.Lock()
        # 4) account workers — NHIỀU LUỒNG/ACCOUNT (như veo3top-b 7-20 luồng/tk, KHÔNG phải 1!)
        #    Mỗi account chạy song song nhiều video -> tận dụng hết account (render 1 cái, grind cái khác).
        self._running = True
        wpa = self._wpa
        self._mo_tho(self.accounts)
        self.log(f"đã khởi động {len(self._workers)} worker ({len(self.accounts)} account x {wpa} luồng)")
        # KEEP-WARM: giữ account video LUÔN TƯƠI (tự chữa 24/7). Video chạy BURST -> account nằm không
        # giữa các burst -> bearer(~30p)/cookie hết hạn -> lúc phase video chạy mới phát hiện 0 SỐNG (chữa chậm+fail).
        # Thread này refresh+validate mỗi ~10p, ngả -> chữa NỀN TRƯỚC -> khi video cần, account đã SỐNG sẵn.
        self._warm_thread = threading.Thread(target=self._keep_warm_loop, daemon=True)
        self._warm_thread.start()
        return True

    def _ensure_factory(self):
        """LAZY: mở token factory WEB (Chrome mint token thật) LẦN ĐẦU khi bypass fail cần fallback.
        Trả factory (đã _started) hoặc None. Bypass chạy tốt (~100%) thì KHÔNG bao giờ gọi -> không mở Chrome."""
        if self.factory and getattr(self.factory, "_started", False):
            return self.factory
        with self._factory_lock:
            if self.factory and getattr(self.factory, "_started", False):
                return self.factory
            try:
                self.log("bypass fail -> KHỞI ĐỘNG token factory WEB (fallback, mở Chrome mint token thật)…")
                self.factory = tf.get_factory(mode="blank", n_chromes=self.token_chromes,
                                              base_port=self.token_port, log=self.log,
                                              ipv6=True, clean=True, visible=True)
            except Exception as e:
                self.log(f"không khởi động được token factory WEB: {e}")
                self.factory = None
        return self.factory if (self.factory and getattr(self.factory, "_started", False)) else None

    def _keep_warm_loop(self):
        """Chủ động làm tươi account (không đợi tới lúc video chạy mới biết chết). refresh bearer từ cookie
        (giữ session ấm như ảnh chạy liên tục) + validate; chết -> chữa NỀN. Mỗi ~10 phút (bearer chết ~30p)."""
        WARM_SECS = int(os.environ.get("VEO3TOP_POOL_KEEPWARM_SECS", "600") or "600")
        # lượt đầu đợi 90s cho startup ổn định
        for _ in range(9):
            if not self._running: return
            time.sleep(10)
        while self._running:
            # Chụp danh sách MỘT LẦN: `reload_accounts` thay `self.accounts`
            # nguyên khối, duyệt thẳng lên thuộc tính là duyệt hai danh sách
            # khác nhau giữa chừng.
            ds = [a for a in list(self.accounts)
                  if not (getattr(a, "_recovering", False) or a.retired)]
            if not self._running: return
            # ĐI QUA ĐÚNG BỘ LUẬT CHUNG (`phien_kiem`): vòng này trước đây là bản
            # chép thứ ba của cùng một lỗi — `except: ok=False` rồi `rest(1800)` +
            # đăng nhập lại, chạy TUẦN TỰ trên CẢ roster mỗi 10 phút. Với account
            # khoẻ mà mạng chập một nhịp, nó cắt 30 phút công suất và đốt một lượt
            # đăng nhập, cứ thế mỗi 10 phút.
            kq = pk.kiem_roster(
                ds,
                submit_chua=lambda a: self.recovery.submit(a.email, lambda a=a: a.recover(self.log)),
                log=self.log, han_muc=self.han_muc_chua, nhan="VIDEO keep-warm",
                # ÉP làm mới thẻ từ cookie: đó mới là phần "keep-WARM" — cookie
                # được chạm mỗi 10 phút nên không nguội giữa hai đợt video.
                kiem=lambda a, ngu=time.sleep: pk.kiem_co_thu_lai(a, ngu=ngu, force=True),
            )
            _log(f"keep-warm VIDEO: {kq['song']} tươi, {kq['chet_chac_chan']} chết chắc chắn "
                 f"-> chữa NỀN chủ động ({kq['cho_chua']} xếp hàng), {kq['khong_kiem_duoc']} chưa rõ "
                 f"-> ĐỂ YÊN, vẫn giao việc")
            # CẢNH BÁO KHO LỆCH: kho có phiên mà nhà máy không dùng được = chạy
            # chậm nhiều lần MÀ KHÔNG BÁO LỖI. Đó chính là cách sự cố 07/08/2026
            # sống sót cả ca. Kêu MỘT lần mỗi đợt lệch, không spam.
            try:
                _cb = self.canh_bao_lech()
                if _cb and not self._da_canh_bao_lech:
                    self._da_canh_bao_lech = True
                    self.log("⚠️ " + _cb)
                elif not _cb:
                    self._da_canh_bao_lech = False
            except Exception:
                pass
            # DỌN RÁC log định kỳ (không phình vô tận) — mỗi vòng keep-warm (~10p) cắt log nếu quá lớn, giữ phần mới.
            try:
                import diag_report as _dr
                _dr.rotate_all()
            except Exception:
                pass
            # ngủ WARM_SECS, chia nhỏ để stop() thoát nhanh
            slept = 0
            while self._running and slept < WARM_SECS:
                time.sleep(5); slept += 5

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
        _kill_video_chromes()   # BACKSTOP: kill token chrome video còn sót -> KHÔNG zombie khi tắt

    # ---------- xử lý 1 job trên 1 account ----------
    def _process(self, account, job):
        """Trả 'success' | 'ratelimit' | ('fail', reason). Ghi kết quả vào job nếu success/fail."""
        auth = account.auth()
        if not auth or not auth.get("bearer") or not auth.get("project"):
            return "ratelimit"   # account chưa có auth -> coi như nghỉ, để account khác làm
        bearer = auth["bearer"]; cookie = auth.get("cookie")
        project = account.next_project(cookie, fallback=auth["project"])   # XOAY project (chống gãy 24/7)
        seed = job.get("seed") or (int(time.time() * 1000) % 2_000_000)
        aspect = job.get("aspect") or "VIDEO_ASPECT_RATIO_LANDSCAPE"

        # 1) upload ảnh reference bằng CHÍNH account này -> media_id (GẮN project vừa xoay).
        #    Cache theo (account, ảnh, PROJECT) -> đổi project thì upload lại đúng project (không dùng media_id sai).
        media_id = self._cached_media(account.email, job["image_path"], project)
        if not media_id:
            sess = f";{int(time.time()*1000)}"
            media_id, err = fc.upload_image(bearer, project, sess, job["image_path"], aspect=aspect)
            if not media_id:
                if err and err.startswith("401"):
                    a2 = account.auth(force=True)   # refresh cookie nhanh
                    if a2 and a2.get("bearer"):
                        bearer = a2["bearer"]; cookie = a2.get("cookie"); project = a2.get("project") or project
                        media_id, err = fc.upload_image(bearer, project, sess, job["image_path"], aspect=aspect)
                    if not media_id:
                        # vẫn 401 -> chữa NỀN + PARK + requeue sang account SỐNG (không fail cứng job).
                        # 401 hai lần liên tiếp trên lượt gọi THẬT = bằng chứng phiên chết;
                        # nghỉ NGẮN tăng dần + XIN LƯỢT chữa (hạn mức) — xem `phien_kiem`.
                        account.trang_thai_phien = pk.CHET_CHAC_CHAN
                        giay = pk.nghi_tang_dan(account)
                        kq = self.han_muc_chua.xep_hang(
                            account.email,
                            lambda a=account: self.recovery.submit(a.email, lambda a=a: a.recover(self.log)))
                        self.log(f"VIDEO: {account.email} 401 lúc upload ref -> nghỉ {int(giay)}s + "
                                 f"{'chữa NỀN ngay' if kq == 'chay' else 'XẾP HÀNG chờ lượt chữa'}, PARK + requeue.")
                        return "retry_soft"
                if not media_id:
                    return ("fail", f"upload ref fail: {err}")
            self._put_media(account.email, job["image_path"], media_id, project)

        # 2) generate I2V — RETRY TOKEN MỚI KIÊN NHẪN (như veo3top). Lỗi chính = reCAPTCHA đánh giá trượt
        #    -> cứ bắn token mới, backoff NGẮN, KHÔNG nghỉ account. Xoay egress phòng lỗi per-IP.
        auth_fails = 0
        bypass_fails = 0
        quota_streak = 0   # số lần 429 (recaptcha_quota) LIÊN TIẾP -> đủ ngưỡng thì nghỉ 3h + đổi account
        for attempt in range(GEN_ATTEMPTS):
            # Submit qua IPv6 RIÊNG của account này (như veo3top-b) -> IP độc lập, quota reCAPTCHA không chung.
            # KHÔNG xoay ở đây; chỉ xoay khi 429 per-IP thật (nhánh ratelimit).
            if account.ipv6:
                ename, eproxy = "ipv6", account.ipv6.proxy_url()
            else:
                ename, eproxy = "ipmay", None
            # ĐƯỜNG CHÍNH: android_bypass (giải mã tst) — bỏ qua reCAPTCHA, submit curl thuần, KHÔNG mint token
            # (đó là nguồn UNUSUAL). Đã đo video 13/13 submit 200. Token='android_bypass', app_type=ANDROID.
            payload = fc.build_payload(job["prompt"], project, fc.BYPASS_TOKEN, seed,
                                       aspect=aspect, reference_media_id=media_id, app_type=fc.APP_TYPE_ANDROID)
            account.limiter.acquire()   # trần submit đồng thời/account TỰ ĐIỀU CHỈNH (AIMD) -> không burst kích throttle
            try:
                kind, data = fc.generate(bearer, payload, url=fc.GEN_I2V, proxy=eproxy, bypass=True)
            finally:
                account.limiter.release()
            # bypass trượt (unusual/other) -> RETRY bypass vài lần (rẻ); liên tiếp >= BYPASS_RETRY mới mint WEB
            # token thật fallback. KHÔNG fallback ratelimit/quota/auth/project (xử lý ở nhánh dưới).
            if kind in ("unusual", "other"):
                bypass_fails += 1
                # CHỈ ANDROID: KHÔNG mint WEB token (nguồn recaptcha_quota) -> chỉ RETRY BYPASS kiên nhẫn (như veo3top).
                if VID_ANDROID_ONLY or bypass_fails < BYPASS_RETRY:
                    time.sleep(0.4 + random.uniform(0, 0.4)); continue
                fac = self._ensure_factory()
                tok = fac.get() if fac else None
                if tok:
                    payload = fc.build_payload(job["prompt"], project, tok, seed,
                                               aspect=aspect, reference_media_id=media_id, app_type=fc.APP_TYPE_WEB)
                    account.limiter.acquire()
                    try:
                        kind, data = fc.generate(bearer, payload, url=fc.GEN_I2V, proxy=eproxy, cookie=cookie)
                    finally:
                        account.limiter.release()
            if kind == "ok":
                account.phien_ok = True   # ra 200 = phiên còn sống (dùng cho cảnh báo kho lệch)
                account.limiter.on_ok()   # submit mượt -> tín hiệu nới trần dần (AIMD)
                quota_streak = 0          # submit 200 -> KHÔNG hết quota -> reset (chống 6h oan do đếm dồn xen kẽ)
                account.note_egress_win(ename)
                op = (fc.operation_names(data) or [None])[0]
                if not op:
                    continue
                # 3) poll render
                pkind, _ = fc.poll_until_done(bearer, [op], max_attempts=POLL_MAX, interval=5)
                if pkind == "failed":
                    job["_result"] = (False, {"media_id": op}, "render FAILED (policy) - can rewrite prompt")
                    return ("fail", "render policy")
                if pkind == "auth":
                    a2 = account.auth(force=True)
                    if a2: bearer = a2["bearer"]; cookie = a2.get("cookie")
                if pkind != "done":
                    return ("fail", "render timeout")
                # 4) download
                os.makedirs(os.path.dirname(job["out_path"]), exist_ok=True)
                for _ in range(10):
                    try:
                        n, _u = fc.media_url_and_download(op, cookie, job["out_path"])
                        job["_result"] = (True, {"media_id": op, "bytes": n, "account": account.email,
                                                 "egress": ename}, "")
                        return "success"
                    except Exception:
                        a2 = account.auth(force=True)
                        if a2: cookie = a2.get("cookie")
                        time.sleep(3)
                return ("fail", "download fail")
            elif kind == "auth":
                # 401: (1) refresh cookie NHANH (inline); vẫn 401 -> chữa NỀN (login+warm+cookie) + PARK +
                # requeue sang account SỐNG. Worker KHÔNG mở chrome -> không chặn tiến độ, tool không khựng.
                auth_fails += 1
                if auth_fails == 1:
                    a2 = account.auth(force=True)
                    if a2 and a2.get("bearer"):
                        bearer = a2["bearer"]; cookie = a2.get("cookie"); project = a2.get("project") or project
                        quota_streak = 0   # 401 (bearer hết hạn) KHÁC hết-quota -> reset, không cộng dồn vào 6h
                        continue
                account.last_kind = "auth_recovering"
                # 401 TRÊN LƯỢT GỌI THẬT = bằng chứng phiên chết. Đây là lý do
                # account "chưa rõ" vẫn được giao việc: chỗ này mới kết luận nổi.
                account.trang_thai_phien = pk.CHET_CHAC_CHAN
                giay = pk.nghi_tang_dan(account)   # NGẮN + tăng dần, không phải 1800s cứng
                kq = self.han_muc_chua.xep_hang(
                    account.email, lambda a=account: self.recovery.submit(a.email, lambda a=a: a.recover(self.log)))
                self.log(f"VIDEO: {account.email} 401 lúc chạy thật -> nghỉ {int(giay)}s + "
                         f"{'chữa NỀN ngay' if kq == 'chay' else 'XẾP HÀNG chờ lượt chữa'}, "
                         f"PARK + requeue (account khác chạy tiếp).")
                return "retry_soft"
            else:
                account.last_kind = f"{kind}@{ename}"
                # NỘI DUNG BỊ CHẶN lúc SUBMIT (PROMPT VIDEO vi phạm): account/token nào cũng bị -> đổi VÔ ÍCH.
                # Trả THẲNG worker (giữ 'policy') -> worker VIẾT LẠI PROMPT VIDEO. (Video hay fail ở render/poll hơn -
                # đã xử ở trên; đây bắt nốt trường hợp chặn ngay submit.)
                _vb = str(data or "").upper()
                if kind == "other" and ("UNSAFE" in _vb or "PUBLIC_ERROR_MINOR" in _vb or "SAFETY" in _vb or "PROHIBITED" in _vb):
                    job["_result"] = (False, {}, f"submit policy - can rewrite prompt: {str(data or '')[:100]}")
                    return ("fail", "submit policy")
                if kind in ("ratelimit", "ip_block"):
                    # per-IP -> xoay IPv6 RIÊNG của account này rồi bắn lại
                    quota_streak = 0
                    if ename == "ipv6" and account.ipv6:
                        try: account.ipv6.rotate()
                        except Exception: pass
                    time.sleep(1.5 + random.uniform(0, 1.0))
                elif pp.is_project_error(kind, data):
                    # LỖI CẤP-PROJECT (PERMISSION_DENIED/NOT_FOUND) -> loại project + REQUEUE (lần sau xoay project
                    # TỐT + upload reference lại đúng project). KHÔNG grind token (không phải lỗi token). Self-heal 24/7.
                    account.mark_project_bad(project)
                    return "retry_soft"
                elif kind == "throttle":
                    # GIỚI HẠN TỐC ĐỘ/BURST (USER_REQUESTS_THROTTLED) — hồi trong VÀI GIÂY. NGHỈ NGẮN rồi bắn lại
                    # CÙNG account. KHÔNG tính quota_streak, TUYỆT ĐỐI KHÔNG cách ly (đã đo: ngừng burst 1-2' -> 200 ngay).
                    quota_streak = 0
                    account.limiter.on_throttle()   # TỰ HẠ trần submit/account (AIMD) -> lần sau ít burst hơn
                    lim, _act = account.limiter.snapshot()
                    account.last_kind = f"throttle(lim={lim})"
                    time.sleep(VID_THROTTLE_REST + random.uniform(0, VID_THROTTLE_REST))
                elif kind == "recaptcha_quota":
                    # HẾT QUOTA THẬT (RESOURCE_EXHAUSTED KHÁC throttle — hết quota model/ngày). Hiếm với video Ultra.
                    # Thử vài lần (phòng thoáng qua), CHẮC CHẮN -> NGHỈ dài + ĐỔI ACCOUNT (retry_soft).
                    quota_streak += 1
                    if quota_streak >= VID_QUOTA_GIVEUP:
                        account.rest(VID_QUOTA_REST)
                        self.log(f"VIDEO: {account.email} HẾT QUOTA thật {quota_streak} lần -> nghỉ {VID_QUOTA_REST//3600}h, đổi account khác")
                        return "retry_soft"
                    time.sleep(1.0 + random.uniform(0, 0.8))
                else:
                    # unusual = token trượt reCAPTCHA THẬT -> bắn TOKEN MỚI nhanh (fresh token điểm cao hơn, như veo3top)
                    quota_streak = 0
                    try:
                        if self.factory: self.factory.note_error()   # tích lỗi -> reset chrome fresh (chỉ khi WEB fallback đang chạy)
                    except Exception: pass
                    time.sleep(1.0 + random.uniform(0, 0.8))
                if attempt and attempt % 10 == 0:
                    self.log(f"account {account.email} [{account.last_kind}] retry token mới {attempt}/{GEN_ATTEMPTS} (kiên nhẫn)")
                continue
        return "retry_soft"   # 40 lần token vẫn chưa qua -> nghỉ NGẮN, trả job về hàng đợi (KHÔNG đổ lỗi account)

    # ---------- vòng lặp 1 account-worker ----------
    def _worker(self, account):
        # `account.retired` kiểm ở ĐẦU vòng -> chỉ thoát SAU khi job hiện tại
        # xong. Gỡ một account KHÔNG BAO GIỜ cắt ngang job đang chạy.
        while self._running and not account.retired:
            w = account.rest_remaining()
            if w > 0:
                time.sleep(min(w, 5)); continue
            try:
                job = self.q.get(timeout=2)
            except queue.Empty:
                continue
            if job.get("_cycles", 0) > JOB_MAX_CYCLES:
                job["_result"] = (False, {}, "quá nhiều lượt - bỏ scene (chạy lại sau tự làm tiếp)")
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
                # reCAPTCHA chưa qua sau GEN_ATTEMPTS token -> KHÔNG nghỉ account (7 luồng khác vẫn chạy),
                # chỉ trả job về hàng đợi, worker này pull job kế tiếp ngay -> tận dụng account liên tục.
                account.fails += 1
                self.q.put(job)   # requeue, worker grind job khác luôn (không block account)
            else:  # ('fail', reason)
                if "_result" not in job:
                    reason = outcome[1] if isinstance(outcome, tuple) and len(outcome) > 1 else str(outcome)
                    job["_result"] = (False, {}, reason)
                with self._clock: self.total_fail += 1
                job["_event"].set()

    # ---------- submit (gọi từ HTTP) ----------
    def submit(self, image_path, prompt, out_path, aspect=None, seed=None):
        job = {"image_path": image_path, "prompt": prompt, "out_path": out_path,
               "aspect": aspect, "seed": seed, "_event": threading.Event()}
        self.q.put(job)
        if not job["_event"].wait(timeout=JOB_WAIT_TIMEOUT):
            return False, {}, "video_factory: timeout chờ job"
        return job.get("_result", (False, {}, "no result"))

    # ================================================================
    # NẠP LẠI NÓNG roster — thêm/bỏ account mà KHÔNG khởi động lại
    # ================================================================
    #
    # ═══ SỐ ĐO LÀM BẰNG CHỨNG, ĐỪNG GỠ KHỐI NÀY ═══
    #
    # 07/08/2026 nhà máy ẢNH chạy 8/64 account đăng nhập được trong khi kho có
    # 96/96 sẵn sàng; ảnh mất 600-780 giây thay vì ~90 và một khách thật có 4
    # job bò ở tốc độ đó. Nhà máy VIDEO có ĐÚNG cái lỗi ấy: `start()` đọc
    # `load_pool_accounts()` một lần rồi thôi. Nó chưa cắn vì video ít account
    # hơn — nhưng "chưa cắn" không phải là "đã sửa".

    def _co_phien(self, email):
        """Account có cookie dùng được trong `_auth_cache` chưa? (điều kiện để
        `Account.auth()` chạy mà KHÔNG mở Chrome)."""
        try:
            d = self.cache._load(email)
            return bool(d and d.get("cookie"))
        except Exception:
            return False

    def _so_lieu_roster(self):
        accounts = list(self.accounts)   # chụp một lần: `self.accounts` bị thay nguyên khối
        # `logged_in`/`not_logged_in` ĐÃ BỊ BỎ (07/08/2026) — một con số mang hai
        # nghĩa. Xem `phien_kiem.dem_trang_thai`.
        so = pk.dem_trang_thai(accounts)
        so["dang_ban"] = sum(1 for a in accounts if a.busy)
        so["kho_san_sang"] = self.n_kho_san_sang
        return so

    def canh_bao_lech(self):
        # `phien_dung_duoc` = SỐNG + CHƯA RÕ: account chưa rõ VẪN được giao việc.
        return rr.canh_bao_kho_lech(
            pk.dem_trang_thai(list(self.accounts))["phien_dung_duoc"], self.n_kho_san_sang,
            uptime_s=time.time() - self.started_ts,
        )

    def reload_accounts(self):
        """Đọc lại `load_pool_accounts()` rồi cập nhật roster ĐANG CHẠY.

        KHÔNG đụng account đang bận: account rời roster mà `busy`/đang được chữa
        thì giữ lại tới lượt nạp sau. Account còn trong roster GIỮ NGUYÊN object
        cũ — dựng lại là vứt cookie/bearer/IPv6/cache media và job đang chạy trên
        đó chết theo."""
        with self._nap_lai_lock:
            pool = load_pool_accounts()
            truoc = self._so_lieu_roster()
            if not pool:
                # settings.yaml đang được `account_bridge` ghi dở là ca có thật
                # (ghi qua file .tmp rồi `os.replace`, nhưng yaml hỏng vẫn ra
                # danh sách rỗng). Coi rỗng là sự thật = tự gỡ hết account tốt.
                self.log("⚠️ nạp lại nóng: KHO đọc ra RỖNG -> giữ nguyên roster đang chạy "
                         "(settings.yaml đang ghi dở?), KHÔNG gỡ account nào")
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
                dang_ban=lambda a: bool(a.busy) or bool(getattr(a, "_recovering", False)),
                tao_moi=lambda d: Account(d["name"], d["email"], d["chrome_path"],
                                          self.cache, d.get("bundle", "")),
            )
            moi = [a for a in kq.danh_sach if a not in cu]
            # Gán NGUYÊN KHỐI (không sửa tại chỗ): luồng đọc luôn thấy danh sách
            # cũ trọn vẹn hoặc danh sách mới trọn vẹn, không bao giờ nửa vời.
            self.accounts = kq.danh_sach
            for a in cu:
                if a in kq.danh_sach:
                    continue
                a.retired = True     # luồng thợ tự thoát SAU job hiện tại
                try:
                    if a.ipv6: a.ipv6.stop()
                except Exception:
                    pass
            self.last_reload_ts = time.time(); self.reload_count += 1

            if moi:
                self._cap_ipv6(moi)
                if self._running:
                    self._mo_tho(moi)
                try:
                    self._startup_check(moi)   # chỉ kiểm account MỚI
                except Exception as e:
                    self.log(f"nạp lại nóng: kiểm account mới lỗi ({e}) — vẫn cho chạy")

            sau = self._so_lieu_roster()
            canh_bao = self.canh_bao_lech()
            self.log(
                f"♻️ nạp lại nóng roster VIDEO: {truoc['accounts']} -> {sau['accounts']} account "
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
        vph = (self.total_done / up * 3600) if up > 5 else 0
        ds = list(self.accounts)
        out = {
            "accounts": [
                {"name": a.name, "email": a.email, "resting_in": round(a.rest_remaining(), 1),
                 "busy": a.busy, "wins": a.wins, "fails": a.fails, "egress_wins": a.egress_wins,
                 "submit_limit": (_snap := a.limiter.snapshot())[0], "submit_active": _snap[1],
                 "trang_thai_phien": a.trang_thai_phien,
                 "last_kind": getattr(a, "last_kind", "")}
                for a in ds
            ],
            "egress": [e[0] for e in self.egress],
            "queue": self.q.qsize(),
            "done": self.total_done, "fail": self.total_fail,
            "video_per_hour": round(vph, 1), "uptime_s": round(up),
            # ── nạp lại nóng + cảnh báo kho lệch (07/08/2026) ──────────────
            # Hôm đó KHÔNG khoá nào trong `/health` nói được rằng nhà máy đang
            # chạy 8/96 tài khoản: mọi chỉ số đều xanh, chỉ có ảnh chậm 8 lần.
            #
            # ⚠ KHOÁ `logged_in` ĐÃ BỊ XOÁ HẲN, ĐỪNG THÊM LẠI — nó là BỘ ĐẾM
            # TIẾN TRÌNH KIỂM đội lốt số account đăng nhập được (đo trên nhà máy
            # ảnh: 8 -> 41 trong 30 phút). Bốn khoá `phien_*` bên dưới thay nó,
            # mỗi con số trả lời đúng một câu hỏi — xem `phien_kiem.dem_trang_thai`.
            "kho_san_sang": self.n_kho_san_sang,
            "chua_cho_luot": self.han_muc_chua.cho_doi(),
            "roster_canh_bao": self.canh_bao_lech(),
            "reload_count": self.reload_count,
            "last_reload_ts": round(self.last_reload_ts),
        }
        so = pk.dem_trang_thai(ds)
        so.pop("accounts", None)   # `accounts` ở trên là DANH SÁCH, không phải số đếm
        out.update(so)
        return out


# ---------------- HTTP ----------------
_FACTORY = None

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, _FACTORY.health())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send(400, {"error": "bad json"}); return
        if self.path == "/shutdown":
            self._send(200, {"ok": True}); threading.Thread(target=_FACTORY.stop, daemon=True).start(); return
        if self.path == "/reload_accounts":
            # NẠP LẠI NÓNG — đường thay thế cho "khởi động lại để đổi account".
            # Khởi động lại giết mọi job đang chạy dở; đường này KHÔNG đụng
            # account đang bận (xem `VideoFactory.reload_accounts`).
            try:
                self._send(200, _FACTORY.reload_accounts())
            except Exception as e:
                _FACTORY.log(f"nạp lại nóng LỖI: {type(e).__name__}: {e}")
                self._send(200, {"ok": False, "loi": f"{type(e).__name__}: {e}"})
            return
        if self.path == "/generate":
            img = data.get("image_path"); pr = data.get("prompt"); outp = data.get("out_path")
            if not img or not outp:
                self._send(400, {"error": "thieu image_path/out_path"}); return
            ok, info, err = _FACTORY.submit(img, pr, outp, aspect=data.get("aspect"), seed=data.get("seed"))
            self._send(200, {"success": bool(ok), "info": info, "error": err})
            return
        self._send(404, {"error": "not found"})


def _kill_zombie_on_port(port, my_pid=None):
    """Kill zombie python processes holding `port` (trừ `my_pid`).

    `taskkill /F` thường bị Access Denied với zombie processes từ worker cũ.
    Dùng WMI (`wmic process delete`) như fallback — đã chứng minh hoạt động.
    """
    import subprocess, re
    if my_pid is None:
        my_pid = os.getpid()
    for attempt in range(3):
        try:
            out = subprocess.check_output(
                ["netstat", "-ano"], timeout=10,
                creationflags=0x08000000,
            ).decode("utf-8", errors="replace")
        except Exception:
            return
        pids = set()
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                if parts:
                    try:
                        pid = int(parts[-1])
                        if pid and pid != my_pid:
                            pids.add(pid)
                    except ValueError:
                        pass
        if not pids:
            return  # Port is free
        _log(f"zombie cleanup: port {port} held by PIDs {pids} (attempt {attempt+1})")
        for pid in pids:
            # Try taskkill first
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, timeout=5, creationflags=0x08000000,
            )
        time.sleep(2)
        # Check if still held
        try:
            out2 = subprocess.check_output(
                ["netstat", "-ano"], timeout=10, creationflags=0x08000000,
            ).decode("utf-8", errors="replace")
            still_held = any(f":{port}" in l and "LISTENING" in l for l in out2.splitlines())
        except Exception:
            still_held = True
        if still_held:
            # Fallback: WMI delete
            for pid in pids:
                try:
                    subprocess.run(
                        ["wmic", "process", "where", f"ProcessId={pid}", "delete"],
                        capture_output=True, timeout=10, creationflags=0x08000000,
                    )
                except Exception:
                    pass
            time.sleep(3)
        else:
            _log(f"zombie cleanup: port {port} is now free")
            return
    _log(f"zombie cleanup: could not free port {port} after 3 attempts")


def _session_keepalive_loop(factory, interval=4*3600):
    """Background thread: refresh NextAuth sessions mỗi `interval` giây.

    Mỗi account: gọi bearer_from_cookie (tự refresh qua Set-Cookie).
    Nếu fail → dùng ChromeCDP.warm_flow() (SSO re-auth, không cần password).
    Chạy daemon — tự tắt khi factory tắt.
    """
    import auth_cache
    time.sleep(120)  # Chờ factory ổn định 2 phút trước khi bắt đầu
    while getattr(factory, '_running', True):
        _log(f"session-keepalive: bắt đầu refresh {len(factory.accounts)} accounts")
        ok_count = 0
        for acc in list(factory.accounts):
            email = getattr(acc, 'email', None)
            if not email:
                continue
            try:
                cached = auth_cache.get(email)
                if not cached or not cached.get('cookie'):
                    continue
                cookie = cached['cookie']
                bearer, got_email = fc.bearer_from_cookie(cookie)
                if bearer:
                    ok_count += 1
                    _log(f"session-keepalive: {email} OK (HTTP refresh)")
                else:
                    _log(f"session-keepalive: {email} HTTP fail, trying warm_flow...")
                    # Fallback: Chrome warm_flow
                    try:
                        from cdp_chrome import ChromeCDP
                        chrome_exe = getattr(acc, 'chrome_exe', None)
                        profile_dir = getattr(acc, 'profile_dir', None)
                        cdp_port = getattr(acc, 'cdp_port', 19500)
                        if chrome_exe and profile_dir:
                            cdp = ChromeCDP(chrome_exe, profile_dir, cdp_port, offscreen=True)
                            if cdp.connect(launch_timeout=30):
                                if cdp.warm_flow(attempts=3):
                                    new_bearer = cdp.bearer()
                                    if new_bearer:
                                        cookies = cdp.cookies()
                                        ck = '; '.join(f"{c['name']}={c['value']}" for c in cookies
                                                       if 'labs.google' in c.get('domain', ''))
                                        project = cdp.first_project_id()
                                        auth_cache.save(email, new_bearer, ck, project)
                                        ok_count += 1
                                        _log(f"session-keepalive: {email} OK (warm_flow)")
                                cdp.close(kill=True)
                    except Exception as ex:
                        _log(f"session-keepalive: {email} warm_flow error: {ex}")
            except Exception as ex:
                _log(f"session-keepalive: {email} error: {ex}")
        _log(f"session-keepalive: {ok_count}/{len(factory.accounts)} refreshed")
        # Sleep in chunks so we can stop quickly
        for _ in range(interval // 30):
            if not getattr(factory, '_running', True):
                return
            time.sleep(30)


def _kill_video_chromes():
    """Tree-kill chrome token factory VIDEO (veo3tok_970x — PORT video base 9700). KHÔNG đụng ảnh (974x) / tool khác."""
    import subprocess
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                        "Where-Object { $_.CommandLine -match 'veo3tok_970' } | "
                        "ForEach-Object { taskkill /F /T /PID $_.ProcessId 2>$null }"],
                       capture_output=True, timeout=30, creationflags=0x08000000)
    except Exception:
        pass


def main():
    global _FACTORY
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--token-chromes", type=int, default=2)
    args = ap.parse_args()
    # SINGLETON GUARD (chống RESPAWN STORM): Windows allow_reuse_address=True -> nhiều process cùng bind :8788
    # được -> chồng đống instance, mỗi cái mở chrome recovery = CỰC NẶNG. Nếu đã có video_factory chạy -> THOÁT.
    try:
        import urllib.request
        _h = json.load(urllib.request.urlopen(f"http://127.0.0.1:{args.port}/health", timeout=3))
        if isinstance(_h, dict) and ("accounts" in _h or "queue" in _h):
            print(f"[videofactory] đã chạy sẵn trên :{args.port} -> THOÁT (tránh trùng instance).", flush=True)
            return
    except Exception:
        pass
    _kill_zombie_on_port(args.port)  # KILL ZOMBIE: dọn python zombie giữ port (WMI fallback)
    _kill_video_chromes()   # CLEAN SLATE: dọn token chrome video sót từ lần trước (tránh zombie tích tụ)
    _FACTORY = VideoFactory(token_chromes=args.token_chromes)
    _FACTORY.start()
    # SESSION KEEPALIVE: tự refresh sessions mỗi 4h (ngăn hết hạn NextAuth)
    _ka = threading.Thread(target=_session_keepalive_loop, args=(_FACTORY,), daemon=True, name="session-keepalive")
    _ka.start()
    import atexit, signal
    atexit.register(_kill_video_chromes)   # BACKSTOP zombie khi tắt
    def _on_sig(*_a):
        try: _FACTORY.stop()
        except Exception: pass
        _kill_video_chromes(); os._exit(0)
    for _s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None), getattr(signal, "SIGBREAK", None)):
        if _s is not None:
            try: signal.signal(_s, _on_sig)
            except Exception: pass
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    _log(f"HTTP nghe 127.0.0.1:{args.port} (POST /generate, GET /health)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _FACTORY.stop(); _kill_video_chromes()


if __name__ == "__main__":
    main()
