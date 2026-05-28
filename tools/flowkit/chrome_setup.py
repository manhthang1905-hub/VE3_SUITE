"""
FlowKit Chrome Setup — ported from server/chrome_session.py setup().

DrissionPage opens Chrome, check account, login if needed, navigate Flow,
create project, wait textarea. Then kill Chrome for agent to restart.
"""
import os
import sys
import time
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
FLOW_URL = "https://labs.google/fx/tools/flow?hl=en"

LOGIN_INDICATORS = [
    "accounts.google.com/signin",
    "accounts.google.com/v3/signin",
    "accounts.google.com/ServiceLogin",
    "accounts.google.com/AccountChooser",
]


# ─── CDP helpers (y nguyen tu server cu chrome_session.py) ──────────

def _enforce_window_layout(page, window_args, log):
    """Ep vi tri/size bang CDP — y nguyen _enforce_window_layout server cu."""
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
    """Apply zoom — y nguyen apply_page_zoom server cu."""
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
        page.run_cdp('Page.addScriptToEvaluateOnNewDocument', source=zoom_bootstrap_js)
        page.run_cdp('Emulation.setPageScaleFactor', pageScaleFactor=scale)
        try:
            page.run_cdp('Runtime.evaluate', expression=zoom_js)
        except Exception:
            page.run_js(zoom_js)
        log("Zoom %s applied (CDP + JS)" % target)
    except Exception as e:
        try:
            page.run_cdp('Runtime.evaluate', expression=zoom_js)
            log("Zoom %s applied (Runtime.evaluate, CDP partial fail: %s)" % (target, e))
        except Exception:
            try:
                page.run_js(zoom_js)
                log("Zoom %s applied (JS fallback)" % target)
            except Exception:
                log("Zoom failed: %s" % e)


def _inject_fingerprint(page, ext_dir, instance_name, log):
    """Disabled — fingerprint spoofing breaks reCAPTCHA trust scores."""
    return


JS_BLOCK_NEW_TAB = """
(function() {
    try {
        window.open = function() { return null; };
    } catch (e) {}
    try {
        var fix = function(root) {
            var scope = root || document;
            var links = scope.querySelectorAll ? scope.querySelectorAll('a[target="_blank"]') : [];
            for (var i = 0; i < links.length; i++) {
                links[i].setAttribute('target', '_self');
                links[i].removeAttribute('rel');
            }
        };
        fix(document);
        document.addEventListener('click', function(ev) {
            var el = ev.target;
            var a = el && el.closest ? el.closest('a[target="_blank"]') : null;
            if (a) {
                a.setAttribute('target', '_self');
                a.removeAttribute('rel');
            }
        }, true);
        document.addEventListener('DOMContentLoaded', function() { fix(document); }, true);
    } catch (e) {}
    return 'TAB_GUARD_OK';
})();
"""


def _inject_tab_guard(page, log):
    """Chan mo tab moi — y nguyen inject_tab_guard server cu."""
    try:
        page.run_cdp('Page.addScriptToEvaluateOnNewDocument', source=JS_BLOCK_NEW_TAB)
        page.run_js(JS_BLOCK_NEW_TAB)
        log("Tab guard injected")
    except Exception as e:
        log("Tab guard skip: %s" % e)


def _close_extra_tabs(page, log):
    """Close all tabs except the Flow tab — keep only 1 tab."""
    try:
        tab_ids = page.tab_ids
        if len(tab_ids) <= 1:
            return
        log("Found %d tabs, closing extras" % len(tab_ids))
        flow_tab = None
        for tid in tab_ids:
            try:
                tab = page.get_tab(tid)
                url = tab.url or ""
                if "labs.google" in url or "fx/tools/flow" in url:
                    flow_tab = tid
                    break
            except Exception:
                continue
        keep = flow_tab or tab_ids[0]
        for tid in tab_ids:
            if tid != keep:
                try:
                    page.close_tabs(tid)
                except Exception:
                    pass
        log("Closed %d extra tabs" % (len(tab_ids) - 1))
    except Exception as e:
        log("Close extra tabs skip: %s" % e)


