"""
token_manager.py
================
Kết nối Chrome Portable ĐANG CHẠY (với --remote-debugging-port=9333)
→ gọi Clerk.session.getToken() trước mỗi API request.

Clerk token valid ~60s → phải refresh trước mỗi lần gọi API.

Setup (1 lần):
  1. Chạy start_chrome_suno.bat (mở Chrome Portable)
  2. Đăng nhập Suno nếu cần
  3. Chạy suno_worker.py bình thường — TokenManager tự connect
"""
import time
import subprocess
import logging
from typing import Optional
from pathlib import Path

log = logging.getLogger("token_manager")

DEBUG_PORT  = 9444          # Dedicated Suno browser debug port
SUNO_URL    = "https://suno.com/create"
TOKEN_TTL   = 50            # Refresh token trước khi hết 60s
CHROME_LAUNCHER = Path(__file__).parent / "start_chrome_suno.bat"


class TokenManager:
    """
    Quản lý Clerk JWT token — connect Chrome Portable đang mở,
    gọi getToken() mỗi khi token sắp hết hạn.
    """

    def __init__(self, port: int = DEBUG_PORT, auto_launch: bool = True):
        self._port  = port
        self._auto_launch = auto_launch
        self._page  = None
        self._token: Optional[str] = None
        self._token_fetched_at: float = 0.0

    # ─────────────────────────────────────────────────────────────────
    def start(self) -> bool:
        """
        Connect tới Chrome Portable đang chạy trên port.
        Chrome phải đã được mở bằng start_chrome_suno.bat.
        """
        try:
            from DrissionPage import ChromiumPage
        except ImportError:
            log.error("[TM] DrissionPage not installed")
            return False

        log.info(f"[TM] Connecting to Chrome on port {self._port}...")
        for attempt in range(3):
            try:
                self._page = ChromiumPage(self._port)
                log.info(f"[TM] Connected. URL: {self._page.url}")
                break
            except Exception as e:
                try:
                    self._page = ChromiumPage(f"127.0.0.1:{self._port}")
                    log.info(f"[TM] Connected (alt). URL: {self._page.url}")
                    break
                except Exception:
                    log.debug(f"[TM] Connect attempt {attempt+1} failed: {e}")
                    if attempt == 0 and self._auto_launch and CHROME_LAUNCHER.exists():
                        try:
                            log.info(f"[TM] Launching preferred Suno browser: {CHROME_LAUNCHER}")
                            subprocess.Popen(["cmd", "/c", str(CHROME_LAUNCHER)])
                            time.sleep(8)
                        except Exception as launch_err:
                            log.warning(f"[TM] Cannot launch preferred browser: {launch_err}")
                    else:
                        time.sleep(2)
        else:
            log.error(f"[TM] Cannot connect Chrome on port {self._port}.")
            log.error(f"[TM] Run: start_chrome_suno.bat  then retry.")
            return False

        # Đảm bảo đang ở trang Suno
        if "suno.com" not in (self._page.url or ""):
            log.info(f"[TM] Navigating to {SUNO_URL}...")
            self._page.get(SUNO_URL)

        # Poll Clerk.session sẵn sàng (max 30s)
        log.info("[TM] Polling for Clerk.session...")
        for attempt in range(10):   # 10 x 3s = 30s
            time.sleep(3)
            tok = self._call_get_token()
            if tok:
                self._token = tok
                self._token_fetched_at = time.time()
                log.info(f"[TM] Ready. Token: {tok[:30]}...")
                return True
            log.debug(f"[TM] try {attempt+1}: clerk not ready")

        log.error("[TM] Clerk.session not ready — is user logged in?")
        return False

    # ─────────────────────────────────────────────────────────────────
    def _call_get_token(self) -> Optional[str]:
        """Gọi window.Clerk.session.getToken() trên page đang mở."""
        if not self._page:
            return None
        try:
            result = self._page.run_js("""
                return new Promise((resolve, reject) => {
                    if (!window.Clerk)         { reject('no_clerk'); return; }
                    if (!window.Clerk.session) { reject('no_session'); return; }
                    window.Clerk.session.getToken({skipCache: true})
                        .then(t  => resolve(t))
                        .catch(e => reject(String(e)));
                });
            """, as_expr=False)
            if result and isinstance(result, str) and len(result) > 50:
                return result.strip()
        except Exception as e:
            err = str(e)
            if 'no_clerk' not in err and 'no_session' not in err:
                log.debug(f"[TM] getToken error: {err[:80]}")
        return None

    # ─────────────────────────────────────────────────────────────────
    def get_token(self) -> Optional[str]:
        """
        Trả về token fresh.
        Luôn thử lấy token mới từ Clerk trước mỗi request;
        nếu không lấy được mới fallback sang token cache.
        """
        age = time.time() - self._token_fetched_at
        log.debug(f"[TM] Token age {age:.0f}s — requesting fresh token...")

        tok = self._call_get_token()
        if tok:
            self._token = tok
            self._token_fetched_at = time.time()
            log.debug(f"[TM] Refreshed: {tok[:20]}...")
            return self._token

        if self._token:
            log.warning("[TM] Live token refresh failed, using cached token")
        else:
            log.warning("[TM] Live token refresh failed and no cached token available")
        return self._token

    # ─────────────────────────────────────────────────────────────────
    def stop(self):
        """Disconnect (KHÔNG đóng Chrome — để session còn sống)."""
        self._page = None
        log.info("[TM] Disconnected (Chrome stays open)")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()
