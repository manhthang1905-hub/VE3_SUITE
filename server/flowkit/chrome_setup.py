"""
FlowKit Chrome Setup — DrissionPage-based login check + project creation.

Ported from server/chrome_session.py setup flow.
Connects to an already-running Chrome via --remote-debugging-port.

Usage:
    from chrome_setup import ensure_chrome_ready
    ok = ensure_chrome_ready(chrome_port=9222, account={"id": "...", "password": "..."})
"""
import sys
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
FLOW_URL = "https://labs.google/fx/tools/flow"

LOGIN_INDICATORS = [
    "accounts.google.com/signin",
    "accounts.google.com/v3/signin",
    "accounts.google.com/ServiceLogin",
    "accounts.google.com/AccountChooser",
]


def _check_logged_in(page) -> str:
    """Check which Google account is logged in. Returns email or ''."""
    try:
        page.get("https://myaccount.google.com")
        time.sleep(3)

        url = page.url or ""
        if "accounts.google.com" in url:
            return ""

        email = page.run_js("""
            var els = document.querySelectorAll('[data-email]');
            if (els.length > 0) return els[0].getAttribute('data-email');
            var all = document.querySelectorAll('header *');
            for (var i = 0; i < all.length; i++) {
                var t = all[i].textContent.trim();
                if (t.indexOf('@') > 0 && t.indexOf('.') > 0 && t.length < 60) return t;
            }
            return '';
        """)
        return str(email or "").strip().lower()
    except Exception as e:
        logger.warning("[Setup] Check login error: %s", e)
        return ""


def _is_on_login_page(page) -> bool:
    """Check if current page is a Google login page."""
    try:
        url = page.url or ""
        return any(ind in url for ind in LOGIN_INDICATORS)
    except Exception:
        return False


def _dismiss_popups(page):
    """Dismiss Flow popups (Bắt đầu / Get started / Got it)."""
    try:
        page.run_js("""
            (function() {
                var dismiss = ['Bắt đầu', 'Get started', 'Got it', 'Dismiss', 'Đã hiểu', 'I understand'];
                var btns = document.querySelectorAll('button');
                for (var b of btns) {
                    var t = (b.textContent || '').trim();
                    for (var d of dismiss) {
                        if (t.indexOf(d) >= 0) { b.click(); return; }
                    }
                }
                var dialog = document.querySelector('[role="dialog"]');
                if (dialog) {
                    var dbtns = dialog.querySelectorAll('button');
                    for (var i = 0; i < dbtns.length; i++) {
                        var text = dbtns[i].textContent.trim();
                        if (text.indexOf('đồng ý') > -1 || text.indexOf('Agree') > -1 ||
                            text.indexOf('Accept') > -1) {
                            dbtns[i].click(); return;
                        }
                    }
                }
            })();
        """)
    except Exception:
        pass


def _click_new_project(page) -> bool:
    """Click 'Dự án mới' or 'Create with Flow' button."""
    try:
        result = page.run_js("""
            (function() {
                var btns = document.querySelectorAll('button');
                for (var b of btns) {
                    var t = (b.textContent || '').trim();
                    if (t.indexOf('add_2') >= 0 || t.indexOf('Dự án mới') >= 0 || t.indexOf('New project') >= 0) {
                        b.click(); return 'CLICKED_NEW';
                    }
                }
                for (var b of btns) {
                    var t = (b.textContent || '').trim();
                    if (t.indexOf('Create with Flow') >= 0 || t.indexOf('Tạo với Flow') >= 0) {
                        b.click(); return 'CLICKED_CREATE';
                    }
                }
                var spans = document.querySelectorAll('span');
                for (var s of spans) {
                    var t = (s.textContent || '').trim();
                    if (t.indexOf('Create with Flow') >= 0 || t.indexOf('Tạo với Flow') >= 0) {
                        var btn = s.closest('button');
                        if (btn) { btn.click(); return 'CLICKED_SPAN'; }
                    }
                }
                return 'NOT_FOUND';
            })();
        """)
        return result and "CLICKED" in str(result)
    except Exception:
        return False


def _create_new_project(page, log_func=None) -> bool:
    """Create new project — retry loop like old server."""
    log = log_func or (lambda msg, *a: logger.info("[Setup] " + msg))

    for attempt in range(20):
        try:
            url = page.url or ""
            if "/project/" in url:
                log("Already in project: %s" % url)
                return True
        except Exception:
            pass

        _dismiss_popups(page)

        if _click_new_project(page):
            log("Clicked new project button (attempt %d)" % (attempt + 1))
            time.sleep(3)
            for w in range(30):
                try:
                    if "/project/" in (page.url or ""):
                        log("Project created: %s" % page.url)
                        return True
                except Exception:
                    pass
                time.sleep(1)
            log("Click OK but project not loaded, retrying...")
            continue

        if attempt > 0 and attempt % 5 == 0:
            log("Reload Flow page (%d/20)..." % attempt)
            try:
                page.get(FLOW_URL)
                time.sleep(3)
            except Exception:
                pass

        time.sleep(0.5)

    log("Failed to create project after 20 attempts!")
    return False