JS_CLEANUP = """
(function() {
    try { localStorage.clear(); } catch(e) {}
    try { sessionStorage.clear(); } catch(e) {}
    try { indexedDB.databases().then(function(dbs) { dbs.forEach(function(db) { indexedDB.deleteDatabase(db.name); }); }); } catch(e) {}
    try { document.cookie.split(";").forEach(function(c) { document.cookie = c.replace(/^ +/, "").replace(/=.*/, "=;expires=" + new Date().toUTCString() + ";path=/"); }); } catch(e) {}
    try { caches.keys().then(function(names) { names.forEach(function(name) { caches.delete(name); }); }); } catch(e) {}
    try { navigator.serviceWorker.getRegistrations().then(function(regs) { regs.forEach(function(r) { r.unregister(); }); }); } catch(e) {}
    return 'CLEANED';
})();
"""


JS_VERIFY_FINGERPRINT = """
(function() {
    var c = navigator.hardwareConcurrency;
    var m = navigator.deviceMemory || 'N/A';
    var w = screen.width;
    var h = screen.height;
    var gl = null;
    try {
        var canvas = document.createElement('canvas');
        var ctx = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        if (ctx) gl = ctx.getParameter(37446);
    } catch(e) {}
    return 'cores=' + c + ' mem=' + m + ' screen=' + w + 'x' + h + ' gpu=' + (gl || 'N/A').substring(0, 40);
})();
"""


# ─── Account Check — y nguyen _check_current_account server cu ──────

