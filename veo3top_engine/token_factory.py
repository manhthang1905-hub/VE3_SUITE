"""
TokenFactory (cách B) — nhà máy recaptcha token dùng CHUNG. 2 MODE:
- mode="blank": chrome TRẮNG (system chrome + profile temp, no-login) đẻ token. Nhẹ, không phí account.
- mode="account": chrome ACCOUNT (login, giữ profile) đẻ token. Token từ session login (score cao),
  giống bản chạy OK lúc đầu. Recycle = reload (giữ login). account_auth() trả bearer+cookie luôn.
Auth (bearer+cookie) generate LUÔN lấy từ account login (auth_cache) — chrome trắng chỉ để đẻ token.
LƯU Ý: 429 TOO_MUCH_TRAFFIC là do IP (WARP) bị rate-limit khi flood -> đã GHÌM tốc độ mint (paced).
"""
import os, sys, time, threading, queue, tempfile, shutil
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from cdp_chrome import ChromeCDP

TOKEN_TTL = 85
MINT_INTERVAL = 1.2      # GHÌM: giây giữa 2 lần mint (tránh flood recaptcha -> 429)
RECYCLE_TOKENS = 150     # account: recycle = reload (giữ login), ít cần fresh
# blank: chrome TRẮNG MỚI TINH thường xuyên -> mỗi profile chỉ đẻ ít token khi session còn "sạch" (điểm cao),
# giống veo3top mở rất nhiều chrome trắng. Tunable qua env VEO3TOP_TOKEN_RECYCLE.
RECYCLE_TOKENS_BLANK = int(os.environ.get("VEO3TOP_TOKEN_RECYCLE", "50") or "50")
RECYCLE_SECS = 300


def _rmtree_hard(path, tries=3):
    """Xoá thư mục có retry — chrome vừa bị kill đôi khi còn giữ lock file 1 nhịp.
    Retry giảm mạnh rò rỉ profile temp (thay vì ignore_errors bỏ lại luôn)."""
    for _ in range(tries):
        try:
            shutil.rmtree(path); return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(0.3)
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.isdir(path)


def _cleanup_stale_temp(older_than_min=10):
    """Xoá profile temp veo3tok_* CŨ (không còn dùng) -> không tích luỹ rác disk.
    (Recycle/kill chrome đôi khi rmtree fail do lock -> profile bị bỏ lại. Đây là lưới an toàn.)"""
    try:
        base = tempfile.gettempdir()
        cutoff = time.time() - older_than_min * 60
        for name in os.listdir(base):
            if name.startswith("veo3tok_"):
                p = os.path.join(base, name)
                try:
                    if os.path.isdir(p) and os.path.getmtime(p) < cutoff:
                        shutil.rmtree(p, ignore_errors=True)
                except OSError:
                    pass
    except Exception:
        pass


def _system_chrome():
    for c in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]:
        if os.path.exists(c):
            return c
    import glob
    for m in glob.glob(r"D:\VE3_SUITE\GoogleChromePortable - Copy (1)\App\Chrome-bin\*\chrome.exe"):
        return m
    return "chrome.exe"


