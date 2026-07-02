"""
image_pool_browser — NHÀ MÁY ẢNH browser-based (IN-PAGE generate). Tách bạch với VE3 (khách hàng chỉ
submit + chờ); nhà máy tự lo TOÀN BỘ vòng đời account.

CHỐT (đã chứng minh ra ảnh 200): ảnh phải submit batchGenerateImages TRONG browser page (reCAPTCHA mint
cùng context -> trust cao). curl_cffi + token rời -> 403/unusual/quota.

KIẾN TRÚC (giống video: nhiều slot song song, IPv6/account, xoay model, retry kiên nhẫn):
- Ứng viên = TẤT CẢ account gmail (gmail_profile_manager). Slot bốc ứng viên -> CHUẨN BỊ (có session sẵn thì
  dùng ngay như cookie video; chưa thì login+warm+tạo project) -> SẢN XUẤT (in-page, model Pro->Nano2->Lite).
- Tự phân loại: ra ảnh = GOOD (giữ+nhớ). 403/unusual hết mọi model = BAD (cách ly, bốc account khác).
  login fail = DEAD. -> nhà máy TỰ LỚN: chạy lâu dò thêm GOOD, nhớ qua các lần chạy (nhanh dần).
- Persist .veo3top_imgpool_state.json: lần sau reuse GOOD ngay, bỏ qua BAD.
- Giới hạn login đồng thời (semaphore) tránh mở nhiều chrome 1 lúc.

API: POST /generate_image {prompt,out_path,aspect?,seed?,image_inputs?} -> {success,info,error}; GET /health.
"""
import os, sys, json, time, threading, queue, argparse, random, sqlite3, subprocess, importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import flow_client as fc
import ipv6_transport as ip6t
from cdp_chrome import ChromeCDP, SITE_KEY, FLOW_URL
try:
    from auth_cache import AuthCache
    _AUTHCACHE = AuthCache(log=lambda *_: None)   # lưu cookie+bearer/account (như veo3top) -> tái dùng nhiều lần
except Exception:
    _AUTHCACHE = None

SUITE = os.path.dirname(_HERE)
GPM = os.path.join(SUITE, "tools", "gmail_profile_manager")
GPM_DB = os.path.join(GPM, "config", "accounts.db")
POOL_PROFILES = os.path.join(GPM, "config", "pool_img_profiles")       # profile ẢNH
POOL_VID_PROFILES = os.path.join(GPM, "config", "pool_vid_profiles")   # profile VIDEO (tách riêng - account có thể trùng email)
STATE_FILE = os.path.join(SUITE, ".veo3top_imgpool_state.json")

