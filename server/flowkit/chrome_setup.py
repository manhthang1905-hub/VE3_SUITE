"""
FlowKit Chrome Setup — DrissionPage-based login check + project creation.

Ported from server/chrome_session.py setup flow.
DrissionPage opens Chrome itself (like old server), NOT connecting to existing.

Usage:
    from chrome_setup import setup_chrome
    page = setup_chrome(chrome_dir, ext_dir, port, account, proxy_arg)
"""
import os
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


# ─── CDP helpers (ported from old server chrome_session.py) ───────────

def _enforce_window_layout(page, window_args, log):
    """Enforce window position/size via CDP — same as old server _enforce_window_layout."""
    try:
        bounds = {'windowState': 'normal'}
        for arg in (window_args or []):
            if '--window-position=' in arg:
                parts = arg.split('=', 1)[1].split(',')
                bounds['left'] = int(parts[0])
                bounds['top'] = int(parts[1])
            elif '--window-size=' in arg:
                parts = arg.split('=', 1)[1].split(',')
                bounds['width'] = int(parts[0])
                bounds['height'] = int(parts[1])

        if len(bounds) <= 1:
            return

        info = page.run_cdp('Browser.getWindowForTarget')
        window_id = info.get('windowId')
        if not window_id:
            return
        page.run_cdp('Browser.setWindowBounds', windowId=window_id, bounds=bounds)
        log("Window layout enforced via CDP: %s" % bounds)
    except Exception as e:
        log("CDP layout skip: %s" % e)


def _apply_zoom(page, log):
    """Apply 50% zoom via CDP + JS — same as old server apply_page_zoom."""
    zoom_val = int(os.getenv("CHROME_PAGE_ZOOM", "50"))
    zoom_val = max(25, min(200, zoom_val))
    target = f"{zoom_val}%"
    scale = max(0.25, min(2.0, zoom_val / 100.0))

    zoom_js = """
        (function() {
            try { document.documentElement.style.zoom = '%s'; } catch(e) {}
            try { if (document.body) document.body.style.zoom = '100%%'; } catch(e) {}
        })();
    """ % target

    zoom_bootstrap_js = """
        (function() {
            var z = '%s';
            var applyZoom = function() {
                try { document.documentElement.style.zoom = z; } catch(e) {}
                try { if (document.body) document.body.style.zoom = '100%%'; } catch(e) {}
            };
            try { applyZoom(); } catch(e) {}
            try { document.addEventListener('DOMContentLoaded', applyZoom, true); } catch(e) {}
            try { window.addEventListener('load', applyZoom, true); } catch(e) {}
        })();
    """ % target

    try:
        # CDP: register zoom for ALL future page loads (before scripts run)
        page.run_cdp('Page.addScriptToEvaluateOnNewDocument', source=zoom_bootstrap_js)
        # CDP: set page scale factor (like old server)
        page.run_cdp('Emulation.setPageScaleFactor', pageScaleFactor=scale)
        # JS: apply to current page
        page.run_js(zoom_js)
        log("Zoom %s applied (CDP + JS)" % target)
    except Exception as e:
        # Fallback: JS only
        try:
            page.run_js(zoom_js)
            log("Zoom %s applied (JS only, CDP failed: %s)" % (target, e))
        except Exception:
            log("Zoom failed: %s" % e)


def _inject_fingerprint(page, ext_dir, instance_name, log):
    """Inject fingerprint via CDP Page.addScriptToEvaluateOnNewDocument — like old server."""
    try:
        fp_path = Path(ext_dir) / "fp_inject.js"
        if not fp_path.exists():
            return
        js = fp_path.read_text(encoding="utf-8")
        # CDP: inject for ALL future page loads (before page scripts run)
        page.run_cdp('Page.addScriptToEvaluateOnNewDocument', source=js)
        # JS: apply to current page too
        page.run_js(js)
        log("Fingerprint injected via CDP")
    except Exception as e:
        log("Fingerprint CDP inject skip: %s" % e)


# ─── Login / Account Check ───────────────────────────────────────────

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


def _do_login(chrome_dir: Path, account: dict, proxy_arg: str = "", worker_id: int = 0) -> bool:
    """Login Google account using google_login.py (same as old server _auto_login)."""
    try:
        sys.path.insert(0, str(BASE_DIR))
        from google_login import login_google_chrome
        portable_exe = chrome_dir / "GoogleChromePortable.exe"
        return login_google_chrome(
            account_info=account,
            chrome_portable=str(portable_exe),
            worker_id=worker_id,
            proxy_arg=proxy_arg,
        )
    except Exception as e:
        logger.error("[Setup] Login error: %s", e)
        return False


def _kill_chrome_for_dir(chrome_dir: Path):
    """Kill any Chrome processes using this chrome_dir. Ensures clean state before login."""
    if sys.platform != "win32":
        return
    import subprocess
    dir_name = chrome_dir.name
    try:
        subprocess.run(
            ['wmic', 'process', 'where',
             f"name='chrome.exe' and CommandLine like '%{dir_name}%'",
             'call', 'terminate'],
            capture_output=True, timeout=10,
            creationflags=0x08000000,
        )
    except Exception:
        pass
    time.sleep(2)


