"""
FlowKit Chrome Setup — DrissionPage-based login check + project creation.

Ported from server/chrome_session.py setup flow.
DrissionPage opens Chrome itself (like old server), NOT connecting to existing.

Usage:
    from chrome_setup import setup_chrome
    page = setup_chrome(chrome_dir, ext_dir, port, account, proxy_arg)
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


def _create_new_project(page, log) -> bool:
    """Create new project — retry loop like old server _create_new_project."""
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
            log("Clicked new project (attempt %d)" % (attempt + 1))
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
    """Login Google account using google_login.py (same as old server _auto_login)."""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from google_login import login_google_chrome
        portable_exe = chrome_dir / "GoogleChromePortable.exe"
        return login_google_chrome(
            account_info=account,
            chrome_portable=str(portable_exe),
            proxy_arg=proxy_arg,
        )
    except Exception as e:
        logger.error("[Setup] Login error: %s", e)
        return False


def _build_options(chrome_dir: Path, ext_dir: Path, port: int, proxy_arg: str = "",
                   window_args: list = None):
    """Build ChromiumOptions — same pattern as old server setup()."""
    from DrissionPage import ChromiumOptions

    portable_exe = chrome_dir / "GoogleChromePortable.exe"

    co = ChromiumOptions()
    co.set_browser_path(str(portable_exe))
    co.set_address(f"127.0.0.1:{port}")
    co.set_argument("--no-first-run")
    co.set_argument("--no-default-browser-check")
    co.set_argument(f"--load-extension={ext_dir}")
    co.set_argument("--disable-background-timer-throttling")
    co.set_argument("--disable-renderer-backgrounding")
    co.set_argument("--disable-backgrounding-occluded-windows")
    co.set_argument("--disable-session-crashed-bubble")
    co.set_argument("--hide-crash-restore-bubble")

    if proxy_arg:
        co.set_argument(f"--proxy-server={proxy_arg}")

    if window_args:
        for arg in window_args:
            co.set_argument(arg)

    return co


def setup_chrome(
    chrome_dir: Path,
    ext_dir: Path,
    port: int,
    account: dict = None,
    proxy_arg: str = "",
    window_args: list = None,
    log_func=None,
    instance_name: str = "",
) -> bool:
    """
    Full Chrome setup — DrissionPage opens Chrome, checks login, creates project.

    Same flow as old server chrome_session.py setup():
    1. Generate fingerprint (includes CSS zoom)
    2. Open Chrome via DrissionPage (with extension loaded)
    3. Check account → login if needed
    4. Navigate to Flow
    5. Click 'Dự án mới'
    6. Wait for textarea
    7. Disconnect DrissionPage (Chrome stays open for extension)

    Returns: True if Chrome is ready
    """
    log = log_func or (lambda msg: logger.info("[Setup] %s", msg))

    from DrissionPage import ChromiumPage
    from launcher import _write_chrome_prefs, clean_chrome_profile

    # Generate fingerprint before launch (includes CSS zoom 50%)
    if instance_name:
        try:
            from launcher import generate_fingerprint
            generate_fingerprint(ext_dir, instance_name)
        except Exception as e:
            log("Fingerprint generation error: %s" % e)

    # Write Preferences before launch
    _write_chrome_prefs(chrome_dir)

    # 1. Open Chrome
    co = _build_options(chrome_dir, ext_dir, port, proxy_arg, window_args)
    log("Opening Chrome: %s (port %d)" % (chrome_dir.name, port))

    try:
        page = ChromiumPage(co)
        log("Chrome opened: %s" % (page.title or "(no title)"))
    except Exception as e:
        log("Chrome failed: %s" % e)
        return False

    # 2. Check account
    need_login = False
    if account:
        current_email = _check_logged_in(page)
        target_email = account["id"].strip().lower()
        log("Current: %s | Target: %s" % (current_email or "(none)", target_email))

        if current_email != target_email:
            log("Wrong account — need login")
            need_login = True

    # 3. Login if needed
    if need_login:
        try:
            page.quit()
        except Exception:
            pass

        clean_chrome_profile(chrome_dir)
        login_ok = _do_login(chrome_dir, account, proxy_arg)
        if not login_ok:
            log("Login FAILED!")
            return False

        log("Login OK — reopening Chrome...")
        _write_chrome_prefs(chrome_dir)
        co2 = _build_options(chrome_dir, ext_dir, port, proxy_arg, window_args)
        try:
            page = ChromiumPage(co2)
            log("Chrome reopened: %s" % (page.title or "(no title)"))
        except Exception as e:
            log("Chrome reopen failed: %s" % e)
            return False

    # 4. Navigate to Flow
    log("Navigating to Flow...")
    page.get(FLOW_URL)
    time.sleep(5)

    # Check if redirected to login
    url = page.url or ""
    if any(ind in url for ind in LOGIN_INDICATORS):
        log("Redirected to login page!")
        if account:
            try:
                page.quit()
            except Exception:
                pass
            clean_chrome_profile(chrome_dir)
            login_ok = _do_login(chrome_dir, account, proxy_arg)
            if not login_ok:
                log("Login FAILED after redirect!")
                return False
            _write_chrome_prefs(chrome_dir)
            co3 = _build_options(chrome_dir, ext_dir, port, proxy_arg, window_args)
            try:
                page = ChromiumPage(co3)
                page.get(FLOW_URL)
                time.sleep(5)
            except Exception as e:
                log("Chrome reopen after login failed: %s" % e)
                return False

    # 5. Create project
    url = page.url or ""
    if "/project/" in url:
        log("Already in project: %s" % url)
    else:
        log("Creating new project...")
        if not _create_new_project(page, log):
            log("Failed to create project!")
            try:
                page.disconnect()
            except Exception:
                pass
            return False

    # 6. Wait for textarea
    if _wait_for_textarea(page):
        log("Chrome ready — textarea visible")
    else:
        log("Textarea not found (may still work)")

    # Disconnect DrissionPage — Chrome stays open for extension
    try:
        page.disconnect()
    except Exception:
        pass

    log("Setup complete — extension takes over")
    return True


def ensure_chrome_ready(
    debug_port: int,
    chrome_dir: Path = None,
    account: dict = None,
    proxy_arg: str = "",
    log_func=None,
) -> bool:
    """
    Connect to already-running Chrome via debug port, navigate to Flow, create project.

    Used by recovery_manager after Chrome restart (subprocess).
    DrissionPage connects to existing Chrome — does NOT open a new one.
    """
    log = log_func or (lambda msg: logger.info("[Setup] %s", msg))

    from DrissionPage import ChromiumPage, ChromiumOptions

    co = ChromiumOptions()
    co.set_address(f"127.0.0.1:{debug_port}")

    try:
        page = ChromiumPage(co)
        log("Connected to Chrome on port %d" % debug_port)
    except Exception as e:
        log("Cannot connect to Chrome on port %d: %s" % (debug_port, e))
        return False

    # Check login
    if account:
        current_email = _check_logged_in(page)
        target_email = account.get("id", "").strip().lower()
        log("Current: %s | Target: %s" % (current_email or "(none)", target_email))

        if current_email != target_email:
            log("Wrong account — need login")
            if chrome_dir:
                try:
                    page.quit()
                except Exception:
                    pass
                from launcher import clean_chrome_profile, _write_chrome_prefs
                clean_chrome_profile(chrome_dir)
                login_ok = _do_login(chrome_dir, account, proxy_arg)
                if not login_ok:
                    log("Login FAILED!")
                    return False
                log("Login OK — reconnecting...")
                _write_chrome_prefs(chrome_dir)
                try:
                    page = ChromiumPage(co)
                except Exception as e:
                    log("Reconnect failed: %s" % e)
                    return False
            else:
                try:
                    page.disconnect()
                except Exception:
                    pass
                return False

    # Navigate to Flow
    log("Navigating to Flow...")
    page.get(FLOW_URL)
    time.sleep(5)

    url = page.url or ""
    if any(ind in url for ind in LOGIN_INDICATORS):
        log("Redirected to login — needs re-login")
        try:
            page.disconnect()
        except Exception:
            pass
        return False

    # Create project
    if "/project/" in (page.url or ""):
        log("Already in project: %s" % page.url)
    else:
        log("Creating new project...")
        if not _create_new_project(page, log):
            log("Failed to create project!")
            try:
                page.disconnect()
            except Exception:
                pass
            return False

    if _wait_for_textarea(page):
        log("Chrome ready — textarea visible")
    else:
        log("Textarea not found (may still work)")

    try:
        page.disconnect()
    except Exception:
        pass

    log("Setup complete")
    return True