IMG_MODELS = [m.strip() for m in os.environ.get("VEO3TOP_IMG_MODELS", "GEM_PIX_2,NARWHAL,HARBOR_SEAL").split(",") if m.strip()]
MODEL_REST_SECS = int(os.environ.get("VEO3TOP_IMG_MODEL_REST", "1800") or "1800")
N_SLOTS = int(os.environ.get("VEO3TOP_IMG_POOL_ACCOUNTS", "10") or "10")
LOGIN_CONCURRENCY = int(os.environ.get("VEO3TOP_IMG_LOGIN_CONCURRENCY", "3") or "3")
# CHỈ REUSE COOKIE (mặc định BẬT): pool KHÔNG tự login (login hàng loạt cùng 1 IP -> Google bắt reCAPTCHA challenge
# -> gãy hết -> DEAD). User login riêng ở GUI (rải chậm) để profile có cookie; pool chỉ MỞ profile đã login sẵn +
# generate. Account chưa có cookie -> đánh dấu 'nologin' (bỏ qua nhẹ, KHÔNG dead, thử lại sau khi user login).
IMG_NO_LOGIN = os.environ.get("VEO3TOP_IMG_NO_LOGIN", "1") == "1"
NOLOGIN_COOLDOWN = int(os.environ.get("VEO3TOP_IMG_NOLOGIN_COOLDOWN", "1800") or "1800")  # 30': account cookie-cũ/session-chết (qua được filter nhưng prepare fail) -> nghỉ lâu, đỡ phí slot churn. User re-login GUI + restart để nạp lại
# ẢNH có NHIỀU account -> retry reCAPTCHA VỪA PHẢI rồi ĐỔI account (tận dụng số đông), khác video (ít acc -> retry nhiều).
GEN_ATTEMPTS = int(os.environ.get("VEO3TOP_IMG_GEN_ATTEMPTS", "2") or "2")   # retry token/1 account/1 job: 403 là PER-ACCOUNT (retry cùng account 403 y hệt) -> chỉ 2 lần rồi ĐỔI account tươi (nhanh hơn nhiều so 5)
GEN_TIMEOUT_MS = int(os.environ.get("VEO3TOP_IMG_GEN_TIMEOUT_MS", "180000") or "180000")  # 3 PHÚT: request tạo ảnh ĐỒNG BỘ (chờ Google tạo XONG ảnh mới trả về). Tạo ảnh lâu -> để rộng cho AN TOÀN, khỏi cắt oan. 403 vẫn trả nhanh (~3s) nên không chậm phát hiện account đốt.
# GIÃN NHỊP (như FlowKit/server có delay giữa request): bắn dồn -> reCAPTCHA UNUSUAL_ACTIVITY nhanh. Giãn ~4s giữa
# các lần tạo trên CÙNG account -> account "sống" lâu hơn, 403 ít hơn (đã học từ 2 tool + test sustained).
IMG_PACING = float(os.environ.get("VEO3TOP_IMG_PACING", "4.0") or "4.0")
# CLEAR-ON-403 (đòn bẩy FlowKit đã chứng minh): 403 -> xóa reCAPTCHA state client-side (localStorage/indexedDB/cache)
# GIỮ cookie login + reload -> gieo lại điểm reCAPTCHA (test: hồi ~1/3 account 403). Non-service, giữ login.
CLEAR_ON_403 = os.environ.get("VEO3TOP_IMG_CLEAR_ON_403", "1") == "1"
CLEAR_RETRIES = int(os.environ.get("VEO3TOP_IMG_CLEAR_RETRIES", "2") or "2")   # số vòng clear+reload+retry trước khi bỏ account
SWAP_GIVEUP = int(os.environ.get("VEO3TOP_IMG_SWAP_GIVEUP", "3") or "3")     # swap N lượt liên tiếp (bị đốt) -> cách ly account, dùng acc tươi
# (ĐÃ BỎ logic "mỗi acc N ảnh rồi nghỉ" — user: KHAI THÁC account tới khi reCAPTCHA lì mới xoay, không giới hạn số ảnh)
JOB_MAX_CYCLES = 20   # job đổi account tối đa nhiều lần (số đông account) trước khi bỏ
# ACC CÓ THỂ HỒI: BAD/DEAD chỉ NGHỈ có thời hạn rồi thử LẠI (flag reCAPTCHA thường tạm) -> tận dụng tối đa,
# tối ưu SẢN LƯỢNG. Hết cooldown account lại vào vòng ứng viên; nếu ra ảnh -> lên GOOD (đã hồi).
BAD_COOLDOWN = int(os.environ.get("VEO3TOP_IMG_BAD_COOLDOWN", "5400") or "5400")    # 1.5h
DEAD_COOLDOWN = int(os.environ.get("VEO3TOP_IMG_DEAD_COOLDOWN", "10800") or "10800")  # 3h (login fail)
# 403 reCAPTCHA KHÔNG phải account hỏng (account vẫn login OK ở Flow) — chỉ điểm/egress dip. Nghỉ NGẮN rồi thử lại,
# KHÔNG sideline 1.5h (kẻo 403-flood làm cạn cả pool: 100 ready -> 10). No-login mode: account có cookie luôn tái dùng được.
RECAPTCHA_REST = int(os.environ.get("VEO3TOP_IMG_RECAPTCHA_REST", "600") or "600")   # 10' nghỉ hồi điểm reCAPTCHA (account CHƯA từng ra ảnh)
# reCAPTCHA bimodal: account ĐÃ ra ảnh -> "ấm", tiếp tục ra được. Nên account tốt (đã win) chỉ nghỉ NGẮN rồi quay lại
# khai thác tiếp (giữ winner chạy liên tục); account lạnh (chưa win) mới nghỉ dài RECAPTCHA_REST.
GOOD_REST = int(os.environ.get("VEO3TOP_IMG_GOOD_REST", "120") or "120")              # 2' cho account đã ra ảnh
# HÂM NÓNG PHIÊN (như veo3top phiên WebView2 ấm): pool mở chrome LẠNH rồi mint ngay -> điểm reCAPTCHA thấp -> cold-403.
# Warm-up: di chuột (CDP trusted) + cuộn + prime grecaptcha + đợi vài giây -> phiên trông 'người thật/đã dùng' -> điểm CAO hơn.
IMG_WARMUP = os.environ.get("VEO3TOP_IMG_WARMUP", "1") == "1"
WARMUP_SECS = float(os.environ.get("VEO3TOP_IMG_WARMUP_SECS", "3.0") or "3.0")
# --- Circuit-breaker QUOTA reCAPTCHA ẢNH (đã CHỨNG MINH: action IMAGE_GENERATION bị Google chặn TOÀN CỤC theo
#     lúc; đổi account/IP/mint-cách-khác đều VÔ ÍCH, VIDEO cùng máy vẫn chạy). Mỗi lần thử FAIL vẫn ĐỐT 1
#     assessment -> làm quota hồi CHẬM hơn. Nên khi phát hiện quota cạn: NGỪNG đốt (chỉ probe nhẹ), TỰ CHẠY LẠI
#     khi Google mở quota. Không hammer 8 slot vô nghĩa. ---
QUOTA_BLOCK_CONSEC = int(os.environ.get("VEO3TOP_IMG_QUOTA_CONSEC", "20") or "20")   # lỗi giờ chủ yếu PER-ACCOUNT (bị đốt reputation), không phải quota global -> xoay qua NHIỀU account tìm cái chạy trước khi coi là cạn global
QUOTA_PROBE_INTERVAL = int(os.environ.get("VEO3TOP_IMG_PROBE_INTERVAL", "300") or "300")  # khi cạn: probe 1 token mỗi 5' (đỡ đốt quota)
QUOTA_PROBE_ATTEMPTS = int(os.environ.get("VEO3TOP_IMG_PROBE_ATTEMPTS", "2") or "2")  # khi cạn: mỗi job chỉ thử 2 token (không storm)
# --- FIX ĐÃ CHỨNG MINH (ra ảnh 200 nhiều lần): reCAPTCHA IMAGE_GENERATION rất khắt khe -> Chrome phải TỐI GIẢN
#     (KHÔNG cờ bot: --test-type/--no-sandbox/--disable-gpu/AutomationControlled/offscreen). ĐÃ TEST: cờ sạch +
#     IPv6 pool proxy VẪN ra ảnh -> GIỮ IPv6 (né 429 như video), chỉ cần bỏ cờ bẩn. Cửa sổ HIỆN (offscreen là tín hiệu bot). ---
os.environ["VEO3TOP_CLEAN_FLAGS"] = "1"    # LUÔN dùng cờ tối giản cho ẢNH (điểm reCAPTCHA cao)
IMG_NATIVE = os.environ.get("VEO3TOP_IMG_NATIVE", "0") == "1"   # 0 = GIỮ IPv6 (mặc định, như video); 1 = mạng máy
# ẨN chrome (mặc định BẬT): đẩy cửa sổ ra ngoài màn hình (vẫn GPU -> ảnh vẫn chạy). Tắt (0) -> hiện cửa sổ. Như video.
IMG_HIDE = os.environ.get("VEO3TOP_IMG_HIDE", "1") == "1"


def _log(msg):
    print(f"[imgpool] {msg}", flush=True)


def _system_chrome():
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        b = os.environ.get(env)
        if b:
            p = os.path.join(b, r"Google\Chrome\Application\chrome.exe")
            if os.path.exists(p):
                return p
    for p in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if os.path.exists(p):
            return p
    return "chrome.exe"


_CHROME = _system_chrome()


def _load_login():
    try:
        spec = importlib.util.spec_from_file_location("ve3_google_login",
                                                      os.path.join(SUITE, "tools", "ve3", "google_login.py"))
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        return m.login_google_chrome
    except Exception as e:
        _log(f"không nạp được google_login: {e}")
        return None


def load_gmail_accounts():
    """Tất cả account gmail có creds (email|password|totp) từ gmail_profile_manager DB."""
    out = []
    try:
        c = sqlite3.connect(GPM_DB); c.row_factory = sqlite3.Row
        rows = c.execute("SELECT email,password,totp_secret FROM accounts "
                         "WHERE password!='' AND totp_secret!='' "
                         "ORDER BY (health_status='alive') DESC, last_used DESC").fetchall()
        c.close()
        seen = set()
        for r in rows:
            e = (r["email"] or "").strip().lower()
            if e and e not in seen:
                seen.add(e)
                out.append({"email": r["email"], "password": r["password"], "totp": r["totp_secret"]})
    except Exception as e:
        _log(f"load_gmail_accounts lỗi: {e}")
    return out


def _profile_logged_in(email):
    """Kiểm tra NHANH profile account ĐÃ login Google chưa (có cookie SAPISID *.google.com) — để pool CHỈ dùng
    account đã login, KHỎI phí slot mở chrome cho gmail chưa login. Đọc Cookies DB (copy tạm) không mở chrome."""
    import glob, sqlite3, tempfile, shutil
    prof = os.path.join(POOL_PROFILES, "p_" + email.replace("@", "_").replace(".", "_"))
    ck = None
    for p in (glob.glob(os.path.join(prof, "**", "Cookies"), recursive=True) +
              glob.glob(os.path.join(prof, "**", "Network", "Cookies"), recursive=True)):
        ck = p; break
    if not ck:
        return False
    tmp = os.path.join(tempfile.gettempdir(), "poolck_" + os.path.basename(prof))
    try:
        shutil.copy(ck, tmp)
        con = sqlite3.connect(tmp)
        n = con.execute("SELECT COUNT(*) FROM cookies WHERE host_key LIKE '%google.com%' "
                        "AND name LIKE '%SAPISID%'").fetchone()[0]
        con.close()
        return n > 0
    except Exception:
        return False
    finally:
        try: os.remove(tmp)
        except Exception: pass


