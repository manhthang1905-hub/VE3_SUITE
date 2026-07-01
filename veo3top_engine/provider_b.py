"""
Veo3topProviderB — cách B (giống veo3top): token-chrome CHUNG + auth (bearer+cookie) per-account cache.
Khác cách A: KHÔNG giữ chrome account mở suốt; chrome account chỉ mở chốc lát để lấy auth (rồi đóng),
token lấy từ factory chung. Nhẹ khi chạy nhiều mã.

submit_video(prompt, media_id, out) -> (success, info, error)  — KHỚP _submit_video của worker.
Tự fix: 401 -> invalidate+lấy lại bearer; download fail -> lấy lại cookie. Retry bền bỉ + cooldown.
"""
import os, sys, time, threading, random
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import flow_client as fc
import token_factory as tf
import ipv6_transport as ip6t
import rate_coordinator
from auth_cache import AuthCache

# auth cache dùng chung toàn process
_AUTH = None
def _auth_cache(log):
    global _AUTH
    if _AUTH is None:
        _AUTH = AuthCache(log=log)
    return _AUTH


# --- Cooldown per-account (dùng chung trong process) để các scene cùng account phối hợp nghỉ ---
_ACCT_LOCK = threading.Lock()
_ACCT_RESUME = {}   # account_lower -> resume_timestamp

def _acct_wait_secs(account):
    """Còn phải nghỉ bao nhiêu giây cho account này (0 = chạy được)."""
    with _ACCT_LOCK:
        return max(0.0, _ACCT_RESUME.get((account or "").lower(), 0) - time.time())

def _acct_set_rest(account, secs):
    """Đặt account nghỉ tới now+secs (chỉ đẩy xa hơn, không rút ngắn)."""
    with _ACCT_LOCK:
        k = (account or "").lower()
        _ACCT_RESUME[k] = max(_ACCT_RESUME.get(k, 0), time.time() + secs)

# Account đã xác định là ACCOUNT-LEVEL (flag/hết quota) -> ĐỪNG rotate IPv6 (vô ích + đốt pool), chỉ NGHỈ.
# Xoá cờ khi có 1 lần generate thành công (account hồi) -> cho phép rotate lại (phòng lỗi IP thật).
_ACCT_FLAGGED = set()
def _acct_is_flagged(account):
    with _ACCT_LOCK:
        return (account or "").lower() in _ACCT_FLAGGED
def _acct_set_flagged(account, val):
    with _ACCT_LOCK:
        k = (account or "").lower()
        _ACCT_FLAGGED.add(k) if val else _ACCT_FLAGGED.discard(k)


