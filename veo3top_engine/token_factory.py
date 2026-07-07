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
MINT_INTERVAL = float(os.environ.get("VEO3TOP_MINT_INTERVAL", "1.2") or "1.2")  # GHÌM giây giữa 2 lần mint. Tăng (vd 2.2) = đốt quota reCAPTCHA CHẬM hơn -> bền, không cạn (sustainable mode).
RECYCLE_TOKENS = 150     # account: recycle = reload (giữ login), ít cần fresh
# blank: chrome TRẮNG MỚI TINH thường xuyên -> mỗi profile chỉ đẻ ít token khi session còn "sạch" (điểm cao),
# giống veo3top mở rất nhiều chrome trắng. Tunable qua env VEO3TOP_TOKEN_RECYCLE.
RECYCLE_TOKENS_BLANK = int(os.environ.get("VEO3TOP_TOKEN_RECYCLE", "3") or "3")  # chrome tươi mỗi ~3 token (đo: recycle thấp=rate cao); adaptive (RESET_ON_ERRORS) reset thêm khi chai giữa chừng
RECYCLE_SECS = 300
# DEMAND-DRIVEN: android_bypass lo CHÍNH -> WEB token chỉ là fallback HIẾM (đo: 1410 recycle / 11 token dùng =
# 99% churn vô ích). Nếu KHÔNG có get() trong TOKEN_IDLE_SECS -> minter NGƯNG mint (chrome vẫn mở sẵn, demand tới
# mint ngay) -> hết churn chrome oan. 0 = tắt (mint liên tục như cũ).
TOKEN_IDLE_SECS = int(os.environ.get("VEO3TOP_TOKEN_IDLE_SECS", "20") or "20")


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
    """Tìm Chrome BỀN across máy (đừng hardcode 1 path — máy khác cài chỗ khác -> token chrome không mở,
    thread minter chết im, không có chrome, không token, nhưng job vẫn submit nên log vẫn báo 'đang tạo video')."""
    import glob
    cands = []
    # 1) cài chuẩn: machine (Program Files / (x86)) + user (LOCALAPPDATA)
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "ProgramW6432"):
        base = os.environ.get(env)
        if base:
            cands.append(os.path.join(base, r"Google\Chrome\Application\chrome.exe"))
    cands += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]
    # 2) registry App Paths (nơi cài thật, kể cả ổ khác)
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                k = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
                v, _ = winreg.QueryValueEx(k, None)
                if v:
                    cands.append(v)
            except OSError:
                pass
    except Exception:
        pass
    for c in cands:
        if c and os.path.exists(c):
            return c
    # 3) Chrome PORTABLE của các account — có trên MỌI máy chạy tool, không cố định path/drive/số Copy
    suite_root = os.path.dirname(_HERE)              # ...\VE3_SUITE
    roots, seen = [], set()
    for r in (suite_root, os.path.dirname(suite_root), r"D:\VE3_SUITE", r"C:\VE3_SUITE",
              r"E:\VE3_SUITE", r"D:\New folder\veo3top"):
        if r and r not in seen:
            seen.add(r); roots.append(r)
    for r in roots:
        for pat in (os.path.join(r, "GoogleChromePortable*", "App", "Chrome-bin", "*", "chrome.exe"),
                    os.path.join(r, "GoogleChromePortable*", "App", "chrome-bin", "chrome.exe"),
                    os.path.join(r, "*", "App", "Chrome-bin", "*", "chrome.exe")):
            for m in glob.glob(pat):
                if os.path.exists(m):
                    return m
    # 4) PATH
    w = shutil.which("chrome") or shutil.which("chrome.exe")
    if w:
        return w
    # 5) Edge (Chromium) — grecaptcha vẫn chạy được -> mint token OK, fallback cuối (còn hơn không có chrome)
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            e = os.path.join(base, r"Microsoft\Edge\Application\msedge.exe")
            if os.path.exists(e):
                return e
    return "chrome.exe"