class Account:
    def __init__(self, idx, email, password, totp, profile_base=None):
        self.idx = idx; self.email = email; self.password = password; self.totp = totp
        # profile RIENG theo pool (anh=pool_img_profiles, video=pool_vid_profiles) -> account trung email khong dung profile
        self.profile = os.path.join(profile_base or POOL_PROFILES, "p_" + email.replace("@", "_").replace(".", "_"))
        self.cdp = None; self.ipv6 = None; self.project = None
        self.state = "new"      # new | ready | bad | dead
        self.good = False
        self.wins = 0; self.fails = 0; self.busy = False; self.last_kind = ""
        self.model_wins = {}; self.model_rest = {}
        self.last_swap = 0.0   # lần gần nhất bị đổi lượt (reCAPTCHA lì) -> xoay về cuối ưu tiên, KHÔNG nghỉ cứng
        self.swaps = 0         # số lượt swap LIÊN TIẾP (reCAPTCHA lì); >= SWAP_GIVEUP -> cách ly; ra ảnh -> reset 0
        self.ref_cache = {}    # hash(rawImageBytes) -> media name (đã upload cho account NÀY) -> khỏi upload lại
        self._port = None

    def pick_model(self):
        now = time.time()
        for m in IMG_MODELS:
            if now >= self.model_rest.get(m, 0):
                return m
        return None

    def _start_ipv6(self):
        if self.ipv6:
            return
        try:
            tr = ip6t.IPv6Transport(f"imgb_{self.idx}", port=9600 + self.idx, log=lambda *_: None)
            if tr.start():
                self.ipv6 = tr
        except Exception:
            self.ipv6 = None

    def _open_cdp(self):
        # ẢNH: GIỮ IPv6 pool (né 429 như video) + cờ TỐI GIẢN (ChromeCDP clean, đã set VEO3TOP_CLEAN_FLAGS) +
        # cửa sổ HIỆN (offscreen là tín hiệu bot). ĐÃ CHỨNG MINH: cờ sạch + IPv6 -> ra ảnh 200. IMG_NATIVE=1 -> mạng máy.
        if IMG_NATIVE:
            proxy = None
        else:
            self._start_ipv6()
            proxy = self.ipv6.proxy_url() if self.ipv6 else None
        port = 9500 + self.idx
        c = ChromeCDP(_CHROME, self.profile, port, offscreen=IMG_HIDE, log=lambda *_: None, proxy=proxy)
        try:
            if not (c.connect(launch_timeout=45) and c.wait_ready(35, verify_token=False)):
                try: c.close(kill=True)
                except Exception: pass
                return None
        except Exception:
            try: c.close(kill=True)
            except Exception: pass
            return None
        return c

    def _clear_captcha_data(self):
        """403 -> XÓA reCAPTCHA state client-side cho origin labs.google + aisandbox (localStorage/indexedDB/cache/SW)
        NHƯNG GIỮ cookies (login SSO còn) + RELOAD -> reCAPTCHA chấm điểm LẠI từ đầu (gieo lại xúc xắc). Đã test:
        hồi ~1/3 account 403->200, giữ login. Đòn bẩy FlowKit (reset captcha), non-service. Trả True nếu còn login."""
        if not self.cdp:
            return False
        types = "local_storage,indexeddb,cache_storage,service_workers,cache,shader_cache,websql,appcache,file_systems"  # KHÔNG cookies -> giữ login
        for o in ("https://labs.google", "https://aisandbox-pa.googleapis.com"):
            try: self.cdp.cmd("Storage.clearDataForOrigin", origin=o, storageTypes=types)
            except Exception: pass
        try: self.cdp.ev("(async()=>{try{localStorage.clear();sessionStorage.clear();if(indexedDB.databases){const d=await indexedDB.databases();for(const x of d)indexedDB.deleteDatabase(x.name);}if(window.caches){const k=await caches.keys();for(const x of k)caches.delete(x);}}catch(e){}return 1;})()", to=6000)
        except Exception: pass
        try: self.cdp.cmd("Page.navigate", url=FLOW_URL)
        except Exception: pass
        time.sleep(3)
        try: self.cdp.wait_ready(30, verify_token=False)
        except Exception: pass
        time.sleep(1.0)
        try: return bool(self.cdp.email())
        except Exception: return True

    def _warmup(self):
        """HÂM NÓNG phiên: CHỈ ĐỢI vài giây cho phiên 'già' tự nhiên trên trang Flow đã load, RỒI mới mint token.
        KHÔNG giả chuột/JS (event synthetic dễ bị reCAPTCHA soi là bot -> phản tác dụng). Đơn giản = an toàn."""
        if not IMG_WARMUP or not self.cdp:
            return
        try:
            time.sleep(WARMUP_SECS)
        except Exception:
            pass

    def _save_cookie(self):
        """LƯU cookie+bearer+project của account ra auth_cache (như veo3top) -> tái dùng nhiều lần / cross-tool.
        (In-page generate không cần cookie này, nhưng lưu để: check nhanh, dùng lại ở nơi khác, giống veo3top.)"""
        if _AUTHCACHE is None or not self.cdp:
            return
        try:
            bearer = self.cdp.bearer()
            cookie = fc.labs_cookie_header(self.cdp.cookies())
            if bearer and cookie:
                _AUTHCACHE._save(self.email, {"bearer": bearer, "cookie": cookie,
                                              "project": self.project, "email": self.email, "ts": time.time()})
        except Exception:
            pass

    def _kill_profile_chrome(self):
        try:
            base = os.path.basename(self.profile)
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                            f"Where-Object {{$_.CommandLine -like '*{base}*'}} | "
                            "ForEach-Object { taskkill /F /T /PID $_.ProcessId 2>$null }"],
                           capture_output=True, timeout=15, creationflags=0x08000000)
        except Exception:
            pass

    def prepare(self, login_fn, login_sem, log):
        """CHUẨN BỊ account: reuse session sẵn (như cookie video) -> ready; chưa có -> login+warm+tạo project.
        Trả True nếu ready (có project). login nặng nên đi qua semaphore (giới hạn đồng thời)."""
        os.makedirs(self.profile, exist_ok=True)
        # 1) thử dùng session SẴN trong profile (nhanh, không login) — giống video reuse cookie
        c = self._open_cdp()
        if c:
            try:
                # THỬ email() NHIỀU LẦN: page/egress (IPv6) chậm dễ trả None thoáng qua -> account ĐÃ login bị gán
                # 'nologin' OAN. Chờ session load thật sự trước khi kết luận chưa login.
                em = None
                for _try in range(4):
                    try: em = c.email()
                    except Exception: em = None
                    if em: break
                    time.sleep(2)
                if em:
                    pid = c.fresh_project()    # TẠO project MỚI mỗi lần (user: không dùng project cũ); lỗi -> fallback project sẵn
                    if pid:
                        self.cdp = c; self.project = pid; self.state = "ready"
                        self._save_cookie()   # lưu cookie tái dùng (như veo3top)
                        self._warmup()        # đợi vài giây cho phiên 'già' -> điểm reCAPTCHA cao hơn (non-service)
                        log(f"♻️ {self.email}: reuse session sẵn (project {self.project[:8]}) -> ready")
                        return True
                    # đã login (có email) nhưng tạo project lỗi (API/egress thoáng) -> nghỉ ngắn thử lại, KHÔNG coi là chưa login
                    self.state = "resting"
                    log(f"😴 {self.email}: đã login nhưng tạo project lỗi (egress?) -> nghỉ ngắn thử lại")
                    try: c.close(kill=True)
                    except Exception: pass
                    return False
            except Exception:
                pass
            try: c.close(kill=True)
            except Exception: pass
        # CHỈ-REUSE-COOKIE: chưa có session sẵn -> KHÔNG tự login (tránh Google reCAPTCHA-challenge khi login hàng
        # loạt cùng 1 IP). Đánh dấu 'nologin' để bỏ qua nhẹ + thử lại sau (user login ở GUI xong sẽ reuse được).
        if IMG_NO_LOGIN:
            self.state = "nologin"
            log(f"⏭️ {self.email}: chưa có cookie đăng nhập -> bỏ qua (hãy login ở GUI để pool reuse)")
            return False
        # 2) LOGIN + WARM (tạo project) — giới hạn đồng thời
        if not login_fn:
            self.state = "dead"; return False
        with login_sem:
            self._kill_profile_chrome(); time.sleep(2)
            try:
                ok = login_fn({"id": self.email, "password": self.password, "totp_secret": self.totp},
                              chrome_portable=_CHROME, profile_dir=self.profile, worker_id=200 + self.idx, proxy_arg="")
            except Exception as e:
                log(f"{self.email}: login lỗi {e}"); ok = False
            if not ok:
                self.state = "dead"; log(f"✗ {self.email}: login/warm THẤT BẠI -> DEAD"); return False
            self._kill_profile_chrome(); time.sleep(3)
        c = self._open_cdp()
        if not c:
            self.state = "dead"; return False
        try:
            self.project = c.first_project_id()
        except Exception:
            self.project = None
        if not self.project:
            try: c.close(kill=True)
            except Exception: pass
            self.state = "dead"; log(f"✗ {self.email}: không project sau warm -> DEAD"); return False
        self.cdp = c; self.state = "ready"
        self._save_cookie()   # lưu cookie+bearer ra auth_cache (như veo3top) -> tái dùng
        log(f"✅ {self.email}: login+warm xong (project {self.project[:8]}, egress {'ipv6' if self.ipv6 else 'ipmay'}) -> ready")
        return True

    def _upload_ref(self, b64, mime="image/png"):
        """Upload 1 ảnh reference (b64) -> media name (để dùng trong imageInputs {imageInputType,name}).
        QUAN TRỌNG: upload qua PYTHON CURL DIRECT (bearer từ page), KHÔNG qua page-IPv6 (socks5 IPv6 KHÔNG upload
        được body lớn -> 'Failed to fetch'). Upload không cần reCAPTCHA nên direct OK. Cache theo hash."""
        key = str(hash(b64))
        if key in self.ref_cache:
            return self.ref_cache[key]
        name = None
        try:
            bearer = self.cdp.bearer()
            if bearer:
                url = f"{fc.BASE}/flow/uploadImage?key={fc.KEY}"
                payload = {"clientContext": {"sessionId": f";{int(time.time()*1000)}",
                                             "projectId": self.project, "tool": "PINHOLE"},
                           "imageBytes": b64}
                r = fc._post(url, fc._headers(bearer), json.dumps(payload), timeout=120, proxy=None)  # DIRECT
                if r.status_code in (200, 201):
                    media = (r.json() or {}).get("media") or {}
                    name = media.get("name") if isinstance(media, dict) else None
        except Exception:
            name = None
        if name:
            self.ref_cache[key] = name
        return name

    def _resolve_inputs(self, image_inputs):
        """ve3 gửi imageInputs=[{imageInputType,rawImageBytes,mimeType}] -> upload -> [{imageInputType,name}] (format API mới)."""
        out = []
        for inp in (image_inputs or []):
            if inp.get("name"):
                out.append({"imageInputType": inp.get("imageInputType", "IMAGE_INPUT_TYPE_REFERENCE"), "name": inp["name"]})
            elif inp.get("rawImageBytes"):
                nm = self._upload_ref(inp["rawImageBytes"], inp.get("mimeType", "image/png"))
                if nm:
                    out.append({"imageInputType": inp.get("imageInputType", "IMAGE_INPUT_TYPE_REFERENCE"), "name": nm})
                else:
                    return None   # upload lỗi -> báo để retry
        return out

    def generate_inpage(self, prompt, model, aspect, seed, image_inputs):
        proj = self.project
        # Reference (nhân vật): API MỚI cần UPLOAD ảnh -> tham chiếu bằng name (không nhúng rawImageBytes -> 400).
        if image_inputs:
            resolved = self._resolve_inputs(image_inputs)
            if resolved is None:
                return "retry", {"err": "upload reference lỗi"}
            image_inputs = resolved
        # FORMAT MỚI (đã soi request THẬT chạy được): structuredPrompt + mediaGenerationContext.batchId + useNewMedia,
        # URL KHÔNG có ?key. clientContext: recaptchaContext + projectId + tool + sessionId.
        js = r'''(async()=>{
          try{
            const proj=%s, SITE=%s, MODEL=%s, ASPECT=%s, SEED=%d, PROMPT=%s, INPUTS=%s;
            const _sac=new AbortController(); const _sto=setTimeout(()=>_sac.abort(),12000);  // session fetch timeout 12s
            let sess; try{ sess=await (await fetch('/fx/api/auth/session',{credentials:'include',signal:_sac.signal})).json(); } finally{ clearTimeout(_sto); }
            if(!sess||!sess.access_token) return {k:'auth'};
            const tok=await grecaptcha.enterprise.execute(SITE,{action:'IMAGE_GENERATION'});
            const ctx={recaptchaContext:{token:tok,applicationType:'RECAPTCHA_APPLICATION_TYPE_WEB'},
              projectId:proj,tool:'PINHOLE',sessionId:';'+Date.now()};
            const req={clientContext:ctx,imageModelName:MODEL,imageAspectRatio:ASPECT,
              structuredPrompt:{parts:[{text:PROMPT}]},seed:SEED,imageInputs:INPUTS};
            const body={clientContext:ctx,
              mediaGenerationContext:{batchId:(crypto.randomUUID?crypto.randomUUID():'b-'+Date.now())},
              useNewMedia:true,requests:[req]};
            const ac=new AbortController(); const _to=setTimeout(()=>ac.abort(),%d);  // TIMEOUT: IPv6 treo -> huy nhanh, nha slot (mac dinh 18s)
            let r; try{ r=await fetch('https://aisandbox-pa.googleapis.com/v1/projects/'+proj+'/flowMedia:batchGenerateImages',
              {method:'POST',headers:{'Content-Type':'text/plain;charset=UTF-8','Authorization':'Bearer '+sess.access_token},body:JSON.stringify(body),signal:ac.signal}); }
            finally{ clearTimeout(_to); }
            const status=r.status; const txt=await r.text(); let j=null; try{j=JSON.parse(txt);}catch(e){}
            return {status:status, body:txt.slice(0,300), json:j};
          }catch(e){return {k:'jserr', body:String(e)};}
        })()''' % (json.dumps(proj), json.dumps(SITE_KEY), json.dumps(model),
                   json.dumps(aspect), int(seed), json.dumps(prompt), json.dumps(image_inputs or []),
                   GEN_TIMEOUT_MS)
        try:
            res = self.cdp.ev(js, to=GEN_TIMEOUT_MS + 15000)   # CDP eval phải chờ LÂU HƠN fetch (2') + đệm, kẻo cắt oan ảnh đang tạo
        except Exception as e:
            return "retry", {"err": str(e)[:120]}
        if not isinstance(res, dict):
            return "retry", {"err": "no result"}
        if res.get("k") == "auth":
            return "auth", {}
        if res.get("k") == "jserr":
            return "retry", {"err": res.get("body", "")[:150]}
        status = res.get("status"); j = res.get("json") or {}; body = res.get("body") or ""
        if status == 200:
            for m in j.get("media", []) or []:
                gi = (m.get("image") or {}).get("generatedImage") or {}
                fife = gi.get("fifeUrl"); name = m.get("name") or gi.get("mediaId")
                if fife or gi.get("encodedImage"):
                    return "ok", {"name": name, "fife": fife, "b64": gi.get("encodedImage"), "seed": gi.get("seed")}
            return "retry", {"err": "200 no image"}
        if status == 401:
            return "auth", {}
        # PHAN BIET (nhu video): reCAPTCHA cham diem thap -> RETRY token moi (kien nhan); quota MODEL -> doi model.
        # "reCAPTCHA evaluation failed" (403 PERMISSION_DENIED HoAC 429 RESOURCE_EXHAUSTED + UNUSUAL) = reCAPTCHA.
        low = body.lower()
        if "recaptcha" in low or "unusual_activity" in low or status == 403:
            return "recaptcha", {"body": body[:120]}   # -> retry token moi (khong bo account)
        if status == 429 or "RESOURCE_EXHAUSTED" in body:
            return "quota", {}                          # quota MODEL that su -> doi model
        return "other", {"body": body[:120], "status": status}

    def close(self):
        try:
            if self.cdp: self.cdp.close(kill=True)
        except Exception: pass
        try:
            if self.ipv6: self.ipv6.stop()
        except Exception: pass
        self.cdp = None
        self.ipv6 = None   # QUAN TRỌNG: xoá transport đã stop -> lần reuse sau _start_ipv6 tạo mới (khỏi dùng proxy chết)