def _check_current_account(page, log) -> str:
    """Check email dang login — y nguyen _check_current_account server cu."""
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
            var btns = document.querySelectorAll('[aria-label*="@"]');
            if (btns.length > 0) {
                var label = btns[0].getAttribute('aria-label');
                var match = label.match(/[\\w.-]+@[\\w.-]+/);
                if (match) return match[0];
            }
            return '';
        """)
        return str(email or "").strip().lower()
    except Exception as e:
        log("Check account error: %s" % e)
        return ""


# ─── UI helpers ─────────────────────────────────────────────────────

def _dismiss_popups(page):
    """Dismiss Flow popups — y nguyen server cu."""
    try:
        page.run_js("""
            (function() {
                var dismiss = ['Bắt đầu', 'Get started', 'Got it', 'Dismiss', 'Đã hiểu', 'I understand',
                    'Começar', 'Entendi', 'Mulai', 'Mengerti', 'Jetzt starten', 'Verstanden',
                    'Commencer', 'Compris', 'Empezar', 'Entendido', 'Inizia', 'Capito',
                    'Начать', 'Понятно', '開始', '了解', '시작'];
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
                            text.indexOf('Accept') > -1 || text.indexOf('Aceitar') > -1 ||
                            text.indexOf('Terima') > -1 || text.indexOf('Akzeptieren') > -1 ||
                            text.indexOf('Accepter') > -1 || text.indexOf('Aceptar') > -1) {
                            dbtns[i].click(); return;
                        }
                    }
                }
            })();
        """)
    except Exception:
        pass


def _click_new_project(page, log=None) -> bool:
    """Click 'Du an moi' — dung page.ele() thay vi run_js (de tin cay hon)."""
    # 1. Tim button co text add_2 (material icon cua "New project")
    try:
        btn = page.ele('tag:button@@text():add_2', timeout=2)
        if btn:
            btn.click()
            if log:
                log("Clicked button (add_2)")
            return True
    except Exception:
        pass

    # 2. Tim button "New project" / "Du an moi"
    for text in ['New project', 'Dự án mới', 'Novo projeto', 'Proyek baru',
                 'Neues Projekt', 'Nouveau projet', 'Nuevo proyecto']:
        try:
            btn = page.ele('tag:button@@text():%s' % text, timeout=1)
            if btn:
                btn.click()
                if log:
                    log("Clicked button (%s)" % text)
                return True
        except Exception:
            pass

    # 3. Tim "Create with Flow" / "Tao voi Flow"
    for text in ['Create with Flow', 'Tạo với Flow', 'Criar com o Flow',
                 'Buat dengan Flow']:
        try:
            btn = page.ele('tag:button@@text():%s' % text, timeout=1)
            if btn:
                btn.click()
                if log:
                    log("Clicked button (%s)" % text)
                return True
        except Exception:
            pass

    # 4. Fallback: bat ky element nao co text add_2
    try:
        el = page.ele('text:add_2', timeout=1)
        if el:
            el.click()
            if log:
                log("Clicked element (text add_2)")
            return True
    except Exception:
        pass

    return False


def _create_new_project(page, log) -> bool:
    """Tao project moi — y nguyen _create_new_project server cu."""
    for attempt in range(30):
        try:
            url = page.url or ""
            if "/project/" in url:
                log("Already in project: %s" % url)
                return True
        except Exception:
            pass

        _dismiss_popups(page)

        if _click_new_project(page, log):
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

        if attempt > 0 and attempt % 10 == 0:
            log("Reload Flow page (%d/30)..." % attempt)
            try:
                page.get(FLOW_URL)
                time.sleep(8)
            except Exception:
                pass

        time.sleep(2)

    log("Failed to create project after 30 attempts!")
    return False


def _wait_for_textarea(page, timeout: int = 30) -> bool:
    """Doi textarea — y nguyen server cu."""
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
    """Login Google — goi google_login.py y nguyen server cu _auto_login."""
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
    """Kill Chrome processes + clean lock files."""
    if sys.platform != "win32":
        return
    import subprocess
    dir_name = chrome_dir.name
    for exe_name in ("chrome.exe", "GoogleChromePortable.exe"):
        try:
            subprocess.run(
                ['wmic', 'process', 'where',
                 f"name='{exe_name}' and CommandLine like '%{dir_name}%'",
                 'call', 'terminate'],
                capture_output=True, timeout=10,
                creationflags=0x08000000,
            )
        except Exception:
            pass
    time.sleep(3)
    # Clean profile lock files so Chrome can start fresh
    profile_dir = chrome_dir / "Data" / "profile" / "Default"
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_file = profile_dir.parent / lock_name
        try:
            if lock_file.exists():
                lock_file.unlink()
        except Exception:
            pass


def _clear_chrome_data(chrome_dir: Path, log):
    """Clear Chrome data — y nguyen _clear_chrome_data server cu."""
    from launcher import clean_chrome_profile
    clean_chrome_profile(chrome_dir)
    log("Cleared Chrome data")


# ─── ChromiumOptions Builder ─────────────────────────────────────────

def _build_options(chrome_dir: Path, ext_dir: Path, port: int, proxy_arg: str = "",
                   window_args: list = None):
    """Build ChromiumOptions — y nguyen server cu setup()."""
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


# ─── Main Setup — be nguyen tu server cu chrome_session.py setup() ──

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
    Full Chrome setup — be nguyen tu server cu chrome_session.py setup().

    1. Mo Chrome -> check account hien tai
    2. Neu sai account -> clear data + login dung account
    3. Vao labs.google/fx/tools/flow
    4. Tao project moi
    5. Doi textarea san sang
    6. Kill Chrome (agent se restart bang subprocess)
    """
    log = log_func or (lambda msg: logger.info("[Setup] %s", msg))

    from DrissionPage import ChromiumPage
    from launcher import _write_chrome_prefs

    # Generate fingerprint before launch
    if instance_name:
        try:
            from launcher import generate_fingerprint
            generate_fingerprint(ext_dir, instance_name)
        except Exception as e:
            log("Fingerprint generation error: %s" % e)

    _write_chrome_prefs(chrome_dir)
    _worker_id = port - 19200 if port >= 19200 else 0

    # ── 1. Mo Chrome de check account ──
    log("Mo Chrome: %s (port %d)" % (chrome_dir.name, port))
    co = _build_options(chrome_dir, ext_dir, port, proxy_arg, window_args)

    try:
        page = ChromiumPage(co)
        log("Chrome opened: %s" % (page.title or "(no title)"))
        _enforce_window_layout(page, window_args, log)
        _apply_zoom(page, log)
    except Exception as e:
        log("Chrome failed: %s" % e)
        _kill_chrome_for_dir(chrome_dir)
        return False

    # Chan mo tab moi truoc khi thao tac UI
    _inject_tab_guard(page, log)

    # Inject fingerprint ngay sau khi mo Chrome
    _inject_fingerprint(page, ext_dir, instance_name, log)

    # Verify fingerprint
    try:
        verify = page.run_js(JS_VERIFY_FINGERPRINT)
        log("Fingerprint verify: %s" % verify)
    except Exception:
        pass

    # ── 2. Check account hien tai ──
    need_login = False
    if account:
        target_email = account['id'].strip().lower()
        current_email = _check_current_account(page, log)
        log("Account hien tai: %s" % (current_email or '(chua login)'))
        log("Account can dung: %s" % target_email)

        if current_email == target_email:
            log("Account DUNG -> khong can login lai")
        else:
            log("Account SAI hoac chua login -> clear data + login")
            need_login = True

    # ── 3. Login neu can ──
    if need_login:
        try:
            page.quit()
        except Exception:
            pass
        page = None
        _kill_chrome_for_dir(chrome_dir)

        _clear_chrome_data(chrome_dir, log)
        login_ok = _do_login(chrome_dir, account, proxy_arg, _worker_id)
        if not login_ok:
            log("Login FAILED: %s" % account['id'])
            return False

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
            log("Chrome mo lai: %s" % (page.title or "(no title)"))
            _enforce_window_layout(page, window_args, log)
            _apply_zoom(page, log)
        except Exception as e:
            log("Chrome restart failed: %s" % e)
            _kill_chrome_for_dir(chrome_dir)
            return False

        _inject_tab_guard(page, log)
        _inject_fingerprint(page, ext_dir, instance_name, log)

    log("Login OK — ban giao cho FlowKit")

    # ── 4. Kill Chrome — ban giao cho FlowKit agent ──
    try:
        page.quit()
    except Exception:
        pass
    _kill_chrome_for_dir(chrome_dir)

    log("Setup xong — Chrome killed, agent se restart")
    return True