# ─── ChromiumOptions Builder ─────────────────────────────────────────

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
        co.set_argument("--proxy-bypass-list=<-loopback>")

    if window_args:
        for arg in window_args:
            co.set_argument(arg)

    return co


# ─── Main Setup ──────────────────────────────────────────────────────

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
    Full Chrome setup — ported from old server chrome_session.py setup().

    Flow:
    1. Generate fingerprint
    2. Open Chrome via DrissionPage (with extension)
    3. Enforce window layout via CDP (Browser.setWindowBounds)
    4. Inject fingerprint via CDP (Page.addScriptToEvaluateOnNewDocument)
    5. Check account → login if needed
    6. Navigate to Flow
    7. Apply zoom via CDP (Emulation.setPageScaleFactor) + JS
    8. Click 'Dự án mới' / create project
    9. Wait for textarea
    10. Disconnect DrissionPage (Chrome stays open for extension)
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

    # worker_id for google_login port assignment
    _worker_id = port - 19200 if port >= 19200 else 0

    # 1. Open Chrome
    co = _build_options(chrome_dir, ext_dir, port, proxy_arg, window_args)
    log("Opening Chrome: %s (port %d)" % (chrome_dir.name, port))

    try:
        page = ChromiumPage(co)
        log("Chrome opened: %s" % (page.title or "(no title)"))
    except Exception as e:
        log("Chrome failed: %s" % e)
        return False

    # 2. Enforce window layout via CDP (like old server _enforce_window_layout)
    _enforce_window_layout(page, window_args, log)

    # 3. Inject fingerprint via CDP (like old server inject_fingerprint_spoof)
    _inject_fingerprint(page, ext_dir, instance_name, log)

    # 4. Check account
    need_login = False
    if account:
        current_email = _check_logged_in(page)
        target_email = account["id"].strip().lower()
        log("Current: %s | Target: %s" % (current_email or "(none)", target_email))

        if current_email != target_email:
            log("Wrong account — need login")
            need_login = True

    # 5. Login if needed
    if need_login:
        try:
            page.quit()
        except Exception:
            pass
        _kill_chrome_for_dir(chrome_dir)

        clean_chrome_profile(chrome_dir)
        login_ok = _do_login(chrome_dir, account, proxy_arg, _worker_id)
        if not login_ok:
            log("Login FAILED!")
            return False

        log("Login OK — reopening Chrome...")
        # Re-generate fingerprint (profile was cleaned)
        if instance_name:
            try:
                from launcher import generate_fingerprint
                generate_fingerprint(ext_dir, instance_name)
            except Exception:
                pass
        _write_chrome_prefs(chrome_dir)
        co2 = _build_options(chrome_dir, ext_dir, port, proxy_arg, window_args)
        try:
            page = ChromiumPage(co2)
            log("Chrome reopened: %s" % (page.title or "(no title)"))
        except Exception as e:
            log("Chrome reopen failed: %s" % e)
            return False

        # Enforce layout + fingerprint again
        _enforce_window_layout(page, window_args, log)
        _inject_fingerprint(page, ext_dir, instance_name, log)

    # 6. Navigate to Flow
    log("Navigating to Flow...")
    page.get(FLOW_URL)
    time.sleep(5)

    # 7. Apply zoom (like old server apply_page_zoom)
    _apply_zoom(page, log)

    # Check if redirected to login
    url = page.url or ""
    if any(ind in url for ind in LOGIN_INDICATORS):
        log("Redirected to login page!")
        if account:
            try:
                page.quit()
            except Exception:
                pass
            _kill_chrome_for_dir(chrome_dir)
            clean_chrome_profile(chrome_dir)
            login_ok = _do_login(chrome_dir, account, proxy_arg, _worker_id)
            if not login_ok:
                log("Login FAILED after redirect!")
                return False
            if instance_name:
                try:
                    from launcher import generate_fingerprint
                    generate_fingerprint(ext_dir, instance_name)
                except Exception:
                    pass
            _write_chrome_prefs(chrome_dir)
            co3 = _build_options(chrome_dir, ext_dir, port, proxy_arg, window_args)
            try:
                page = ChromiumPage(co3)
                _enforce_window_layout(page, window_args, log)
                _inject_fingerprint(page, ext_dir, instance_name, log)
                page.get(FLOW_URL)
                time.sleep(5)
                _apply_zoom(page, log)
            except Exception as e:
                log("Chrome reopen after login failed: %s" % e)
                return False

    # 8. Create project
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

    # Re-apply zoom after project page loads
    _apply_zoom(page, log)

    # 9. Wait for textarea
    if _wait_for_textarea(page):
        log("Chrome ready — textarea visible")
    else:
        log("Textarea not found (may still work)")

    # 10. Disconnect DrissionPage — Chrome stays open for extension
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

    _worker_id = debug_port - 19200 if debug_port >= 19200 else 0

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
                _kill_chrome_for_dir(chrome_dir)
                from launcher import clean_chrome_profile, _write_chrome_prefs
                clean_chrome_profile(chrome_dir)
                login_ok = _do_login(chrome_dir, account, proxy_arg, _worker_id)
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

    # Apply zoom
    _apply_zoom(page, log)

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

    _apply_zoom(page, log)

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