class ImagePoolBrowser:
    def __init__(self, n_slots=N_SLOTS, log=_log):
        self.log = log
        self.q = queue.Queue()
        self.n_slots = n_slots
        self.candidates = []            # tất cả Account ứng viên
        self._cand_lock = threading.Lock()
        self._cand_i = 0
        self.active = {}                # email -> Account đang được slot dùng
        self.login_sem = threading.Semaphore(LOGIN_CONCURRENCY)
        self._running = False
        self._slots = []
        self.total_done = 0; self.total_fail = 0; self.started_ts = time.time()
        self._clock = threading.Lock()
        self._login = _load_login()
        self._state = self._load_state()
        # circuit-breaker quota reCAPTCHA ẢNH (toàn cục theo lúc)
        self._consec_recaptcha = 0        # số lần recaptcha-fail LIÊN TIẾP toàn pool (reset khi có 1 ảnh)
        self._quota_blocked_until = 0.0   # >now => đang coi quota cạn, chỉ probe nhẹ
        self._quota_alerted = False       # đã cảnh báo 1 lần (khỏi spam log)

    # ---------- persistence (nhớ GOOD/BAD qua các lần chạy) ----------
    def _load_state(self):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self):
        try:
            json.dump(self._state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _mark(self, email, **kw):
        with self._clock:
            d = self._state.get(email, {}); d.update(kw); d["ts"] = int(time.time())
            st = kw.get("state")
            if "until" in kw:                                    # caller đã set until riêng (vd account tốt nghỉ ngắn) -> tôn trọng
                pass
            elif st == "bad":
                d["until"] = int(time.time()) + BAD_COOLDOWN      # HỒI sau cooldown
            elif st == "dead":
                d["until"] = int(time.time()) + DEAD_COOLDOWN
            elif st == "nologin":
                d["until"] = int(time.time()) + NOLOGIN_COOLDOWN  # chưa login -> thử lại sau (user login GUI xong)
            elif st == "resting":
                d["until"] = int(time.time()) + RECAPTCHA_REST    # 403 reCAPTCHA -> nghỉ NGẮN rồi thử lại (account vẫn OK)
            elif st in ("good", "ready"):
                d.pop("until", None)                              # hồi -> xoá hạn nghỉ
            self._state[email] = d; self._save_state()

    def _in_cooldown(self, email):
        st = self._state.get(email, {})
        # bad/dead: nghỉ hồi; resting: account vừa làm đủ hạn ngạch, nghỉ ngắn cho reCAPTCHA hồi điểm (chống bơm dồn)
        # nologin: chưa có cookie -> nghỉ ngắn rồi thử lại (user có thể vừa login ở GUI)
        return st.get("state") in ("bad", "dead", "resting", "nologin") and time.time() < st.get("until", 0)

    # ---------- circuit-breaker quota reCAPTCHA ẢNH ----------
    def quota_blocked(self):
        return time.time() < self._quota_blocked_until

    def _note_gen(self, ok):
        """Ghi nhận 1 JOB xong: ok=True (ra ảnh) reset breaker; ok=False (recaptcha lì) tăng đếm, quá ngưỡng -> block."""
        with self._clock:
            if ok:
                self._consec_recaptcha = 0
                self._quota_blocked_until = 0.0
                self._quota_alerted = False
                return
            self._consec_recaptcha += 1
            if self._consec_recaptcha >= QUOTA_BLOCK_CONSEC:
                self._quota_blocked_until = time.time() + QUOTA_PROBE_INTERVAL
                if not self._quota_alerted:
                    self._quota_alerted = True
                    self.log("⚠️ QUOTA reCAPTCHA ẢNH (IMAGE_GENERATION) CẠN phía Google — "
                             f"đã {self._consec_recaptcha} lần liên tiếp bị chặn (video vẫn chạy bình thường). "
                             f"NGỪNG đốt quota, chỉ probe 1 lần/{QUOTA_PROBE_INTERVAL}s; TỰ CHẠY LẠI ra ảnh ngay khi Google mở quota.")

    # ---------- candidate acquisition ----------
    # XOAY VÒNG (không nghỉ cứng): account fail reCAPTCHA bị đẩy về CUỐI ưu tiên (last_swap mới nhất),
    # nhưng LUÔN còn trong pool -> không bao giờ hết account -> không treo. Chỉ DEAD (login fail) mới nghỉ cứng.
    def _next_candidate(self):
        with self._cand_lock:
            elig = [a for a in self.candidates
                    if a.email not in self.active and not self._in_cooldown(a.email)]
            if not elig:
                # tất cả đang bận hoặc DEAD-cooldown -> chờ (slot sẽ thử lại)
                return None
            def _rank(a):
                st = self._state.get(a.email, {}).get("state", a.state)
                ready_first = 0 if st in ("good", "ready") else 1   # account đã sẵn sàng -> reuse ngay, khỏi login
                return (ready_first, a.last_swap)                    # rồi tới account lâu chưa bị đổi lượt nhất
            a = min(elig, key=_rank)
            self.active[a.email] = a
            return a

    def _release_candidate(self, a):
        with self._cand_lock:
            self.active.pop(a.email, None)

    def start(self):
        os.makedirs(POOL_PROFILES, exist_ok=True)
        # CLEAN SLATE: kill sạch chrome pool ẢNH CŨ còn sót từ lần chạy trước (tree-kill) -> tránh chrome mồ côi
        # tích tụ khi restart/đổi số slot (vd đổi 10->5 vẫn còn 10 chrome cũ chạy).
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                            "Where-Object { $_.CommandLine -like '*pool_img_profiles*' } | "
                            "ForEach-Object { taskkill /F /T /PID $_.ProcessId 2>$null }"],
                           capture_output=True, timeout=30, creationflags=0x08000000)
            time.sleep(2)
        except Exception:
            pass
        gmails = load_gmail_accounts()
        # CHỈ DÙNG ACCOUNT ĐÃ LOGIN (no-login mode): lọc bỏ account chưa có cookie google -> pool KHÔNG phí slot mở
        # chrome cho gmail chưa login (fail rồi nghỉ). User login thêm ở GUI -> RESTART tool để nạp thêm.
        if IMG_NO_LOGIN:
            _before = len(gmails)
            gmails = [a for a in gmails if _profile_logged_in(a["email"])]
            self.log(f"🔐 chỉ dùng account ĐÃ LOGIN: {len(gmails)}/{_before} "
                     f"(bỏ {_before - len(gmails)} account chưa login/không cookie)")
        # DỌN RÁC TRẠNG THÁI khi khởi động (no-login mode): "dead"=login-fail từ code CŨ (giờ pool KHÔNG login nữa nên
        # vô nghĩa) + "bad"/"resting"=403 reCAPTCHA (account vẫn login OK ở Flow). -> XOÁ hết để TẤT CẢ account có cookie
        # quay lại pool thử reuse. Nếu account thật sự chưa login -> prepare tự đánh dấu 'nologin' (thử lại sau).
        if IMG_NO_LOGIN:
            cleared = 0
            with self._clock:
                for em, st in list(self._state.items()):
                    if st.get("state") in ("dead", "bad", "resting", "nologin"):
                        st.pop("until", None); st["state"] = "new"; cleared += 1
                self._save_state()
            if cleared:
                self.log(f"🧹 dọn {cleared} account trạng thái cũ (dead/bad từ lần login cũ) -> đưa lại vào pool thử reuse cookie")
        # sắp GOOD (đã biết) lên đầu -> reuse nhanh, nhà máy khởi động nhanh dần theo thời gian
        def _rank(a):
            st = self._state.get(a["email"], {})
            return (0 if st.get("state") == "good" else (2 if st.get("state") == "bad" else 1))
        gmails.sort(key=_rank)
        self.candidates = [Account(i, a["email"], a["password"], a["totp"]) for i, a in enumerate(gmails)]
        good = sum(1 for a in gmails if self._state.get(a["email"], {}).get("state") == "good")
        bad = sum(1 for a in gmails if self._state.get(a["email"], {}).get("state") == "bad")
        self.log(f"pool ẢNH browser: {len(self.candidates)} ứng viên (đã biết GOOD={good} BAD={bad}), "
                 f"{self.n_slots} slot, login đồng thời {LOGIN_CONCURRENCY}, model {IMG_MODELS}")
        self._running = True
        for s in range(self.n_slots):
            t = threading.Thread(target=self._slot_worker, args=(s,), daemon=True)
            t.start(); self._slots.append(t)
        threading.Thread(target=self._chrome_sweeper, daemon=True).start()   # tự dọn chrome zombie/orphan
        return True

    def _chrome_sweeper(self):
        """Định kỳ REAP chrome ZOMBIE (đã chết, không ExecutablePath) + chrome pool MỒ CÔI (profile pool nhưng
        port KHÔNG thuộc slot đang chạy) -> tránh chrome rác tích tụ làm máy nặng dần. An toàn: KHÔNG đụng Chrome
        cá nhân (có ExecutablePath + không profile pool + port active được giữ)."""
        ports = "|".join(str(9500 + i) for i in range(self.n_slots))
        while self._running:
            time.sleep(90)
            try:
                cond = ("(-not $_.ExecutablePath) -or ($_.CommandLine -like '*pool_img_profiles*' -and "
                        f"$_.CommandLine -notmatch 'remote-debugging-port=({ports})(\\s|$|\")')")
                subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                                f"Where-Object {{ {cond} }} | "
                                "ForEach-Object { taskkill /F /T /PID $_.ProcessId 2>$null }"],
                               capture_output=True, timeout=40, creationflags=0x08000000)
            except Exception:
                pass

    def stop(self):
        self._running = False
        for a in list(self.active.values()):
            try: a.close()
            except Exception: pass

    # ---------- 1 SLOT: bốc account -> chuẩn bị -> sản xuất tới khi account hỏng -> bốc account khác ----------
    def _slot_worker(self, slot):
        while self._running:
            a = self._next_candidate()
            if a is None:
                time.sleep(5); continue
            try:
                a.idx = slot                      # port/IPv6 theo slot (cố định số lượng)
                ok = a.prepare(self._login, self.login_sem, self.log)
                if not ok:
                    reason = "chưa login (reuse cookie)" if a.state == "nologin" else "login/warm fail"
                    self._mark(a.email, state=a.state, reason=reason)
                    a.close(); continue
                self._mark(a.email, state="ready", project=a.project, cookie="alive")
                # SẢN XUẤT tới khi account BẮT ĐẦU FAIL (reCAPTCHA lì) — KHAI THÁC TỐI ĐA, KHÔNG xoay theo số lượng
                while self._running and a.state == "ready":
                    try:
                        job = self.q.get(timeout=3)
                    except queue.Empty:
                        continue
                    if job.get("_cycles", 0) > JOB_MAX_CYCLES:
                        msg = ("quota reCAPTCHA ẢNH đang cạn (Google-side) - bỏ lượt này, chạy lại sau sẽ tự làm tiếp"
                               if self.quota_blocked() else "quá nhiều lượt - bỏ ảnh")
                        job["_result"] = (False, {}, msg); job["_event"].set(); continue
                    job["_cycles"] = job.get("_cycles", 0) + 1
                    a.busy = True
                    try:
                        outcome = self._process(a, job)
                    except Exception as e:
                        outcome = ("fail", f"exception: {e}")
                    a.busy = False
                    if outcome == "success":
                        a.wins += 1; a.swaps = 0   # ra ảnh -> reset swap counter (khai thác tiếp, không đếm để nghỉ)
                        if not a.good:
                            # LƯU wins vào state -> NHỚ account từng ra ảnh (qua cả restart) để ưu tiên bốc trước (winner roster)
                            a.good = True; self._mark(a.email, state="good", project=a.project, cookie="alive", wins=a.wins)
                        with self._clock: self.total_done += 1
                        _r = job.get("_result") or (None, {}, "")
                        _info = _r[1] if len(_r) > 1 else {}
                        self.log(f"🖼️ RA ẢNH: {a.email} [{os.path.basename(job.get('out_path','?'))}] "
                                 f"{_info.get('bytes','?')} bytes model={_info.get('model','?')} "
                                 f"(tổng {self.total_done} ảnh)")
                        job["_event"].set()
                        # KHAI THÁC TỐI ĐA: account tốt ra ảnh LIÊN TỤC, KHÔNG đổi/nghỉ theo số lượng. Chỉ khi account
                        # BẮT ĐẦU FAIL (reCAPTCHA lì -> swap) và lì đủ SWAP_GIVEUP lượt mới cách ly -> xoay account khác.
                    elif outcome == "retry_soft":
                        a.fails += 1; self.q.put(job)          # trả job về hàng đợi (account/slot khác lo)
                    elif isinstance(outcome, tuple) and outcome[0] == "reauth":
                        self.q.put(job)                         # cần login lại -> break để prepare lại
                        a.state = "new"
                    elif isinstance(outcome, tuple) and outcome[0] == "swap":
                        # reCAPTCHA lì -> ĐỔI ACCOUNT. Account bị đốt (swap NHIỀU lần liên tiếp) -> CÁCH LY (bad+cooldown)
                        # để pool DÙNG account TƯƠI (còn ~150 acc) thay vì phí slot churn mãi acc đã đốt. 1 ảnh -> reset.
                        self.q.put(job)                         # job -> account khác làm ngay
                        a.last_swap = time.time()
                        a.swaps = getattr(a, "swaps", 0) + 1
                        a.state = "new"                         # break produce-loop -> slot bốc account khác
                        if a.swaps >= SWAP_GIVEUP:
                            # 403 KHÔNG = account hỏng (vẫn login OK ở Flow). Account ĐÃ ra ảnh (winner "ấm") -> nghỉ NGẮN
                            # (GOOD_REST) rồi quay lại khai thác tiếp; account CHƯA ra ảnh (lạnh) -> nghỉ dài (RECAPTCHA_REST).
                            _rest = GOOD_REST if (a.good or a.wins > 0) else RECAPTCHA_REST
                            a.state = "resting"
                            self._mark(a.email, state="resting", until=int(time.time()) + _rest,
                                       reason=f"reCAPTCHA lì {a.swaps} lượt - nghỉ {'ngắn(winner)' if _rest==GOOD_REST else 'dài(lạnh)'}")
                            self.log(f"😴 {a.email}: reCAPTCHA lì {a.swaps} lượt -> nghỉ {_rest}s ({'winner' if _rest==GOOD_REST else 'lạnh'}), dùng account tươi")
                        else:
                            self._mark(a.email, state="ready", reason="recaptcha - doi luot")
                            self.log(f"↻ {a.email}: reCAPTCHA lì (lượt {a.swaps}) -> đổi account khác")
                    elif isinstance(outcome, tuple) and outcome[0] == "bad":
                        self.q.put(job)                         # 403/unusual -> nghỉ ngắn (account vẫn login OK), job sang account khác
                        a.state = "resting"; self._mark(a.email, state="resting", reason="flagged (403/unusual) - nghỉ ngắn")
                        self.log(f"😴 {a.email}: 403/unusual -> nghỉ ngắn ({RECAPTCHA_REST}s), slot bốc account khác")
                    else:
                        if "_result" not in job:
                            job["_result"] = (False, {}, outcome[1] if isinstance(outcome, tuple) else str(outcome))
                        with self._clock: self.total_fail += 1
                        job["_event"].set()
            finally:
                a.close(); self._release_candidate(a)

    def _process(self, a, job):
        """1 job: retry (in-page token mới mỗi lần) + xoay model. Trả success/retry_soft/(bad|reauth|fail|swap).
        Khi QUOTA reCAPTCHA ẢNH cạn (toàn cục): KHÔNG storm — chỉ 1 job probe mỗi QUOTA_PROBE_INTERVAL, mỗi
        probe vài token; job khác nghỉ (retry_soft) để KHỎI đốt thêm quota (giúp Google mở lại nhanh hơn)."""
        model = a.pick_model()
        if not model:
            return "retry_soft"                 # mọi model đang nghỉ quota
        # --- Circuit-breaker: nếu đang coi quota cạn, chỉ cho 1 probe/khoảng; job khác nghỉ ---
        if self.quota_blocked():
            with self._clock:
                now = time.time()
                if now < self._quota_blocked_until:
                    # chưa tới hạn probe kế -> job này nghỉ (khỏi đốt quota)
                    do_probe = False
                else:
                    do_probe = True
                    self._quota_blocked_until = now + QUOTA_PROBE_INTERVAL   # đặt lịch probe kế
            if not do_probe:
                time.sleep(2.0)
                return "retry_soft"             # job quay lại hàng đợi, không đốt token
            attempts = QUOTA_PROBE_ATTEMPTS
        else:
            attempts = GEN_ATTEMPTS
        seed = job.get("seed") or (int(time.time() * 1000) % 900000)
        aspect = job.get("aspect") or "IMAGE_ASPECT_RATIO_LANDSCAPE"
        image_inputs = job.get("image_inputs") or []
        nref = len(image_inputs)
        tag = os.path.basename(job.get("out_path", "?"))
        self.log(f"🎨 {a.email} tạo [{tag}] model={model} refs={nref} (job cycle {job.get('_cycles',1)})")
        # GIÃN NHỊP: đảm bảo tối thiểu IMG_PACING giây kể từ lần tạo trước trên CÙNG account (chống UNUSUAL_ACTIVITY)
        _since = time.time() - getattr(a, "last_gen_ts", 0.0)
        if IMG_PACING > 0 and _since < IMG_PACING:
            time.sleep(IMG_PACING - _since)
        clear_rounds = 0
        while True:
            retry_seen = 0
            for attempt in range(attempts):
                a.last_gen_ts = time.time()
                kind, data = a.generate_inpage(job["prompt"], model, aspect, seed, image_inputs)
                if kind == "ok":
                    a.model_wins[model] = a.model_wins.get(model, 0) + 1
                    op = job["out_path"]
                    try:
                        if os.path.dirname(op): os.makedirs(os.path.dirname(op), exist_ok=True)
                        if data.get("fife"):
                            n = fc.download_image_url(data["fife"], op)
                        else:
                            import base64; b = base64.b64decode(data["b64"]); open(op, "wb").write(b); n = len(b)
                        job["_result"] = (True, {"media_name": data.get("name"), "bytes": n, "seed": data.get("seed"),
                                                 "account": a.email, "model": model,
                                                 "egress": "ipv6" if a.ipv6 else "ipmay"}, "")
                        self._note_gen(True)        # ra ảnh -> reset circuit-breaker (quota đã mở/còn)
                        return "success"
                    except Exception:
                        time.sleep(1); continue
                elif kind == "auth":
                    self.log(f"🔑 {a.email} [{tag}]: session 401 (bearer chết) -> login lại")
                    return ("reauth", "session 401")
                elif kind == "quota":
                    # quota MODEL that su (RESOURCE_EXHAUSTED khong kem reCAPTCHA) -> DOI MODEL (Pro->Nano2->Lite)
                    a.last_kind = "quota"; a.model_rest[model] = time.time() + MODEL_REST_SECS
                    nxt = a.pick_model()
                    if nxt and nxt != model:
                        self.log(f"📊 {a.email} {model} hết quota -> đổi {nxt}"); model = nxt; continue
                    self.log(f"📊 {a.email} [{tag}]: hết quota MỌI model -> job sang account khác")
                    return "retry_soft"                # het quota moi model -> job sang account khac
                elif kind == "recaptcha":
                    # reCAPTCHA cham diem thap -> RETRY TOKEN MOI (kien nhan nhu video, "retry chan moi duoc").
                    a.last_kind = "recaptcha"
                    if attempt == 0 or attempt == attempts - 1:
                        self.log(f"🔁 {a.email} [{tag}]: reCAPTCHA (lần {attempt+1}/{attempts}) {(data.get('body') or '')[:70]}")
                    time.sleep(0.6 + random.uniform(0, 0.6))
                else:
                    # hang/loi/khong ket qua -> retry; nhieu lan (page ket) -> re-prepare account
                    retry_seen += 1
                    self.log(f"⚠️ {a.email} [{tag}]: lỗi khác (kind={kind}) status={data.get('status')} {(data.get('body') or data.get('err') or '')[:90]}")
                    if retry_seen >= 2:   # treo/timeout 2 lần (mỗi lần tới 3') -> bỏ account, re-prepare (chặn giữ slot quá lâu)
                        return ("reauth", "hang/loi page")
                    time.sleep(0.8)
            # hết attempts mà reCAPTCHA vẫn lì -> CLEAR-ON-403 (xóa state + reload -> gieo lại điểm) rồi thử tiếp
            if CLEAR_ON_403 and a.last_kind == "recaptcha" and clear_rounds < CLEAR_RETRIES and not self.quota_blocked():
                clear_rounds += 1
                self.log(f"🧹 {a.email} [{tag}]: 403 -> xóa data+reload gieo lại điểm (vòng {clear_rounds}/{CLEAR_RETRIES})")
                if not a._clear_captcha_data():
                    return ("reauth", "mất login sau clear")
                continue   # thử lại attempts sau khi clear
            break
        # hết cả clear-retry mà vẫn lì. Ghi nhận (quá ngưỡng -> coi quota ẢNH cạn toàn cục -> ngừng đốt).
        self._note_gen(False)
        if self.quota_blocked():
            return "retry_soft"
        return ("swap", f"reCAPTCHA lì (sau {clear_rounds} vòng clear)")

    def submit(self, prompt, out_path, aspect=None, seed=None, image_inputs=None, timeout=900):
        job = {"prompt": prompt, "out_path": out_path, "aspect": aspect, "seed": seed,
               "image_inputs": image_inputs, "_event": threading.Event()}
        self.q.put(job)
        if not job["_event"].wait(timeout=timeout):
            return False, {}, "image pool: timeout (nhà máy đang chuẩn bị account?) - thử lại lượt sau"
        return job.get("_result", (False, {}, "no result"))

    def health(self):
        up = time.time() - self.started_ts
        act = list(self.active.values())
        good = sum(1 for v in self._state.values() if v.get("state") == "good")
        bad = sum(1 for v in self._state.values() if v.get("state") == "bad")
        return {
            "slots": self.n_slots, "candidates": len(self.candidates),
            "known_good": good, "known_bad": bad,
            "active": [{"email": a.email, "state": a.state, "good": a.good, "busy": a.busy,
                        "wins": a.wins, "fails": a.fails, "model_wins": a.model_wins} for a in act],
            "models": IMG_MODELS, "queue": self.q.qsize(), "done": self.total_done, "fail": self.total_fail,
            "image_per_hour": round(self.total_done / up * 3600, 1) if up > 5 else 0, "uptime_s": round(up),
            "quota_blocked": self.quota_blocked(),
            "quota_block_remaining_s": max(0, round(self._quota_blocked_until - time.time())),
            "consec_recaptcha": self._consec_recaptcha,
        }


