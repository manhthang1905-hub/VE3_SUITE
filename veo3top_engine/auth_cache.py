"""
AuthCache — cache bearer + cookie THEO TỪNG ACCOUNT cho cách B.
- Lưu ra file json (cfg/veo3top_auth/<account>.json).
- Tự lấy lần đầu (mở chrome account chốc lát → bearer + cookie → lưu → đóng).
- Tự refresh khi hết hạn / khi caller báo 401 (bearer) hoặc download fail (cookie). => tự fix.
Thread-safe (nhiều mã chạy song song).
"""
import os, json, time, threading, re
import sys
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import flow_client as fc
from cdp_chrome import ChromeCDP

BEARER_TTL = 2400          # 40 phút (token Google ~1h, refresh sớm cho chắc)
_AUTH_DIR_DEFAULT = os.path.join(_HERE, "_auth_cache")


def _safe(name):
    return re.sub(r"[^a-zA-Z0-9._-]", "_", str(name or "unknown"))


class AuthCache:
    def __init__(self, store_dir=None, log=lambda *_: None):
        self.dir = store_dir or _AUTH_DIR_DEFAULT
        os.makedirs(self.dir, exist_ok=True)
        self.log = log
        self._mem = {}                  # account -> {bearer,cookie,project,ts}
        self._locks = {}
        self._global = threading.Lock()

    def _lock_for(self, account):
        with self._global:
            if account not in self._locks:
                self._locks[account] = threading.Lock()
            return self._locks[account]

    def _path(self, account):
        return os.path.join(self.dir, _safe(account) + ".json")

    def _load(self, account):
        if account in self._mem:
            return self._mem[account]
        p = self._path(account)
        if os.path.exists(p):
            try:
                d = json.load(open(p, encoding="utf-8"))
                self._mem[account] = d
                return d
            except Exception:
                return None
        return None

    def _save(self, account, d):
        self._mem[account] = d
        try:
            json.dump(d, open(self._path(account), "w", encoding="utf-8"))
        except Exception:
            pass

    def _fresh(self, d):
        return d and d.get("bearer") and (time.time() - d.get("ts", 0) < BEARER_TTL)

    def _acquire(self, account, chrome_exe, profile_dir, port):
        """Mở chrome account chốc lát: lấy bearer + cookie + project, lưu, đóng."""
        self.log(f"[authcache] acquire {account} (chrome port {port})...")
        c = ChromeCDP(chrome_exe, profile_dir, port, offscreen=True, log=self.log)
        try:
            if not c.connect() or not c.wait_ready(30):
                self.log(f"[authcache] {account}: chrome/grecaptcha fail")
                return None
            bearer = c.bearer()
            cookie = fc.labs_cookie_header(c.cookies())
            project = c.first_project_id()
            if not bearer:
                return None
            d = {"bearer": bearer, "cookie": cookie, "project": project,
                 "email": c.email(), "ts": time.time()}
            self._save(account, d)
            return d
        finally:
            c.close(kill=True)   # đóng chrome account sau khi lấy xong (cách B không giữ mở)

    def _refresh_from_cookie(self, account, d):
        """Refresh bearer TỪ cookie (KHÔNG mở chrome) — giống veo3top. Trả dict mới hoặc None."""
        if not d or not d.get("cookie"):
            return None
        bearer, email = fc.bearer_from_cookie(d["cookie"])
        if not bearer:
            return None
        d = dict(d)
        d["bearer"] = bearer
        d["ts"] = time.time()
        if email:
            d["email"] = email
        self._save(account, d)
        self.log(f"[authcache] {account}: refresh bearer TỪ COOKIE (không mở chrome)")
        return d

    def ensure(self, account, chrome_exe, profile_dir, port, force=False):
        """Trả dict {bearer,cookie,project,...} còn hạn; tự lấy nếu cần.
        Ưu tiên refresh bearer TỪ COOKIE (không mở chrome) -> chỉ mở chrome khi cookie cũng hết hạn."""
        lock = self._lock_for(account)
        with lock:
            d = self._load(account)
            if not force and self._fresh(d):
                return d
            # bearer hết hạn/401 nhưng còn cookie -> thử refresh từ cookie trước (nhanh, không mở chrome)
            if d and d.get("cookie") and d.get("project"):
                nd = self._refresh_from_cookie(account, d)
                if nd:
                    return nd
            # cookie cũng hết hạn / chưa có -> mở chrome account lấy lại (fallback)
            return self._acquire(account, chrome_exe, profile_dir, port)

    def invalidate(self, account):
        """Bearer hỏng (401) -> GIỮ cookie, chỉ bỏ bearer để lần sau refresh TỪ COOKIE (không mở chrome).
        Chỉ xoá hẳn khi không có cookie."""
        with self._lock_for(account):
            d = self._mem.get(account) or self._load(account)
            if d and d.get("cookie"):
                d = dict(d); d["bearer"] = None; d["ts"] = 0
                self._save(account, d)
            else:
                self._mem.pop(account, None)
                try:
                    os.remove(self._path(account))
                except Exception:
                    pass