class _Minter:
    """1 chrome đẻ token. blank=True -> chrome trắng (temp profile). blank=False -> account login."""
    def __init__(self, chrome_exe, profile_dir, port, pool, log, blank=True, action=None, persistent=False, factory=None, index=0, total=1,
                 ipv6=False, clean=False, visible=False, ipv6_port=1100):
        self.factory = factory       # ref TokenFactory để nhận tín hiệu RESET (lỗi tích nhiều -> chrome fresh)
        self._seen_reset_gen = 0
        self._idx = index; self._total = max(1, total)   # reset SO LE: mỗi lần chỉ 1 chrome reset (tránh blackout token)
        self.blank = blank
        self.action = action                # None=VIDEO_GENERATION (mặc định cdp); ảnh='IMAGE_GENERATION'
        # persistent=True: blank dùng profile CỐ ĐỊNH warm (tích luỹ trust reCAPTCHA giống veo3top),
        # KHÔNG temp mới tinh + recycle = reload page (giữ profile). Dùng cho nhà máy pool.
        self.persistent = bool(persistent)
        # CÔNG THỨC THẮNG VIDEO (đã ra 200): mint token qua IPv6 tươi + cờ CLEAN + cửa sổ hiện.
        #  ipv6=True  -> con chrome mint bind IPv6 pool (budget reCAPTCHA riêng, né TOO_MUCH_TRAFFIC IP máy)
        #  clean=True -> VEO3TOP_CLEAN_FLAGS (bỏ cờ lộ-bot --test-type/--disable-gpu -> né UNUSUAL_ACTIVITY)
        #  visible=True -> offscreen=False (GPU thật, không đẩy cửa sổ ra ngoài = tín hiệu bot nhẹ)
        self.want_ipv6 = bool(ipv6)
        self.want_clean = bool(clean)
        self.want_visible = bool(visible)
        self._ipv6_port = ipv6_port + port  # port RIÊNG cho socks IPv6 của minter này (tránh đụng account/img)
        self.ipv6_tr = None
        if self.want_clean:
            os.environ["VEO3TOP_CLEAN_FLAGS"] = "1"
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

    def _ensure_ipv6(self):
        """Lazily dựng 1 IPv6Transport riêng cho minter này -> con chrome mint đi ra IPv6 pool tươi.
        Trả proxy_url (socks5) hoặc None nếu pool lỗi (rơi về IP máy — vẫn chạy, chỉ dễ TOO_MUCH_TRAFFIC)."""
        if self.ipv6_tr is not None:
            try: return self.ipv6_tr.proxy_url()
            except Exception: return None
        try:
            import ipv6_transport as ip6t
            tr = ip6t.IPv6Transport(f"tok_{self.port}", port=self._ipv6_port, log=lambda *_: None)
            if tr.start():
                self.ipv6_tr = tr
                return tr.proxy_url()
        except Exception as e:
            self.log(f"[tokenfactory:{self.port}] IPv6 start lỗi: {e} — mint tạm IP máy")
        return None

    def _rotate_ipv6(self):
        """Xoay source IPv6 (khi RESET do lỗi reCAPTCHA tích nhiều) -> budget mint mới, né TOO_MUCH_TRAFFIC."""
        if self.ipv6_tr is not None:
            try: self.ipv6_tr.rotate()
            except Exception: pass

    def _open(self, fresh_profile=False):
        try:
            try:
                if self.cdp: self.cdp.close(kill=True)
            except Exception: pass
            if self.blank and fresh_profile and not self.persistent and self.temp and os.path.isdir(self.temp):
                _rmtree_hard(self.temp); self.temp = None
            prof = self._profile() if (self.blank and (fresh_profile or not self.temp)) else (self.temp if self.blank else self.profile_dir)
            # IPv6 tươi cho con chrome mint (chỉ blank): lazily start 1 transport/minter, reset=xoay IP
            proxy = None
            if self.want_ipv6 and self.blank:
                proxy = self._ensure_ipv6()
            offscreen = not self.want_visible if self.blank else True
            self.cdp = ChromeCDP(self.chrome_exe, prof, self.port, offscreen=offscreen, log=self.log, proxy=proxy)
            if not (self.cdp.connect(launch_timeout=45) and self.cdp.wait_ready(35)):
                return False
            if not self.blank:
                try: self.email = self.cdp.email()
                except Exception: pass
            elif self.want_clean:
                # WARM-UP reCAPTCHA: chrome tươi -> mint ĐẦU thường UNUSUAL, mint SAU mới điểm cao (đo được ~50%->~85%).
                # Đốt 1 token bỏ đi để "khởi động" session trước khi bơm token thật vào pool.
                try: self.cdp.mint_token(self.action)
                except Exception: pass
            return True
        except FileNotFoundError:
            # chrome.exe không tồn tại ở path này -> KHÔNG nuốt im, để run() log rõ (thiếu Chrome)
            return False
        except Exception as e:
            self.log(f"[tokenfactory:{self.port}] mở chrome lỗi: {e}")
            return False

    def get_auth(self):
        import flow_client as fc
        with self.lock:
            return {"bearer": self.cdp.bearer(), "cookie": fc.labs_cookie_header(self.cdp.cookies()),
                    "project": self.cdp.first_project_id(), "email": self.email, "ts": time.time()}

    def run(self):
        # RETRY mở chrome thay vì chết im: nếu thiếu Chrome/path sai -> log RÕ + thử lại (để user thấy & sửa,
        # và tự phục hồi nếu Chrome được cài sau). Trước đây _open raise -> thread chết lặng -> "không có chrome
        # nhưng log vẫn báo tạo video" vì không token mà job vẫn submit.
        tries = 0
        while not self._stop and not self._open(fresh_profile=True):
            tries += 1
            self.log(f"[tokenfactory:{self.port}] KHÔNG mở được chrome token (lần {tries}): '{self.chrome_exe}' "
                     f"— Chrome chưa cài đúng chỗ? Không có chrome = không token = video KHÔNG chạy dù log báo đang tạo. Thử lại 15s...")
            for _ in range(15):
                if self._stop: return
                time.sleep(1)
        if self._stop: return
        self.log(f"[tokenfactory:{self.port}] chrome {'TRẮNG(no-login)' if self.blank else 'ACCOUNT('+str(self.email)+')'} sẵn sàng ({self.chrome_exe}), đẻ token (paced {MINT_INTERVAL}s)")
        t_recycle = time.time()
        while not self._stop:
            # RESET theo LỖI (giống veo3top numreset) — SO LE: mỗi lần chỉ chrome tới lượt reset (không blackout token)
            if self.factory is not None and getattr(self.factory, "_reset_gen", 0) > self._seen_reset_gen:
                gen = self.factory._reset_gen
                self._seen_reset_gen = gen
                if (gen % self._total) == self._idx:
                    self.log(f"[tokenfactory:{self.port}] RESET chrome (so le, reCAPTCHA context mới)")
                    self._rotate_ipv6()   # IP mint mới -> né TOO_MUCH_TRAFFIC (budget reCAPTCHA riêng)
                    with self.lock:
                        self._open(fresh_profile=True)
                    t_recycle = time.time()
            if self.pool.maxsize and self.pool.qsize() >= self.pool.maxsize:
                time.sleep(0.3); continue
            # DEMAND-DRIVEN: không có get() gần đây (bypass đang lo tốt) -> NGƯNG mint (khỏi recycle chrome vô ích).
            # Chrome vẫn MỞ sẵn -> khi bypass trượt gọi get() -> _last_demand cập nhật -> mint lại ngay (độ trễ nhỏ).
            if TOKEN_IDLE_SECS > 0 and self.factory is not None and \
               (time.time() - getattr(self.factory, "_last_demand", 0)) > TOKEN_IDLE_SECS:
                time.sleep(1); continue
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
                            self._rotate_ipv6()                  # chrome tươi + IP tươi (budget reCAPTCHA mới) = công thức thắng
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
        try:
            if self.ipv6_tr: self.ipv6_tr.stop()
        except Exception: pass
        # GIỮ profile warm (persistent) — chỉ xoá temp thường
        if self.temp and os.path.isdir(self.temp) and not self.persistent:
            _rmtree_hard(self.temp)