class _Minter:
    """1 chrome đẻ token. blank=True -> chrome trắng (temp profile). blank=False -> account login."""
    def __init__(self, chrome_exe, profile_dir, port, pool, log, blank=True, action=None, persistent=False, factory=None, index=0, total=1):
        self.factory = factory       # ref TokenFactory để nhận tín hiệu RESET (lỗi tích nhiều -> chrome fresh)
        self._seen_reset_gen = 0
        self._idx = index; self._total = max(1, total)   # reset SO LE: mỗi lần chỉ 1 chrome reset (tránh blackout token)
        self.blank = blank
        self.action = action                # None=VIDEO_GENERATION (mặc định cdp); ảnh='IMAGE_GENERATION'
        # persistent=True: blank dùng profile CỐ ĐỊNH warm (tích luỹ trust reCAPTCHA giống veo3top),
        # KHÔNG temp mới tinh + recycle = reload page (giữ profile). Dùng cho nhà máy pool.
        self.persistent = bool(persistent)
        self.chrome_exe = _system_chrome() if blank else chrome_exe
        self.profile_dir = profile_dir      # blank: bỏ qua (dùng temp/persistent); account: profile login
        self.port = port; self.pool = pool; self.log = log
        self.cdp = None; self.temp = None; self._stop = False; self._minted = 0
        self.lock = threading.Lock(); self.email = None

    def _profile(self):
        if self.blank:
            if self.persistent:
                base = os.path.join(_HERE, ".veo3top_warm_profiles")
                os.makedirs(base, exist_ok=True)
                self.temp = os.path.join(base, str(self.port))   # CỐ ĐỊNH theo port (warm dần)
                os.makedirs(self.temp, exist_ok=True)
                return self.temp
            self.temp = tempfile.mkdtemp(prefix=f"veo3tok_{self.port}_")
            return self.temp
        return self.profile_dir

    def _open(self, fresh_profile=False):
        try:
            if self.cdp: self.cdp.close(kill=True)
        except Exception: pass
        if self.blank and fresh_profile and not self.persistent and self.temp and os.path.isdir(self.temp):
            _rmtree_hard(self.temp); self.temp = None
        prof = self._profile() if (self.blank and (fresh_profile or not self.temp)) else (self.temp if self.blank else self.profile_dir)
        self.cdp = ChromeCDP(self.chrome_exe, prof, self.port, offscreen=True, log=self.log)
        if not (self.cdp.connect(launch_timeout=45) and self.cdp.wait_ready(35)):
            return False
        if not self.blank:
            try: self.email = self.cdp.email()
            except Exception: pass
        return True

    def get_auth(self):
        import flow_client as fc
        with self.lock:
            return {"bearer": self.cdp.bearer(), "cookie": fc.labs_cookie_header(self.cdp.cookies()),
                    "project": self.cdp.first_project_id(), "email": self.email, "ts": time.time()}

    def run(self):
        if not self._open(fresh_profile=True):
            self.log(f"[tokenfactory:{self.port}] chrome ({'trắng' if self.blank else 'account'}) khởi tạo lỗi"); return
        self.log(f"[tokenfactory:{self.port}] chrome {'TRẮNG(no-login)' if self.blank else 'ACCOUNT('+str(self.email)+')'} sẵn sàng, đẻ token (paced {MINT_INTERVAL}s)")
        t_recycle = time.time()
        while not self._stop:
            # RESET theo LỖI (giống veo3top numreset) — SO LE: mỗi lần chỉ chrome tới lượt reset (không blackout token)
            if self.factory is not None and getattr(self.factory, "_reset_gen", 0) > self._seen_reset_gen:
                gen = self.factory._reset_gen
                self._seen_reset_gen = gen
                if (gen % self._total) == self._idx:
                    self.log(f"[tokenfactory:{self.port}] RESET chrome (so le, reCAPTCHA context mới)")
                    with self.lock:
                        self._open(fresh_profile=True)
                    t_recycle = time.time()
            if self.pool.maxsize and self.pool.qsize() >= self.pool.maxsize:
                time.sleep(0.3); continue
            try:
                with self.lock:
                    tok = self.cdp.mint_token(self.action)
            except Exception:
                tok = None
            if tok:
                try: self.pool.put((tok, time.time()), timeout=1)
                except queue.Full: pass
                self._minted += 1
                # blank: recycle THƯỜNG XUYÊN (chrome mới tinh) -> token luôn từ session sạch (điểm cao)
                recycle_n = RECYCLE_TOKENS_BLANK if self.blank else RECYCLE_TOKENS
                if self._minted % recycle_n == 0 or (time.time() - t_recycle) > RECYCLE_SECS:
                    warm = self.persistent or (not self.blank)   # persistent/account: RELOAD giữ profile (warm/login)
                    self.log(f"[tokenfactory:{self.port}] recycle sau {self._minted} token ({'reload-warm' if warm else 'trắng-mới'})")
                    with self.lock:
                        if not warm:
                            self._open(fresh_profile=True)       # blank thường: profile MỚI TINH
                        else:
                            try:
                                self.cdp.cmd("Page.enable"); self.cdp.cmd("Page.reload", ignoreCache=True); self.cdp.wait_ready(30)
                            except Exception:
                                self._open()                     # reload giữ profile (warm reCAPTCHA / login)
                    t_recycle = time.time()
                time.sleep(MINT_INTERVAL)
            else:
                time.sleep(0.5)

    def stop(self):
        self._stop = True
        try:
            if self.cdp: self.cdp.close(kill=True)
        except Exception: pass
        # GIỮ profile warm (persistent) — chỉ xoá temp thường
        if self.temp and os.path.isdir(self.temp) and not self.persistent:
            _rmtree_hard(self.temp)