def _wait_for_textarea(page, timeout: int = 30) -> bool:
    """Wait for textarea/contenteditable to appear."""
    for _ in range(timeout):
        try:
            result = page.run_js("""
                var ce = document.querySelector('[contenteditable="true"]');
                var ta = document.querySelector('textarea:not([class*="recaptcha"])');
                if (ce) return 'contenteditable';
                if (ta) return 'textarea';
                return 'not_found';
            """)
            if result and result != "not_found":
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def _do_login(chrome_dir: Path, account: dict, proxy_arg: str = "") -> bool:
    """Login Google account using google_login.py."""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from google_login import login_google_chrome

        chrome_exe = chrome_dir / "App" / "Chrome-bin" / "chrome.exe"
        if not chrome_exe.exists():
            portable = chrome_dir / "GoogleChromePortable.exe"
            chrome_exe = portable

        return login_google_chrome(
            account_info=account,
            chrome_portable=str(chrome_exe),
            proxy_arg=proxy_arg,
        )
    except Exception as e:
        logger.error("[Setup] Login error: %s", e)
        return False


def ensure_chrome_ready(
    debug_port: int,
    chrome_dir: Path = None,
    account: dict = None,
    proxy_arg: str = "",
    log_func=None,
) -> bool:
    """
    Connect to running Chrome via DrissionPage, ensure logged in and in project.

    Flow (same as old server setup()):
    1. Connect to Chrome debug port
    2. Check if logged in → if not, login via google_login.py
    3. Navigate to Flow URL
    4. Click 'Dự án mới' / 'Create with Flow'
    5. Wait for textarea ready

    Args:
        debug_port: Chrome remote debugging port
        chrome_dir: Path to Chrome Portable root (for login)
        account: {"id": "email", "password": "pass"} — for auto-login
        proxy_arg: Proxy string for login Chrome
        log_func: Logging function(msg)

    Returns: True if Chrome is ready (in project, textarea visible)
    """
    log = log_func or (lambda msg: logger.info("[Setup] %s", msg))

    from DrissionPage import ChromiumPage, ChromiumOptions

    log("Connecting to Chrome at 127.0.0.1:%d..." % debug_port)
    co = ChromiumOptions()
    co.set_address(f"127.0.0.1:{debug_port}")

    try:
        page = ChromiumPage(co)
        log("Connected: %s" % (page.title or "(no title)"))
    except Exception as e:
        log("Cannot connect to Chrome: %s" % e)
        return False

    # Check login
    if account:
        current_email = _check_logged_in(page)
        target_email = account["id"].strip().lower()
        log("Current account: %s" % (current_email or "(not logged in)"))
        log("Target account: %s" % target_email)

        if current_email != target_email:
            log("Need login — closing Chrome for google_login.py...")
            try:
                page.quit()
            except Exception:
                pass

            if chrome_dir:
                from launcher import clean_chrome_profile
                clean_chrome_profile(chrome_dir)

            login_ok = _do_login(chrome_dir, account, proxy_arg)
            if not login_ok:
                log("Login FAILED!")
                return False

            log("Login OK, reconnecting...")
            time.sleep(3)
            try:
                page = ChromiumPage(co)
                log("Reconnected: %s" % (page.title or "(no title)"))
            except Exception as e:
                log("Reconnect failed: %s" % e)
                return False

    # Navigate to Flow
    log("Navigating to Flow...")
    page.get(FLOW_URL)
    time.sleep(5)

    # Check if redirected to login
    if _is_on_login_page(page):
        log("Redirected to login page!")
        if account and chrome_dir:
            try:
                page.quit()
            except Exception:
                pass
            login_ok = _do_login(chrome_dir, account, proxy_arg)
            if not login_ok:
                log("Login FAILED after redirect!")
                return False
            time.sleep(3)
            try:
                page = ChromiumPage(co)
                page.get(FLOW_URL)
                time.sleep(5)
            except Exception as e:
                log("Reconnect after login failed: %s" % e)
                return False

    # Check if already in project
    url = page.url or ""
    if "/project/" in url:
        log("Already in project: %s" % url)
    else:
        log("Creating new project...")
        if not _create_new_project(page, log_func=log):
            log("Failed to create project!")
            return False

    # Wait for textarea
    if _wait_for_textarea(page):
        log("Chrome ready — textarea visible")
    else:
        log("Textarea not found (may still work)")

    # Disconnect DrissionPage (don't close Chrome)
    try:
        page.disconnect()
    except Exception:
        pass

    return True
