"""
account_manager — VÒNG ĐỜI AUTH cho farm pool (video + ảnh), TỰ CHỮA, KHÔNG gãy tool, KHÔNG chặn tiến độ.

Thang tự chữa (rẻ -> đắt):
  1) cache còn hạn            -> dùng luôn
  2) refresh bearer TỪ COOKIE -> nhanh, không mở chrome
  3) warm_extract            -> mở profile (đã login) -> load Flow (WARM) -> chờ project -> lấy cookie+bearer
  4) full_recover            -> clear lock + LOGIN password (DrissionPage, reuse tool ve3) + WARM + lấy cookie
  5) park                    -> account chịu thua tạm 30' + báo; job requeue sang account SỐNG

Nguyên tắc:
- COPY/ISOLATE: có bước login/warm THAM KHẢO flowkit nhưng chạy chrome RIÊNG (port riêng, KHÔNG dùng
  extension agent) -> không dẫm chân backend extension.
- CHỐNG DẪM CHÂN: (a) file-lock cross-process theo copyN (video & image factory là 2 process khác nhau
  không mở cùng 1 profile); (b) nếu 1 chrome KHÁC (extension) đang giữ profile -> SKIP, thử lại sau.
- KHÔNG CHẶN: recovery chạy ở THREAD NỀN (RecoveryManager), mỗi lúc chỉ 1 login chrome. Worker gặp 401 chỉ
  'flag + park + requeue', không tự mở chrome -> account/luồng khác chạy tiếp, tool không khựng.
- VALIDATE: bearer 'fresh' vẫn có thể 401 (cookie chết) -> validate bằng 1 POST thật (401 = chết).
"""
import os, sys, time, threading, queue, subprocess
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import flow_client as fc

SUITE = os.path.dirname(_HERE)   # D:\VE3_SUITE


# ---------------- helpers dùng chung ----------------
def chrome_paths(chrome_path):
    """(chrome_exe, profile_dir) từ chrome_path portable. chrome_path có thể là ...\\GoogleChromePortable.exe."""
    cp = str(chrome_path or "")
    if not cp:
        return None, None
    root = os.path.dirname(cp) if cp.lower().endswith(".exe") else cp
    exe = os.path.join(root, "App", "Chrome-bin", "chrome.exe")
    prof = os.path.join(root, "Data", "profile")
    if os.path.exists(exe) and os.path.isdir(prof):
        return exe, prof
    return None, None


def validate_auth(auth, timeout=30):
    """POST video T2V với token GIẢ: 401 = bearer CHẾT; khác (403/400/429) = bearer SỐNG (chưa tới recaptcha).
    Rẻ, chắc chắn — dùng để biết account thật sự dùng được không (đừng tin bearer 'fresh')."""
    try:
        if not (auth and auth.get("bearer") and auth.get("project")):
            return False
        payload = fc.build_payload("x", auth["project"], "DUMMY", 1, aspect="VIDEO_ASPECT_RATIO_LANDSCAPE")
        r = fc._post(fc.GEN_T2V, fc._headers(auth["bearer"]), __import__("json").dumps(payload),
                     timeout=timeout, proxy=None)
        return r.status_code != 401
    except Exception:
        return True   # lỗi mạng -> KHÔNG kết luận chết (tránh recover oan), coi như sống


def _profile_in_use(profile_dir):
    """True nếu 1 chrome KHÁC đang chạy trên profile này (vd extension backend) -> đừng mở đè (dẫm chân)."""
    try:
        pd = os.path.normcase(os.path.abspath(profile_dir))
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=15, creationflags=0x08000000)
        for line in (out.stdout or "").splitlines():
            if pd in os.path.normcase(line):
                return True
    except Exception:
        pass
    return False


class _CopyLock:
    """File-lock cross-process theo copyN: video & image factory (2 process) KHÔNG mở cùng 1 profile."""
    def __init__(self, copyn):
        self.path = os.path.join(SUITE, f".veo3top_authlock_{copyn}.lock")
        self.fd = None

    def acquire(self, stale=300):
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(self.fd, str(os.getpid()).encode()); return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(self.path) > stale:
                    os.remove(self.path)
                    self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                    os.write(self.fd, str(os.getpid()).encode()); return True
            except Exception:
                pass
            return False
        except Exception:
            return False

    def release(self):
        try:
            if self.fd is not None:
                os.close(self.fd)
            os.remove(self.path)
        except Exception:
            pass
        self.fd = None


def _clear_singleton(profile_dir):
    for lk in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            os.remove(os.path.join(profile_dir, lk))
        except Exception:
            pass


def _kill_profile_chromes(profile_dir):
    """Kill mọi chrome.exe đang mở profile này (vd chrome login sót lại — login_google_chrome nhánh
    'Already logged in' KHÔNG đóng chrome). Chỉ gọi khi ĐÃ giữ CopyLock + đã xác nhận không phải chrome khác."""
    try:
        pd = os.path.normcase(os.path.abspath(profile_dir))
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             "Select-Object ProcessId,CommandLine | ConvertTo-Csv -NoTypeInformation"],
            capture_output=True, text=True, timeout=15, creationflags=0x08000000)
        for line in (out.stdout or "").splitlines():
            if pd in os.path.normcase(line):
                pid = line.strip().strip('"').split('"')[0].split(",")[0]
                if pid.isdigit():
                    subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                                   capture_output=True, creationflags=0x08000000)
    except Exception:
        pass