class TokenFactory:
    def __init__(self, mode="blank", chrome_exe=None, profile_dir=None, n_chromes=1, buffer=24, base_port=9840, log=print, action=None, persistent=False,
                 ipv6=False, clean=False, visible=False):
        self.mode = mode
        self.action = action     # None=VIDEO_GENERATION; ảnh='IMAGE_GENERATION' (đẻ token đúng action cho endpoint ảnh)
        self.persistent = bool(persistent)   # blank: profile CỐ ĐỊNH warm (trust reCAPTCHA) — giống veo3top
        self.ipv6 = bool(ipv6); self.clean = bool(clean); self.visible = bool(visible)  # công thức thắng VIDEO token
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
        self._last_demand = time.time()   # lần get() gần nhất -> demand-driven mint (idle thì ngưng, khỏi churn)
        # ADAPTIVE: reset chrome theo LẦN BỊ CHAI (lỗi reCAPTCHA tích) thay vì đợi RECYCLE cố định.
        # Hạ 30->6: chrome chai chỉ sau vài token (đã đo), phí 30 token xấu là quá nhiều -> reset sớm = rate cao hơn.
        self.RESET_ON_ERRORS = int(os.environ.get("VEO3TOP_RESET_ON_ERRORS", "6") or "6")

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
                m = _Minter(self.chrome_exe, self.profile_dir, self.base_port + i, self.pool, self.log, blank=blank, action=self.action, persistent=self.persistent, factory=self, index=i, total=n,
                            ipv6=self.ipv6, clean=self.clean, visible=self.visible)
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
        self._last_demand = time.time()   # có nhu cầu -> đánh thức minter mint (demand-driven)
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