_FACTORY = None

_CONN_ERRS = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError)

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def handle_one_request(self):
        # ve3 client hay TIMEOUT rồi ngắt kết nối khi ảnh tạo lâu (churn reCAPTCHA) -> nuốt lỗi socket,
        # KHÔNG để spam traceback + KHÔNG crash thread handler.
        try:
            super().handle_one_request()
        except _CONN_ERRS:
            self.close_connection = True
        except Exception:
            self.close_connection = True
    def _send(self, code, obj):
        try:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        except _CONN_ERRS:
            self.close_connection = True   # client đã ngắt -> bỏ qua
    def do_GET(self):
        if self.path == "/health": self._send(200, _FACTORY.health())
        else: self._send(404, {"error": "nf"})
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        except Exception:
            self._send(400, {"error": "bad json"}); return
        if self.path == "/shutdown":
            self._send(200, {"ok": True}); threading.Thread(target=_FACTORY.stop, daemon=True).start(); return
        if self.path == "/generate_image":
            if not data.get("out_path"):
                self._send(400, {"error": "thieu out_path"}); return
            ok, info, err = _FACTORY.submit(data.get("prompt"), data["out_path"], aspect=data.get("aspect"),
                                            seed=data.get("seed"), image_inputs=data.get("image_inputs"))
            self._send(200, {"success": bool(ok), "info": info, "error": err}); return
        self._send(404, {"error": "nf"})


def main():
    global _FACTORY
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8789)
    ap.add_argument("--accounts", type=int, default=N_SLOTS)
    args = ap.parse_args()
    _FACTORY = ImagePoolBrowser(n_slots=args.accounts)
    _FACTORY.start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    _log(f"HTTP ảnh browser nghe 127.0.0.1:{args.port} (POST /generate_image)")
    try:
        srv.serve_forever()
    finally:
        _FACTORY.stop()


if __name__ == "__main__":
    main()
