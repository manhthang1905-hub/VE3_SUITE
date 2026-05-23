"""
token_extractor.py — DrissionPage + Network Interceptor
Mở Chrome (profile đã login Suno) → tự capture bearer token.
User KHÔNG cần làm gì sau khi chrome mở (tự navigate đến suno.com).
"""
import time
import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger("token_extractor")

# Token cache file (tránh phải mở browser mỗi lần)
TOKEN_CACHE = Path(__file__).parent / ".suno_token.txt"
TOKEN_TTL   = 3600   # seconds — token hết hạn sau 1 tiếng

SUNO_URL = "https://suno.com"
API_TRIGGER_URL = "https://suno.com/create"   # Trang nào có API call
API_HOST = "studio-api-prod.suno.com"


def _load_cached_token() -> Optional[str]:
    """Đọc token từ cache nếu còn valid (< TTL cũ)."""
    if not TOKEN_CACHE.exists():
        return None
    try:
        mtime = TOKEN_CACHE.stat().st_mtime
        if time.time() - mtime > TOKEN_TTL:
            log.info("[CACHE] Token expired")
            return None
        token = TOKEN_CACHE.read_text("utf-8").strip()
        if token and len(token) > 20:
            log.info(f"[CACHE] Loaded token: {token[:20]}...")
            return token
    except Exception as e:
        log.warning(f"[CACHE] Read error: {e}")
    return None


def _save_token(token: str) -> None:
    try:
        TOKEN_CACHE.write_text(token.strip(), encoding="utf-8")
    except Exception as e:
        log.warning(f"[CACHE] Write error: {e}")


def get_bearer_token(
    chrome_profile: Optional[str] = None,
    headless: bool = False,
    timeout: int = 60,
    force_refresh: bool = False,
) -> Optional[str]:
    """
    Lấy Bearer token từ Chrome đã login Suno.

    Strategy:
    1. Check cache (< 1 hour) → return ngay
    2. Mở Chrome (DrissionPage) với profile đã đăng nhập
    3. Navigate đến suno.com/create
    4. Interceptor bắt request có Authorization header
    5. Extract + cache + return token

    Args:
        chrome_profile: Path đến Chrome profile dir (đã login Suno)
        headless: True = Chrome ẩn
        timeout:  Giây chờ tối đa để bắt token
        force_refresh: True = bỏ cache, lấy token mới
    Returns:
        Bearer token string hoặc None nếu thất bại
    """
    if not force_refresh:
        cached = _load_cached_token()
        if cached:
            return cached

    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
    except ImportError:
        log.error("[DP] DrissionPage not installed: pip install DrissionPage")
        return None

    log.info("[DP] Opening Chrome to capture Suno bearer token...")

    captured_token = [None]   # mutable container trong closure

    try:
        opts = ChromiumOptions()
        if chrome_profile:
            opts.set_argument(f"--user-data-dir={chrome_profile}")
        if headless:
            opts.headless()

        page = ChromiumPage(addr_or_opts=opts)

        # Navigate đến Suno → trigger API calls
        log.info(f"[DP] Navigating to {API_TRIGGER_URL}...")
        page.get(API_TRIGGER_URL)

        # Chờ và scan network requests để bắt Authorization header
        start = time.time()
        while time.time() - start < timeout:
            try:
                # Dùng CDP Network để lấy headers (DrissionPage hỗ trợ)
                # Cách 1: Đọc cookie __client_uat___ (Clerk JWT)
                cookies = page.cookies(as_dict=True)
                for k, v in cookies.items():
                    if '__client' in k.lower() or 'session' in k.lower():
                        log.debug(f"[DP] Cookie found: {k}={v[:30]}...")

                # Cách 2: Chạy JS để lấy token từ Clerk auth
                token_js = page.run_js("""
                    try {
                        // Clerk.js stores JWT in window.__clerk
                        if (window.Clerk && window.Clerk.session) {
                            return window.Clerk.session.getToken().then
                                ? null  // async - handled separately
                                : window.Clerk.session.lastActiveToken?.jwt;
                        }
                        return null;
                    } catch(e) { return null; }
                """)
                if token_js and len(str(token_js)) > 30:
                    captured_token[0] = str(token_js).strip()
                    log.info(f"[DP] Got token from Clerk.session (sync)")
                    break

                # Cách 3: Intercept XHR (nếu page có network listener)
                # Đợi user action hoặc auto-refresh
                time.sleep(1.5)

                # Re-check sau 2 giây bằng cách request credentials endpoint
                if int(time.time() - start) % 10 == 0:
                    page.run_js("""
                        fetch('https://studio-api-prod.suno.com/api/credits/v1/', {
                            method: 'GET',
                            credentials: 'include'
                        });
                    """)

            except Exception as e:
                log.debug(f"[DP] JS check: {e}")
                time.sleep(1.5)

        # Cách 4: Dùng Clerk getToken async
        if not captured_token[0]:
            try:
                token_async = page.run_js("""
                    return new Promise((resolve) => {
                        if (window.Clerk && window.Clerk.session) {
                            window.Clerk.session.getToken().then(t => resolve(t));
                        } else {
                            resolve(null);
                        }
                    });
                """)
                if token_async and len(str(token_async)) > 30:
                    captured_token[0] = str(token_async).strip()
                    log.info("[DP] Got token from Clerk.session.getToken()")
            except Exception as e:
                log.warning(f"[DP] Async token: {e}")

        page.quit()

    except Exception as e:
        log.error(f"[DP] DrissionPage error: {e}")
        return None

    token = captured_token[0]
    if token:
        _save_token(token)
        log.info(f"[DP] Token captured & cached: {token[:30]}...")
    else:
        log.error("[DP] Could not capture bearer token")

    return token


def get_token_from_network_listener(
    chrome_profile: Optional[str] = None,
    timeout: int = 60,
) -> Optional[str]:
    """
    Phương pháp mạnh hơn: dùng DrissionPage listen() để bắt packets.
    Mở suno.com, listen tất cả requests → bắt Authorization header.
    """
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
    except ImportError:
        return None

    opts = ChromiumOptions()
    if chrome_profile:
        opts.set_argument(f"--user-data-dir={chrome_profile}")

    captured = [None]

    page = ChromiumPage(addr_or_opts=opts)

    # Start listening BEFORE navigation
    page.listen.start(API_HOST)

    page.get(API_TRIGGER_URL)

    log.info("[LISTEN] Waiting for Suno API request to capture token...")
    start = time.time()
    while time.time() - start < timeout and not captured[0]:
        # Poll cho packets
        packet = page.listen.wait(timeout=3)
        if packet:
            headers = {}
            if hasattr(packet, 'request') and packet.request:
                headers = getattr(packet.request, 'headers', {}) or {}
            elif hasattr(packet, 'headers'):
                headers = packet.headers or {}

            auth = headers.get('Authorization') or headers.get('authorization', '')
            if auth.startswith('Bearer ') and len(auth) > 40:
                token = auth[7:].strip()
                captured[0] = token
                log.info(f"[LISTEN] Bearer token captured: {token[:30]}...")
                break

        # Trigger API call để có request
        if int(time.time() - start) % 8 < 2:
            try:
                page.run_js("""
                    fetch('https://studio-api-prod.suno.com/api/credits/v1/', {
                        credentials: 'include'
                    });
                """)
            except:
                pass

    page.listen.stop()
    page.quit()

    if captured[0]:
        _save_token(captured[0])

    return captured[0]
