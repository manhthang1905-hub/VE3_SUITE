"""
Veo3topImageProviderB — tạo ẢNH bắn thẳng Flow API (flowMedia:batchGenerateImages), song song
với pipeline VIDEO (provider_b.py). Tái dùng TOÀN BỘ hạ tầng: token_factory (action IMAGE_GENERATION),
ipv6_transport (pool IPv6 tránh 429), rate_coordinator (ghìm nhịp chung máy), auth_cache (bearer+cookie/account).

KHÁC video: ảnh SYNCHRONOUS — POST 200 là có ảnh luôn (fifeUrl), KHÔNG cần poll. Download fifeUrl DIRECT IPv4.

submit_image(prompt, out_path, ...) -> (success, info, error)  — khớp _submit_image của worker
(worker trả (ok, media_name, sinfo, err); wrapper ở worker sẽ map lại).

QUAN TRỌNG: KHÔNG đụng provider_b/token_factory video. Dùng factory ảnh riêng (get_image_factory),
port token + ipv6 riêng để không va chạm với subprocess video.
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

# auth cache dùng chung toàn process (chia sẻ ổ đĩa với video -> tái dùng bearer/cookie account)
_AUTH = None
def _auth_cache(log):
    global _AUTH
    if _AUTH is None:
        _AUTH = AuthCache(log=log)
    return _AUTH


# --- Cooldown/flag per-account RIÊNG cho ảnh (không đụng state video) ---
_ACCT_LOCK = threading.Lock()
_ACCT_RESUME = {}
_ACCT_FLAGGED = set()

def _acct_wait_secs(account):
    with _ACCT_LOCK:
        return max(0.0, _ACCT_RESUME.get((account or "").lower(), 0) - time.time())

def _acct_set_rest(account, secs):
    with _ACCT_LOCK:
        k = (account or "").lower()
        _ACCT_RESUME[k] = max(_ACCT_RESUME.get(k, 0), time.time() + secs)

def _acct_is_flagged(account):
    with _ACCT_LOCK:
        return (account or "").lower() in _ACCT_FLAGGED

def _acct_set_flagged(account, val):
    with _ACCT_LOCK:
        k = (account or "").lower()
        _ACCT_FLAGGED.add(k) if val else _ACCT_FLAGGED.discard(k)


class Veo3topImageProviderB:
    # Cùng triết lý kiên nhẫn như provider_b (video): retry token mới nhiều lần, đổi IPv6 hiếm, nghỉ ngắn.
    MAX_ATTEMPTS = 120
    # Ảnh: 429 là per-IP THUẦN TÚY (token được nhận, chỉ IP bị rate) -> đổi IPv6 là cách chữa CHÍNH.
    # Rotate MẠNH TAY hơn video (video kẹt chủ yếu do token score, ảnh kẹt do IP).
    MAX_ROTATE = 8
    ROTATE_EVERY = 5
    GIVEUP_CONSEC = 40
    BACKOFF_BASE = 2
    BACKOFF_QUOTA = 4
    BACKOFF_MAX = 6
    # recaptcha_quota = RESOURCE_EXHAUSTED (quota sitekey cạn TOÀN CỤC): mỗi retry vẫn đốt 1 assessment
    # -> GIÃN MẠNH (không đốt thêm vào lửa, để quota hồi nhanh hơn cho cả máy). Ramp 4->25s.
    BACKOFF_QUOTA_MAX = 25
    MAX_REST_CYCLES = 6
    REST_BASE = 60
    REST_MAX = 180

    def __init__(self, account_name, chrome_exe, profile_dir, auth_port,
                 token_chrome_exe=None, token_profile_dir=None, token_port=9740,
                 token_chromes=2, token_mode="blank", ipv6_port=None,
                 image_aspect="IMAGE_ASPECT_RATIO_LANDSCAPE", log=print):
        self.account = account_name
        self.chrome_exe = chrome_exe
        self.profile_dir = profile_dir
        self.auth_port = auth_port
        self.token_mode = token_mode
        self.token_chrome_exe = token_chrome_exe or chrome_exe
        self.token_profile_dir = token_profile_dir or profile_dir
        self.token_port = token_port
        self.token_chromes = max(1, int(token_chromes))
        self.image_aspect = image_aspect
        # ipv6_port riêng cho ảnh (mặc định lệch video): token_port + 500
        self.ipv6_port = int(ipv6_port) if ipv6_port else (int(token_port) + 500)
        self.log = log
        self.factory = None
        self.transport = None
        self.use_ipv6 = False
        self.gen_proxy = ""      # proxy riêng cho generate ảnh (không đụng GEN_PROXY global của video)
        self.cache = _auth_cache(log)

    def start(self):
        # CHỈ generate (gửi token captcha = chỗ 429) đi IPv6 pool; download đi DIRECT IPv4 máy.
        self.transport = ip6t.IPv6Transport(
            self.account or f"veo3topimg_{self.token_port}", port=self.ipv6_port, log=self.log)
        url = self.transport.start()
        # KHÔNG dùng fc.set_gen_proxy (global dùng chung với video) — truyền proxy riêng vào generate_image.
        # gen_proxy = "" -> generate ảnh đi DIRECT IPv4 (khi pool sập), độc lập hẳn với video.
        if url:
            self.gen_proxy = url
            self.use_ipv6 = True
            self.log(f"[img-b] generate ảnh qua IPv6 pool ({url}); download DIRECT IPv4")
        else:
            self.transport = None
            self.use_ipv6 = False
            self.gen_proxy = ""
            self.log("[img-b] pool IPv6 không dùng được -> generate ảnh DIRECT (có thể 429)")
        # Factory token ẢNH (action IMAGE_GENERATION) — RIÊNG factory video.
        self.factory = tf.get_image_factory(mode=self.token_mode,
                                            chrome_exe=self.token_chrome_exe,
                                            profile_dir=self.token_profile_dir,
                                            n_chromes=self.token_chromes,
                                            base_port=self.token_port, log=self.log)
        return bool(self.factory and self.factory._started)

    def _get_auth(self, force=False):
        if self.factory and self.factory.account_email and self.account and \
           self.account.lower() == str(self.factory.account_email).lower():
            return self.factory.account_auth()
        return self.cache.ensure(self.account, self.chrome_exe, self.profile_dir, self.auth_port, force=force)

    def _sleep_chunked(self, secs, chunk=5):
        end = time.time() + max(0, secs)
        while True:
            rem = end - time.time()
            if rem <= 0:
                break
            time.sleep(min(chunk, rem))

    def submit_image(self, prompt, out_path, seed=None, image_inputs=None, aspect=None):
        """Trả (success, info, error). info gồm media_name (dùng làm reference), bytes."""
        if not self.factory:
            if not self.start():
                return False, {}, "img-b: token factory khong khoi tao duoc"
        auth = self._get_auth()
        if not auth or not auth.get("bearer"):
            return False, {}, "img-b: khong lay duoc bearer cua account"
        bearer = auth["bearer"]; project = auth.get("project")
        if not project:
            return False, {}, "img-b: account khong co project_id"
        if seed is None:
            seed = int(time.time() * 1000) % 900000
        aspect = aspect or self.image_aspect

        last = ""; rest_cycles = 0
        while True:
            w = _acct_wait_secs(self.account)
            if w > 0:
                self.log(f"[img-b] account {self.account} đang nghỉ, đợi {int(w)}s...")
                self._sleep_chunked(w)
                a2 = self._get_auth(force=True)
                if a2 and a2.get("bearer"):
                    bearer = a2["bearer"]; project = a2.get("project") or project

            consec = 0; rotations = 0
            for _inner in range(self.MAX_ATTEMPTS):
                tok = self.factory.get()
                if not tok:
                    last = "no token from factory"; time.sleep(1); continue
                payload = fc.build_image_payload(prompt, project, tok, seed,
                                                 aspect=aspect, image_inputs=image_inputs)
                rate_coordinator.acquire()   # ghìm nhịp bắn CHUNG toàn máy (dùng chung với video)
                kind, data = fc.generate_image(bearer, project, payload, proxy=self.gen_proxy)
                if kind == "ok":
                    consec = 0
                    _acct_set_flagged(self.account, False)
                    name, fife, b64, rseed = fc.image_result(data)
                    if not (fife or b64):
                        last = "200 no image url"; continue
                    os.makedirs(os.path.dirname(out_path), exist_ok=True) if os.path.dirname(out_path) else None
                    try:
                        if fife:
                            n = fc.download_image_url(fife, str(out_path))
                        else:
                            import base64
                            data_bytes = base64.b64decode(b64)
                            with open(out_path, "wb") as f:
                                f.write(data_bytes)
                            n = len(data_bytes)
                        return True, {"media_name": name, "bytes": n, "seed": rseed}, ""
                    except Exception as e:
                        last = f"download fail: {e}"
                        time.sleep(2); continue
                elif kind == "auth":
                    self.cache.invalidate(self.account); auth = self._get_auth(force=True)
                    if auth and auth.get("bearer"):
                        bearer = auth["bearer"]; project = auth.get("project") or project
                    last = "bearer refreshed"; continue
                elif kind in ("unusual", "ratelimit", "ip_block", "recaptcha_quota"):
                    consec += 1; last = kind
                    if consec >= self.GIVEUP_CONSEC:
                        break
                    is_per_ip = kind in ("ratelimit", "ip_block")
                    if (is_per_ip and consec % self.ROTATE_EVERY == 0 and self.use_ipv6 and self.transport
                            and not _acct_is_flagged(self.account) and rotations < self.MAX_ROTATE):
                        rotations += 1
                        self.transport.rotate()
                        self.log(f"[img-b] {kind} consec={consec} -> đổi IPv6 ({rotations}/{self.MAX_ROTATE})")
                    if kind == "recaptcha_quota":
                        # quota cạn toàn cục -> giãn mạnh (đỡ đốt thêm assessment, chờ quota hồi)
                        nap = min(self.BACKOFF_QUOTA_MAX, self.BACKOFF_QUOTA + consec * 1.3) + random.uniform(0, 2)
                    else:
                        # per-IP/token -> retry nhanh (đổi IPv6 + token mới cứu được)
                        nap = min(self.BACKOFF_MAX, self.BACKOFF_BASE + consec * 0.1) + random.uniform(0, 1.5)
                    if consec % 10 == 0:
                        self.log(f"[img-b] {kind} -> retry token mới lần {consec}/{self.GIVEUP_CONSEC}")
                    time.sleep(nap)
                    continue
                else:
                    last = f"other:{data}"; time.sleep(0.5)

            _acct_set_flagged(self.account, True)
            rest_cycles += 1
            if rest_cycles > self.MAX_REST_CYCLES:
                return False, {}, (f"img-b: account {self.account} flag/quota kéo dài "
                                   f"(đã nghỉ+thử {rest_cycles-1} chu kỳ) - bỏ ảnh, chạy lại sau sẽ tự làm tiếp")
            rest = min(self.REST_MAX, self.REST_BASE * rest_cycles)
            _acct_set_rest(self.account, rest)
            self.log(f"[img-b] account {self.account} bị flag/quota ({last}) -> NGHỈ {rest}s rồi TỰ THỬ LẠI "
                     f"(chu kỳ {rest_cycles}/{self.MAX_REST_CYCLES})")
            self._sleep_chunked(rest)
            a2 = self._get_auth(force=True)
            if a2 and a2.get("bearer"):
                bearer = a2["bearer"]; project = a2.get("project") or project