def get_factory(mode="blank", chrome_exe=None, profile_dir=None, n_chromes=1, base_port=9840, log=print, persistent=False,
                ipv6=False, clean=False, visible=False, **_ignore):
    global _FACTORY
    with _FLOCK:
        if _FACTORY is None:
            _FACTORY = TokenFactory(mode=mode, chrome_exe=chrome_exe, profile_dir=profile_dir,
                                    n_chromes=n_chromes, base_port=base_port, log=log, persistent=persistent,
                                    ipv6=ipv6, clean=clean, visible=visible)
            _FACTORY.start()
        return _FACTORY


# --- Factory RIÊNG cho ẢNH (action='IMAGE_GENERATION'), tách hẳn factory video ở trên ---
_IMG_FACTORY = None
_IMG_FLOCK = threading.Lock()

def get_image_factory(mode="blank", chrome_exe=None, profile_dir=None, n_chromes=1, base_port=9740, log=print,
                      ipv6=False, clean=False, visible=False, **_ignore):
    """Nhà máy token cho ẢNH — đẻ recaptcha action IMAGE_GENERATION. Singleton riêng (không đụng video).
    ipv6/clean/visible: CÔNG THỨC THẮNG (mang từ video sang — đã đo ảnh ~75-80% với chrome trắng tươi + IPv6 + CLEAN)."""
    global _IMG_FACTORY
    with _IMG_FLOCK:
        if _IMG_FACTORY is None:
            _IMG_FACTORY = TokenFactory(mode=mode, chrome_exe=chrome_exe, profile_dir=profile_dir,
                                        n_chromes=n_chromes, base_port=base_port, log=log,
                                        action="IMAGE_GENERATION",
                                        ipv6=ipv6, clean=clean, visible=visible)
            _IMG_FACTORY.start()
        return _IMG_FACTORY