class TokenFactory:
    def __init__(self, mode="blank", chrome_exe=None, profile_dir=None, n_chromes=1, buffer=24, base_port=9840, log=print, action=None, persistent=False):
        self.mode = mode
        self.action = action     # None=VIDEO_GENERATION; ảnh='IMAGE_GENERATION' (đẻ token đúng action cho endpoint ảnh)
        self.persistent = bool(persistent)   # blank: profile CỐ ĐỊNH warm (trust reCAPTCHA) — giống veo3top
        self.chrome_exe = chrome_exe
        self.profile_dir = profile_dir
        self.n_chromes = max(1, int(n_chromes))
        self.base_port = base_port
        self.log = log
        # buffer lớn hơn để đủ token cho 20 luồng video generate cùng lúc
        self.pool = queue.Queue(maxsize=max(12, buffer))
        self.minters = []
        self._started = False
        self._stop_janitor = False
        self._lock = threading.Lock()
        self.account_email = None
        # RESET theo lỗi (giống veo3top): lỗi recaptcha tích > RESET_ON_ERRORS -> reset chrome fresh
        self._reset_gen = 0
        self._err_count = 0
        self.RESET_ON_ERRORS = int(os.environ.get("VEO3TOP_RESET_ON_ERRORS", "30") or "30")

    def note_error(self):
        """Provider gọi khi 1 generate trượt reCAPTCHA. Tích đủ -> báo minters reset chrome (context mới)."""
        with self._lock:
            self._err_count += 1
            if self._err_count >= self.RESET_ON_ERRORS:
                self._err_count = 0
                self._reset_gen += 1

    def _start_temp_janitor(self):
        """Thread nền dọn profile temp veo3tok_* cũ định kỳ (mã chạy dài không tích rác)."""
        def _loop():
            import cdp_chrome
            while not self._stop_janitor:
                _cleanup_stale_temp(10)
                try: cdp_chrome.cleanup_dead_pidfiles()   # dọn file .pid trỏ chrome đã chết
                except Exception: pass
                for _ in range(60):          # ngủ 5 phút, nhưng thoát nhanh khi stop
                    if self._stop_janitor: return
                    time.sleep(5)
        threading.Thread(target=_loop, daemon=True).start()

    def start(self):
        with self._lock:
            if self._started: return True
            blank = (self.mode != "account")
            if blank:
                _cleanup_stale_temp(10)      # dọn rác profile cũ từ lần chạy trước NGAY khi start
                self._start_temp_janitor()
            # account mode: 1 minter (profile login không mở 2 lần được). blank: có thể nhiều.
            n = 1 if not blank else self.n_chromes
            for i in range(n):
                m = _Minter(self.chrome_exe, self.profile_dir, self.base_port + i, self.pool, self.log, blank=blank, action=self.action, persistent=self.persistent, factory=self, index=i, total=n)
                self.minters.append(m)
                threading.Thread(target=m.run, daemon=True).start()
            self._started = True
            if not blank:
                for _ in range(40):
                    if self.minters[0].email: self.account_email = self.minters[0].email; break
                    time.sleep(1)
            self.log(f"[tokenfactory] mode={self.mode} ({n} chrome) đẻ token (buffer={self.pool.maxsize}, paced)")
            return True

    def account_auth(self):
        if self.mode == "account" and self.minters:
            return self.minters[0].get_auth()
        return None

    def get(self, timeout=45):
        end = time.time() + timeout
        while time.time() < end:
            try:
                tok, ts = self.pool.get(timeout=2)
            except queue.Empty:
                continue
            if time.time() - ts < TOKEN_TTL:
                return tok
        return None

    def stop(self):
        self._stop_janitor = True
        for m in self.minters:
            m.stop()


_FACTORY = None
_FLOCK = threading.Lock()

def get_factory(mode="blank", chrome_exe=None, profile_dir=None, n_chromes=1, base_port=9840, log=print, persistent=False, **_ignore):
    global _FACTORY
    with _FLOCK:
        if _FACTORY is None:
            _FACTORY = TokenFactory(mode=mode, chrome_exe=chrome_exe, profile_dir=profile_dir,
                                    n_chromes=n_chromes, base_port=base_port, log=log, persistent=persistent)
            _FACTORY.start()
        return _FACTORY


# --- Factory RIÊNG cho ẢNH (action='IMAGE_GENERATION'), tách hẳn factory video ở trên ---
_IMG_FACTORY = None
_IMG_FLOCK = threading.Lock()

def get_image_factory(mode="blank", chrome_exe=None, profile_dir=None, n_chromes=1, base_port=9740, log=print, **_ignore):
    """Nhà máy token cho ẢNH — đẻ recaptcha action IMAGE_GENERATION. Singleton riêng (không đụng video)."""
    global _IMG_FACTORY
    with _IMG_FLOCK:
        if _IMG_FACTORY is None:
            _IMG_FACTORY = TokenFactory(mode=mode, chrome_exe=chrome_exe, profile_dir=profile_dir,
                                        n_chromes=n_chromes, base_port=base_port, log=log,
                                        action="IMAGE_GENERATION")
            _IMG_FACTORY.start()
        return _IMG_FACTORY