class Veo3topProviderB:
    MAX_ATTEMPTS = 80
    COOLDOWN_EVERY = 8
    COOLDOWN_SECONDS = 25
    RETRY_DELAY = 1.5
    # KIÊN NHẪN RETRY như veo3top (nó thử ~50 lần/vòng, nhiều vòng, rồi mới thành công — KHÔNG nghỉ sớm).
    MAX_ROTATE = 3        # đổi IPv6 rất HIẾM (chủ yếu retry token mới, không phải đổi IP)
    ROTATE_EVERY = 15     # chỉ đổi IPv6 mỗi 15 fail (phòng vài IP bị chặn); còn lại cứ retry token mới
    GIVEUP_CONSEC = 40    # thử 40 lần token mới rồi mới nghỉ (giống veo3top ~50). Đừng nghỉ sớm!
    BACKOFF_BASE = 2      # backoff NHẸ (retry nhanh như veo3top ~1-2s/lần)
    BACKOFF_QUOTA = 3     # RESOURCE_EXHAUSTED cũng cứ retry (account vẫn ra video khi retry) — chờ ngắn thôi
    BACKOFF_MAX = 6
    MAX_ATTEMPTS = 120    # (ghi đè hằng dưới) cho phép nhiều lần thử/vòng
    # Self-recovery: thử nhiều mà vẫn fail -> nghỉ NGẮN rồi thử tiếp. Không kẹt vĩnh viễn.
    MAX_REST_CYCLES = 6
    REST_BASE = 60        # nghỉ chu kỳ 1 = 60s (ngắn — cứ thử lại như veo3top)
    REST_MAX = 180        # trần mỗi lần nghỉ = 3 phút

    def __init__(self, account_name, chrome_exe, profile_dir, auth_port,
                 token_chrome_exe=None, token_profile_dir=None, token_port=9840,
                 token_chromes=1, token_mode="blank", ipv6_port=None,
                 video_aspect="VIDEO_ASPECT_RATIO_LANDSCAPE", log=print):
        self.account = account_name
        self.chrome_exe = chrome_exe
        self.profile_dir = profile_dir
        self.auth_port = auth_port
        # token_mode: "blank" = chrome trắng no-login (nhẹ); "account" = chrome account login (token score cao).
        self.token_mode = token_mode
        self.token_chrome_exe = token_chrome_exe or chrome_exe
        self.token_profile_dir = token_profile_dir or profile_dir
        self.token_port = token_port
        self.token_chromes = max(1, int(token_chromes))
        self.video_aspect = video_aspect
        # ipv6_port riêng/mã (mỗi subprocess 1 SOCKS): mặc định suy từ token_port -> không đụng nhau
        self.ipv6_port = int(ipv6_port) if ipv6_port else (int(token_port) + 300)
        self.log = log
        self.factory = None
        self.transport = None
        self.use_ipv6 = False
        self.cache = _auth_cache(log)

    def start(self):
        # CHỈ generate (gửi token captcha = chỗ 429) đi IPv6 pool; upload/download/poll đi DIRECT IPv4 máy.
        self.transport = ip6t.IPv6Transport(
            self.account or f"veo3top_{self.token_port}", port=self.ipv6_port, log=self.log)
        url = self.transport.start()
        if url:
            fc.set_gen_proxy(url)     # generate -> IPv6
            self.use_ipv6 = True
            self.log(f"[veo3top-b] generate qua IPv6 pool ({url}); upload/download DIRECT IPv4")
        else:
            self.transport = None
            self.use_ipv6 = False
            fc.set_gen_proxy(None)    # pool sập -> generate direct (có thể 429, nhưng tool vẫn chạy)
            self.log("[veo3top-b] pool IPv6 không dùng được -> generate DIRECT (có thể 429)")
        # Factory theo mode: blank (chrome trắng) hoặc account (chrome account login).
        self.factory = tf.get_factory(mode=self.token_mode,
                                      chrome_exe=self.token_chrome_exe,
                                      profile_dir=self.token_profile_dir,
                                      n_chromes=self.token_chromes,
                                      base_port=self.token_port, log=self.log)
        return bool(self.factory and self.factory._started)

    def _get_auth(self, force=False):
        """Nếu mã trùng account của factory -> lấy auth từ factory (tránh mở profile 2 lần). Else auth_cache."""
        if self.factory and self.factory.account_email and self.account and \
           self.account.lower() == str(self.factory.account_email).lower():
            return self.factory.account_auth()
        return self.cache.ensure(self.account, self.chrome_exe, self.profile_dir, self.auth_port, force=force)

    def _sleep_chunked(self, secs, chunk=5):
        """Ngủ theo mảnh nhỏ (đỡ block cứng) — dùng cho cooldown dài."""
        end = time.time() + max(0, secs)
        while True:
            rem = end - time.time()
            if rem <= 0:
                break
            time.sleep(min(chunk, rem))

    def submit_video(self, prompt, media_id, out_path, seed=None, max_attempts=None, poll_max=48):
        if not media_id:
            return False, {}, "veo3top-b: thieu media_id"
        if not self.factory:
            if not self.start():
                return False, {}, "veo3top-b: token factory khong khoi tao duoc"
        auth = self._get_auth()
        if not auth or not auth.get("bearer"):
            return False, {}, "veo3top-b: khong lay duoc bearer cua account"
        bearer = auth["bearer"]; cookie = auth.get("cookie"); project = auth.get("project")
        if not project:
            return False, {}, "veo3top-b: account khong co project_id"
        if seed is None:
            seed = int(time.time() * 1000) % 2_000_000

        last = ""; rest_cycles = 0
        while True:
            # Account đang nghỉ (do scene khác cùng account phát hiện flag)? -> đợi hết cooldown rồi thử.
            w = _acct_wait_secs(self.account)
            if w > 0:
                self.log(f"[veo3top-b] account {self.account} đang nghỉ, đợi {int(w)}s rồi thử lại...")
                self._sleep_chunked(w)
                a2 = self._get_auth(force=True)   # bearer có thể hết hạn sau nghỉ dài
                if a2 and a2.get("bearer"):
                    bearer = a2["bearer"]; cookie = a2.get("cookie"); project = a2.get("project") or project

            # 1 CHU KỲ THỬ: chạy tới khi thành công, hoặc fail-liên-tiếp = GIVEUP_CONSEC (đổi IPv6 tối đa MAX_ROTATE).
            consec = 0; rotations = 0
            for _inner in range(self.MAX_ATTEMPTS):
                tok = self.factory.get()
                if not tok:
                    last = "no token from factory"; time.sleep(1); continue
                payload = fc.build_payload(prompt, project, tok, seed,
                                           aspect=self.video_aspect, reference_media_id=media_id)
                rate_coordinator.acquire()   # ghìm nhịp bắn CHUNG toàn máy (mọi mã) -> không tự vỡ rate
                kind, data = fc.generate(bearer, payload, url=fc.GEN_I2V)
                if kind == "ok":
                    consec = 0
                    _acct_set_flagged(self.account, False)   # account hồi -> cho phép rotate lại sau này
                    mid = (fc.operation_names(data) or [None])[0]
                    if not mid:
                        last = "200 no media id"; continue
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    pkind, _ = fc.poll_until_done(bearer, [mid], max_attempts=poll_max, interval=5)
                    if pkind == "failed":
                        # render FAILED = policy/content -> tra NHANH cho tool viet lai prompt
                        return False, {"media_id": mid}, "veo3top-b: render FAILED (policy) - can rewrite prompt"
                    if pkind == "auth":
                        self.cache.invalidate(self.account); auth = self._get_auth(force=True); bearer = auth["bearer"]; cookie = auth.get("cookie")
                    if pkind != "done":
                        return False, {"media_id": mid}, "veo3top-b: render timeout"
                    for _ in range(12):
                        try:
                            n, url = fc.media_url_and_download(mid, cookie, str(out_path))
                            return True, {"media_id": mid, "bytes": n}, ""
                        except Exception:
                            self.cache.invalidate(self.account); auth = self._get_auth(force=True); cookie = auth.get("cookie")
                            time.sleep(4)
                    return False, {"media_id": mid}, "veo3top-b: download fail"
                elif kind == "auth":
                    self.cache.invalidate(self.account); auth = self._get_auth(force=True)
                    if auth and auth.get("bearer"):
                        bearer = auth["bearer"]; cookie = auth.get("cookie")
                    last = "bearer refreshed"; continue
                elif kind in ("unusual", "ratelimit", "ip_block", "recaptcha_quota"):
                    # KIÊN NHẪN như veo3top: cứ RETRY TOKEN MỚI (fresh token từ factory) tới ~GIVEUP_CONSEC lần,
                    # backoff nhẹ (~2-3s). Đổi IPv6 HIẾM (mỗi ROTATE_EVERY fail) phòng vài IP bị chặn.
                    # KHÔNG nghỉ sớm — veo3top thử ~50 lần/vòng mới ra, "hết quota" cứ retry là vẫn ra.
                    consec += 1; last = kind
                    if consec >= self.GIVEUP_CONSEC:
                        break   # thử rất nhiều vẫn fail -> ra ngoài nghỉ NGẮN rồi thử tiếp
                    is_per_ip = kind in ("ratelimit", "ip_block")
                    if (is_per_ip and consec % self.ROTATE_EVERY == 0 and self.use_ipv6 and self.transport
                            and not _acct_is_flagged(self.account) and rotations < self.MAX_ROTATE):
                        rotations += 1
                        self.transport.rotate()
                        self.log(f"[veo3top-b] {kind} consec={consec} -> đổi IPv6 ({rotations}/{self.MAX_ROTATE})")
                    base = self.BACKOFF_QUOTA if kind == "recaptcha_quota" else self.BACKOFF_BASE
                    # backoff nhẹ + JITTER: nhiều luồng cùng mã không retry đồng loạt (đỡ va nhau trên rate chung)
                    nap = min(self.BACKOFF_MAX, base + consec * 0.1) + random.uniform(0, 1.5)
                    if consec % 10 == 0:
                        self.log(f"[veo3top-b] {kind} -> retry token mới lần {consec}/{self.GIVEUP_CONSEC} (kiên nhẫn như veo3top)")
                    time.sleep(nap)
                    continue
                else:
                    last = f"other:{data}"; time.sleep(0.5)

            # Ra tới đây = 1 chu kỳ fail (account-level). Đánh dấu account-level -> các scene ngừng rotate (đỡ pool).
            _acct_set_flagged(self.account, True)
            rest_cycles += 1
            if rest_cycles > self.MAX_REST_CYCLES:
                return False, {}, (f"veo3top-b: account {self.account} flag/quota kéo dài "
                                   f"(đã nghỉ+thử {rest_cycles-1} chu kỳ) - bỏ scene, chạy lại sau sẽ tự làm tiếp")
            rest = min(self.REST_MAX, self.REST_BASE * rest_cycles)
            _acct_set_rest(self.account, rest)
            self.log(f"[veo3top-b] account {self.account} bị flag/quota ({last}) -> NGHỈ {rest}s rồi TỰ THỬ LẠI "
                     f"(chu kỳ {rest_cycles}/{self.MAX_REST_CYCLES})")
            self._sleep_chunked(rest)
            a2 = self._get_auth(force=True)
            if a2 and a2.get("bearer"):
                bearer = a2["bearer"]; cookie = a2.get("cookie"); project = a2.get("project") or project