# ─── apply_chrome_cdp — apply CDP settings sau khi subprocess start ──

def apply_chrome_cdp(
    debug_port: int,
    ext_dir: Path = None,
    instance_name: str = "",
    window_args: list = None,
    log_func=None,
    timeout: int = 30,
) -> bool:
    """
    Connect to running Chrome, apply CDP settings (layout + zoom + fingerprint + tab guard).
    Called after subprocess starts Chrome.
    """
    log = log_func or (lambda msg: logger.info("[CDP] %s", msg))

    from DrissionPage import ChromiumPage, ChromiumOptions

    co = ChromiumOptions()
    co.set_address(f"127.0.0.1:{debug_port}")

    page = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            page = ChromiumPage(co)
            break
        except Exception:
            time.sleep(2)

    if not page:
        log("Cannot connect to Chrome on port %d" % debug_port)
        return False

    try:
        # Wait for Flow page to load (not chrome:// or blank)
        flow_ready = False
        for _ in range(20):
            try:
                url = page.url or ""
                if "labs.google" in url and "flow" in url:
                    flow_ready = True
                    break
            except Exception:
                pass
            time.sleep(2)

        if not flow_ready:
            log("Flow page not loaded — navigating...")
            try:
                page.get(FLOW_URL)
                time.sleep(8)
            except Exception:
                pass

        _close_extra_tabs(page, log)
        _enforce_window_layout(page, window_args, log)
        _inject_tab_guard(page, log)
        if ext_dir:
            _inject_fingerprint(page, ext_dir, instance_name, log)
        log("CDP settings applied")
    except Exception as e:
        log("CDP apply error: %s" % e)
    finally:
        try:
            page.disconnect()
        except Exception:
            pass

    return True


# ─── ensure_chrome_ready — dung cho recovery_manager ────────────────

def ensure_chrome_ready(
    debug_port: int,
    chrome_dir: Path = None,
    account: dict = None,
    proxy_arg: str = "",
    log_func=None,
) -> bool:
    """
    Connect to already-running Chrome, navigate Flow, create project.
    Used by recovery_manager after Chrome restart.
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
        current_email = _check_current_account(page, log)
        target_email = account.get("id", "").strip().lower()
        log("Account hien tai: %s | Can: %s" % (current_email or "(chua login)", target_email))

        if current_email != target_email:
            log("Account SAI -> login lai")
            if chrome_dir:
                try:
                    page.quit()
                except Exception:
                    pass
                _kill_chrome_for_dir(chrome_dir)
                _clear_chrome_data(chrome_dir, log)
                login_ok = _do_login(chrome_dir, account, proxy_arg, _worker_id)
                if not login_ok:
                    log("Login FAILED!")
                    return False
                log("Login OK — reconnecting...")
                from launcher import _write_chrome_prefs
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

    # Verify login — vao Flow check redirect
    log("Check login: %s" % FLOW_URL)
    page.get(FLOW_URL)
    time.sleep(5)

    url = page.url or ""
    if 'accounts.google.com' in url:
        log("Redirect ve login — chua dang nhap")
        try:
            page.disconnect()
        except Exception:
            pass
        return False

    log("Login OK — ready for FlowKit")
    try:
        page.disconnect()
    except Exception:
        pass
    return True