def warm_extract(email, chrome_exe, profile_dir, cache, port, log=lambda *_: None, check_busy=True):
    """WARM + LẤY COOKIE: mở chrome profile (đã login) -> load Flow -> wait_ready (script/grecaptcha load =
    Flow warm) -> CHỜ project (như chờ 'Du an moi') -> lấy bearer+cookie+project -> lưu cache. None nếu fail.
    check_busy=True: KHÔNG mở nếu profile đang bị chrome khác dùng (tránh dẫm chân). full_recover gọi với
    check_busy=False vì đã giữ CopyLock + tự kill chrome login sót của chính nó."""
    try:
        from cdp_chrome import ChromeCDP
    except Exception:
        return None
    if check_busy and _profile_in_use(profile_dir):
        log(f"[auth {email}] profile đang được chrome khác dùng -> hoãn warm (tránh dẫm chân)")
        return None
    _clear_singleton(profile_dir)
    c = ChromeCDP(chrome_exe, profile_dir, port, offscreen=True, log=lambda *_: None)
    try:
        if not c.connect() or not c.wait_ready(35):
            return None
        bearer = c.bearer()
        if not bearer:
            return None
        cookie = fc.labs_cookie_header(c.cookies())
        project = None
        for _ in range(10):                # chờ project xuất hiện (warm) — như tool chờ 'Du an moi'
            project = c.first_project_id()
            if project:
                break
            time.sleep(2)
        if not project:
            return None                    # chưa tạo được project -> chưa tạo được ảnh/video
        d = {"bearer": bearer, "cookie": cookie, "project": project, "email": email, "ts": time.time()}
        try: cache._save(email, d)
        except Exception: pass
        return d
    except Exception:
        return None
    finally:
        try: c.close(kill=True)
        except Exception: pass


def full_recover(email, bundle, chrome_path, cache, copyn, name="", login_worker_id=None, warm_port=None,
                 log=lambda *_: None):
    """QUY TRÌNH CHỮA CỨNG khi profile hết đăng nhập: clear lock -> LOGIN password (reuse ve3 google_login,
    tự điền email/pass/2FA + warm 'Create with Google Flow') -> warm_extract lấy cookie. None nếu chịu thua.
    Có file-lock cross-process theo copyN (video/image không mở trùng)."""
    parts = [p.strip() for p in str(bundle or "").split("|")]
    ce, pf = chrome_paths(chrome_path)
    if len(parts) < 2 or not parts[0] or not parts[1] or not (ce and pf):
        return None
    account_info = {"id": parts[0], "password": parts[1], "totp_secret": parts[2] if len(parts) >= 3 else ""}
    wid = login_worker_id if login_worker_id is not None else (140 + copyn)
    wport = warm_port if warm_port is not None else (9880 + copyn)
    lock = _CopyLock(copyn)
    if not lock.acquire():
        log(f"[auth {email}] account khác/process khác đang chữa (copy {copyn}) -> hoãn")
        return None
    try:
        if _profile_in_use(pf):
            log(f"[auth {email}] profile đang bị chrome khác dùng -> hoãn recover")
            return None
        _clear_singleton(pf)
        # LOGIN password (mở chrome riêng, port riêng) — reuse hàm ve3 (chạy chrome độc lập, không dùng chung)
        ok = False
        try:
            ve3 = os.path.join(SUITE, "tools", "ve3")
            if ve3 not in sys.path:
                sys.path.insert(0, ve3)
            from google_login import login_google_chrome
            ok = login_google_chrome(account_info, chrome_portable=chrome_path, profile_dir=pf,
                                     worker_id=wid, proxy_arg="")
        except Exception as e:
            log(f"[auth {email}] login lỗi: {e}")
            ok = False
        if not ok:
            return None
        # login_google_chrome nhánh 'Already logged in' KHÔNG đóng chrome -> kill chrome login sót của CHÍNH ta
        # (đang giữ CopyLock + đã xác nhận đầu hàm không phải chrome khác) để warm mở lại profile được.
        _kill_profile_chromes(pf)
        time.sleep(3)
        _clear_singleton(pf)
        return warm_extract(email, ce, pf, cache, wport, log=log, check_busy=False)
    finally:
        lock.release()


# ---------------- RecoveryManager: chữa NỀN, không chặn worker ----------------
class RecoveryManager:
    """1 thread nền chữa account lỗi, MỖI LÚC 1 chrome login (không đồng loạt). Worker chỉ 'submit + park',
    không tự mở chrome -> tool không khựng, account/luồng khác chạy tiếp."""
    def __init__(self, log=lambda *_: None):
        self.log = log
        self.q = queue.Queue()
        self._pending = set()
        self._lock = threading.Lock()
        self._stop = False
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def submit(self, key, recover_fn):
        """Đưa 1 account vào hàng chờ chữa (dedup theo key). recover_fn: callable trả dict auth hoặc None."""
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
        self.q.put((key, recover_fn))

    def _loop(self):
        while not self._stop:
            try:
                key, fn = self.q.get(timeout=2)
            except queue.Empty:
                continue
            try:
                fn()
            except Exception as e:
                self.log(f"[recovery] {key} lỗi: {e}")
            finally:
                with self._lock:
                    self._pending.discard(key)

    def stop(self):
        self._stop = True
