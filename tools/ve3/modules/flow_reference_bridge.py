from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


LogFunc = Callable[[str, str], None]


@dataclass
class FlowReferenceConfig:
    chrome_path: str
    profile_dir: str = ""
    email: str = ""
    password: str = ""
    totp_secret: str = ""
    worker_id: int = 0
    proxy_arg: str = ""


@dataclass
class FlowReferenceResult:
    ok: bool = False
    logged_in: bool = False
    token: str = ""
    project_id: str = ""
    project_url: str = ""
    error: str = ""


@dataclass
class FlowReferenceUploadResult:
    ok: bool = False
    media_id: str = ""
    project_url: str = ""
    error: str = ""


class FlowReferenceBridge:
    """
    Thin bridge over the reference repo flow.

    Goal:
    1. Check login state on an existing Chrome profile.
    2. Login if needed.
    3. Open an existing Flow project or create a new one.
    4. Trigger one real image request and capture token/project_id.
    """

    def __init__(
        self,
        suite_root: str | Path,
        config: FlowReferenceConfig,
        log_func: Optional[LogFunc] = None,
    ):
        self.suite_root = Path(suite_root)
        self.config = config
        self.log_func = log_func or (lambda msg, level="INFO": None)
        self.reference_root = self.suite_root / "tools" / "ve3"
        self._ensure_reference_on_path()

    def log(self, msg: str, level: str = "INFO") -> None:
        self.log_func(msg, level)

    def _flowkit_extension_dir(self) -> str:
        """Derive FlowKit extension dir from Chrome path (Copy N → ext_810{N-1})."""
        import re as _re
        chrome_path = self._resolved_browser_path()
        m = _re.search(r"Copy \((\d+)\)", chrome_path)
        if m:
            idx = int(m.group(1)) - 1
            ext_dir = self.suite_root / "flowkit_extensions" / f"ext_{8100 + idx}"
            if ext_dir.is_dir():
                return str(ext_dir)
        # Fallback: Chrome without Copy → use ext_8100
        ext_dir = self.suite_root / "flowkit_extensions" / "ext_8100"
        if ext_dir.is_dir():
            return str(ext_dir)
        return ""

    def _ensure_reference_on_path(self) -> None:
        ref_str = str(self.reference_root)
        if ref_str not in sys.path:
            sys.path.insert(0, ref_str)

    def _run_reference_subprocess(self, script: str, timeout: int = 600) -> dict:
        cmd = ["python", "-X", "utf8", "-c", script]
        env = dict(**__import__("os").environ)
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            cmd,
            cwd=str(self.reference_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
        if proc.stdout:
            for line in proc.stdout.splitlines():
                if line.startswith("__RESULT__="):
                    try:
                        return json.loads(line.split("=", 1)[1].strip())
                    except Exception:
                        pass
                self.log(f"[REF-SUB] {line}", "INFO")
        if proc.stderr:
            for line in proc.stderr.splitlines():
                self.log(f"[REF-SUB][ERR] {line}", "WARN")
        return {
            "ok": False,
            "error": f"reference subprocess failed rc={proc.returncode}",
        }

    def _build_account_info(self) -> dict:
        return {
            "id": self.config.email.strip(),
            "password": self.config.password.strip(),
            "totp_secret": self.config.totp_secret.strip(),
        }

    def _portable_user_data_dir(self) -> str:
        fallback = str(self.config.profile_dir or "").strip()
        if fallback and Path(fallback).exists():
            return fallback
        chrome_path = Path(self.config.chrome_path or "")
        if not chrome_path.exists():
            return ""
        chrome_dir = chrome_path.parent
        for candidate in (chrome_dir / "Data" / "profile", chrome_dir / "User Data"):
            if candidate.exists():
                return str(candidate)
        return ""

    def _resolved_browser_path(self) -> str:
        chrome_path = Path(self.config.chrome_path or "")
        if not chrome_path.exists():
            return str(chrome_path)
        if chrome_path.name.lower() == "googlechromeportable.exe":
            embedded = chrome_path.parent / "App" / "Chrome-bin" / "chrome.exe"
            if embedded.exists():
                return str(embedded)
        return str(chrome_path)

    def _resolved_user_data_dir(self) -> str:
        portable_dir = self._portable_user_data_dir()
        if portable_dir:
            return portable_dir
        fallback = str(self.config.profile_dir or "").strip()
        if fallback and Path(fallback).exists():
            return fallback
        return ""

    def _log_profile_source(self) -> None:
        portable_dir = self._portable_user_data_dir()
        external_dir = str(self.config.profile_dir or "").strip()
        if portable_dir:
            self.log(f"[REF] Using portable profile: {portable_dir}", "INFO")
            if external_dir and Path(external_dir).exists() and Path(external_dir) != Path(portable_dir):
                self.log(f"[REF] Ignore external profile_dir because portable profile is the source of truth: {external_dir}", "WARN")
            return
        if external_dir and Path(external_dir).exists():
            self.log(f"[REF] Portable profile not found -> fallback external profile: {external_dir}", "WARN")
        else:
            self.log("[REF] No explicit user-data-dir found; relying on Chrome portable defaults", "WARN")

    def check_logged_in(self, project_url: str = "") -> bool:
        target_url = project_url or "https://labs.google/fx/vi/tools/flow"
        user_data_dir = self._resolved_user_data_dir()
        user_data_arg = f"options.set_user_data_path(r'{user_data_dir}')" if user_data_dir else ""
        script = f"""
import json, time
from DrissionPage import ChromiumOptions, ChromiumPage
options = ChromiumOptions()
options.set_browser_path(r'{self.config.chrome_path}')
{user_data_arg}
options.set_local_port({9322 + int(self.config.worker_id)})
options.set_argument('--no-first-run')
options.set_argument('--no-default-browser-check')
page = None
out = {{"ok": False, "logged_in": False, "url": "", "error": ""}}
try:
    page = ChromiumPage(options)
    page.get(r'{target_url}')
    time.sleep(8)
    current_url = page.url or ''
    out["url"] = current_url
    logged_out = any(k in current_url for k in [
        'accounts.google.com/signin',
        'accounts.google.com/v3/signin',
        'accounts.google.com/ServiceLogin',
        'accounts.google.com/AccountChooser',
    ])
    out["ok"] = True
    out["logged_in"] = not logged_out
except Exception as e:
    out["error"] = str(e)
finally:
    try:
        if page:
            page.quit()
    except Exception:
        pass
print("__RESULT__=" + json.dumps(out, ensure_ascii=False))
"""
        data = self._run_reference_subprocess(script, timeout=120)
        if data.get("ok") and data.get("logged_in"):
            self.log(f"[REF] Chrome profile appears logged in: {str(data.get('url') or '')[:80]}", "SUCCESS")
            return True
        if data.get("error"):
            self.log(f"[REF] Login check failed: {data['error']}", "WARN")
        else:
            self.log("[REF] Chrome profile is not logged in", "WARN")
        return False

    def ensure_login(self, project_url: str = "") -> bool:
        if self.check_logged_in(project_url=project_url):
            return True
        acct = self._build_account_info()
        profile_dir = self._resolved_user_data_dir()
        profile_kw = f"profile_dir=r'{profile_dir}'," if profile_dir else ""
        self._log_profile_source()
        script = f"""
import json
from google_login import login_google_chrome
account_info = {json.dumps(acct, ensure_ascii=False)}
ok = login_google_chrome(
    account_info=account_info,
    chrome_portable=r'{self.config.chrome_path}',
    {profile_kw}
    worker_id={int(self.config.worker_id)},
    proxy_arg=r'{self.config.proxy_arg}',
)
print("__RESULT__=" + json.dumps({{"ok": bool(ok)}}, ensure_ascii=False))
"""
        self.log("[REF] Profile not logged in -> running reference login flow", "INFO")
        data = self._run_reference_subprocess(script, timeout=300)
        ok = bool(data.get("ok"))
        self.log("[REF] Login success" if ok else "[REF] Login failed", "SUCCESS" if ok else "ERROR")
        return ok

    def upload_reference_image(self, image_path: str | Path, project_url: str = "", project_id: str = "", timeout: int = 45) -> FlowReferenceUploadResult:
        result = FlowReferenceUploadResult(project_url=project_url or (f"https://labs.google/fx/vi/tools/flow/project/{project_id}" if project_id else ""))
        image_path = Path(image_path)
        if not image_path.exists():
            result.error = f"reference image not found: {image_path}"
            return result
        try:
            profile_dir = self._resolved_user_data_dir()
            browser_path = self._resolved_browser_path()
            target_url = result.project_url or "https://labs.google/fx/vi/tools/flow"
            port = 9322 + int(self.config.worker_id)
            script = f'''
import json
import re
import time
from io import BytesIO
from pathlib import Path

from DrissionPage import ChromiumOptions, ChromiumPage
from PIL import Image
import win32clipboard
import win32con

image_path = Path(r{str(image_path)!r})
chrome_path = r{browser_path!r}
user_data_dir = {json.dumps(profile_dir)}
target_url = {json.dumps(target_url)}
port = {port}
timeout = {int(timeout)}
out = {{"ok": False, "media_id": "", "project_url": "", "error": "", "packets": []}}

def copy_image_to_clipboard(path):
    image = Image.open(path).convert('RGB')
    output = BytesIO()
    image.save(output, 'BMP')
    data = output.getvalue()[14:]
    output.close()
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_DIB, data)
    finally:
        win32clipboard.CloseClipboard()

def ids_from_text(text):
    ids = []
    for pat in (r'media\\.getMediaUrlRedirect\\?name=([0-9a-fA-F-]{{20,}})', r'[?&]name=([0-9a-fA-F-]{{20,}})', r'"name"\\s*:\\s*"([0-9a-fA-F-]{{20,}})"'):
        for match in re.findall(pat, text):
            if match not in ids:
                ids.append(match)
    return ids

def packet_to_dict(pkt):
    row = {{}}
    for name in ('url', 'method', 'resource_type', 'request_id', 'tab_id'):
        try:
            row[name] = getattr(pkt, name)
        except Exception:
            pass
    try:
        row['postData'] = str(getattr(getattr(pkt, 'request', None), 'postData', ''))[:1200]
    except Exception:
        pass
    return row

def open_page():
    options = ChromiumOptions()
    options.set_browser_path(chrome_path)
    if user_data_dir:
        options.set_user_data_path(user_data_dir)
    options.set_local_port(port)
    options.set_argument('--no-first-run')
    options.set_argument('--no-default-browser-check')
    try:
        return ChromiumPage(options)
    except Exception as first_exc:
        fallback_options = ChromiumOptions()
        fallback_options.set_local_port(9222)
        try:
            return ChromiumPage(fallback_options)
        except Exception:
            raise first_exc

page = None
try:
    page = open_page()
    if target_url:
        page.get(target_url)
        time.sleep(4)
    out['project_url'] = page.url or target_url
    page.listen.set_targets(targets=['media.getMediaUrlRedirect', '/fx/api/', 'flowMedia', 'upload', 'media'])
    page.listen.start()
    copy_image_to_clipboard(image_path)
    box = None
    try:
        box = page.ele('css:[contenteditable="true"]', timeout=8) or page.ele('css:[role="textbox"]', timeout=3) or page.ele('css:textarea', timeout=3)
    except Exception:
        box = None
    if box:
        box.click()
    else:
        page.run_js("""
(function(){{
  var el = document.querySelector('[contenteditable="true"]') || document.querySelector('[role="textbox"]') || document.querySelector('textarea');
  if (el) {{ el.focus(); el.click(); return 'focused'; }}
  return 'not-found';
}})();
""")
    time.sleep(0.5)
    try:
        page.actions.key_down('CTRL').key_down('V').key_up('V').key_up('CTRL')
    except Exception:
        page.run_cdp('Input.dispatchKeyEvent', type='keyDown', modifiers=2, windowsVirtualKeyCode=86, nativeVirtualKeyCode=86, code='KeyV', key='v')
        page.run_cdp('Input.dispatchKeyEvent', type='keyUp', modifiers=2, windowsVirtualKeyCode=86, nativeVirtualKeyCode=86, code='KeyV', key='v')
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            pkt = page.listen.wait(timeout=1)
        except Exception:
            pkt = None
        if not pkt:
            continue
        row = packet_to_dict(pkt)
        out['packets'].append(row)
        ids = ids_from_text(json.dumps(row, ensure_ascii=False))
        if ids:
            out['ok'] = True
            out['media_id'] = ids[-1]
            break
    if not out['ok'] and not out['error']:
        out['error'] = 'media id not captured after paste'
except Exception as exc:
    out['error'] = str(exc)
finally:
    try:
        if page:
            page.quit()
    except Exception:
        pass
print('__RESULT__=' + json.dumps(out, ensure_ascii=False))
'''
            self.log(f"[REF] Uploading local reference image through Flow UI: {image_path.name}", "INFO")
            data = self._run_reference_subprocess(script, timeout=max(90, int(timeout) + 45))
            if data.get("ok") and data.get("media_id"):
                result.ok = True
                result.media_id = str(data.get("media_id") or "")
                result.project_url = str(data.get("project_url") or result.project_url)
            else:
                result.error = str(data.get("error") or "reference upload failed")
            return result
        except Exception as exc:
            result.error = str(exc)
            return result

    def acquire_token(self, existing_project_id: str = "", existing_project_url: str = "", prompt: str = "", keep_chrome_open: bool = False) -> FlowReferenceResult:
        result = FlowReferenceResult()
        try:
            capture_prompt = prompt or "simple neutral product photo of a white ceramic mug on a wooden table, natural daylight"
            profile_dir = self._resolved_user_data_dir()
            browser_path = self._resolved_browser_path()
            acct = self._build_account_info()
            _fk_ext = self._flowkit_extension_dir() if keep_chrome_open else ""
            script = f"""
import json
import shutil
import time
from pathlib import Path

from DrissionPage import ChromiumOptions, ChromiumPage
from google_login import login_google_chrome
from modules.drission_flow_api import DrissionFlowAPI
account_info = {json.dumps(acct, ensure_ascii=False)}
out = {{"ok": False, "token": "", "project_id": "", "project_url": "", "error": ""}}
chrome_path = r'{browser_path}'
user_data_dir = {json.dumps(profile_dir)}
worker_id = {int(self.config.worker_id)}
proxy_arg = r'{self.config.proxy_arg}'
existing_project_id = {json.dumps(existing_project_id)}
existing_project_url = {json.dumps(existing_project_url or "")}
capture_prompt = {json.dumps(capture_prompt, ensure_ascii=False)}
keep_chrome_open = {keep_chrome_open}
flowkit_ext_dir = {json.dumps(_fk_ext)}

def log(msg):
    print(msg, flush=True)

def build_api():
    kwargs = {{}}
    if user_data_dir:
        kwargs['profile_dir'] = user_data_dir
    if flowkit_ext_dir:
        import os as _os
        _os.environ['FLOWKIT_LOAD_EXTENSION'] = flowkit_ext_dir  # noqa
    return DrissionFlowAPI(
        chrome_portable=chrome_path,
        skip_portable_detection=True,
        worker_id=worker_id,
        total_workers=1,
        headless=False,
        verbose=True,
        webshare_enabled=False,
        **kwargs,
    )

def is_logged_out_url(url):
    url = str(url or '')
    markers = (
        'accounts.google.com/signin',
        'accounts.google.com/v3/signin',
        'accounts.google.com/ServiceLogin',
        'accounts.google.com/AccountChooser',
    )
    return any(marker in url for marker in markers)

def build_options(port):
    options = ChromiumOptions()
    options.set_browser_path(chrome_path)
    if user_data_dir:
        options.set_user_data_path(user_data_dir)
    options.set_local_port(port)
    options.set_argument('--no-first-run')
    options.set_argument('--no-default-browser-check')
    if flowkit_ext_dir:
        options.set_argument('--load-extension=' + flowkit_ext_dir)
    return options

def read_logged_email(page):
    return str(page.run_js(\"\"\"
        var els = document.querySelectorAll('[data-email]');
        if (els.length > 0) return els[0].getAttribute('data-email');
        var all = document.querySelectorAll('header *');
        for (var i = 0; i < all.length; i++) {{
            var t = (all[i].textContent || '').trim();
            if (t.indexOf('@') > 0 && t.indexOf('.') > 0 && t.length < 120) return t;
        }}
        var btns = document.querySelectorAll('[aria-label*="@"]');
        if (btns.length > 0) {{
            var label = btns[0].getAttribute('aria-label') || '';
            var match = label.match(/[\\w.+-]+@[\\w.-]+/);
            if (match) return match[0];
        }}
        return '';
    \"\"\") or '').strip().lower()

def check_account_state(expected_email):
    expected_email = str(expected_email or '').strip().lower()
    if not expected_email:
        return ('skip', '')
    page = None
    try:
        page = ChromiumPage(build_options(9322 + worker_id))
        page.get('https://myaccount.google.com')
        time.sleep(3)
        verify_url = str(page.url or '').lower()
        if is_logged_out_url(verify_url):
            return ('logged_out', '')
        logged_email = read_logged_email(page)
        if not logged_email:
            return ('unknown', '')
        if logged_email == expected_email:
            return ('ok', logged_email)
        return ('mismatch', logged_email)
    except Exception as exc:
        return ('error', str(exc))
    finally:
        try:
            if page:
                page.quit()
        except Exception:
            pass

def clear_user_data():
    if not user_data_dir:
        return False, 'missing_user_data_dir'
    target = Path(user_data_dir)
    if not target.exists():
        return True, 'user_data_missing'
    # Preserve extension dirs so Load Unpacked extensions survive clear
    _ext_keep = {'Extensions', 'Extension State', 'Extension Rules',
                 'Local Extension Settings', 'Extension Scripts',
                 'IndexedDB', 'Local Storage', 'Secure Preferences',
                 'Preferences'}
    for child in target.iterdir():
        try:
            if child.is_dir():
                if child.name in _ext_keep:
                    continue
                # Also check inside Default/
                shutil.rmtree(child, ignore_errors=False)
            else:
                if child.name in ('Preferences', 'Secure Preferences'):
                    continue
                child.unlink()
        except Exception as exc:
            return False, str(exc)
    # Clear inside Default/ but keep extension subdirs
    default_dir = target / 'Default'
    if default_dir.is_dir():
        for child in default_dir.iterdir():
            if child.name in _ext_keep:
                continue
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=False)
                else:
                    if child.name in ('Preferences', 'Secure Preferences'):
                        continue
                    child.unlink()
            except Exception:
                pass
    return True, ''

def warmup_flow_home():
    page = None
    try:
        page = ChromiumPage(build_options(9422 + worker_id))
        page.get('https://labs.google/fx/vi/tools/flow')
        time.sleep(3)
        for attempt in range(10):
            click_result = page.run_js(\"\"\"
                (function() {{
                    var btns = document.querySelectorAll('button,[role="button"]');
                    for (var i = 0; i < btns.length; i++) {{
                        var text = (btns[i].textContent || '').trim();
                        if (text.includes('Create with Google Flow') || text.includes('Create with Flow') || text.includes('Tạo với Flow')) {{
                            btns[i].click();
                            return 'CLICKED_CREATE_WITH_FLOW';
                        }}
                    }}
                    return 'NOT_FOUND';
                }})();
            \"\"\")
            if click_result and 'CLICKED' in str(click_result):
                log("[INFO] [REF-WARMUP] clicked Create with Flow")
                time.sleep(2)
            ready = page.run_js(\"\"\"
                (function() {{
                    var btn = document.querySelector('button');
                    var all = document.querySelectorAll('button');
                    for (var i = 0; i < all.length; i++) {{
                        var text = (all[i].textContent || '').trim();
                        if (text === 'add_2' || text.includes('Dự án mới') || text.includes('New project')) return true;
                    }}
                    return false;
                }})();
            \"\"\")
            if ready:
                log("[INFO] [REF-WARMUP] Flow home ready")
                return True
            time.sleep(1)
        return True
    except Exception as exc:
        log("[WARN] [REF-WARMUP] " + str(exc))
        return False
    finally:
        try:
            if page:
                page.quit()
        except Exception:
            pass

api = None
try:
    verify_state, verify_email = check_account_state(account_info.get('id'))
    log("[INFO] [REF-VERIFY] initial state=" + str(verify_state) + " email=" + str(verify_email))

    if verify_state in ('logged_out', 'mismatch'):
        cleared, clear_error = (True, '') if keep_chrome_open else clear_user_data()
        if not cleared:
            out["error"] = "Clear chrome profile failed: " + str(clear_error)
        else:
            log("[WARN] [REF-VERIFY] cleared chrome user data before re-login")
            login_ok = login_google_chrome(
                account_info=account_info,
                chrome_portable=chrome_path,
                profile_dir=(user_data_dir or None),
                worker_id=worker_id,
                proxy_arg=proxy_arg,
            )
            if not login_ok:
                out["error"] = "Login failed after clearing chrome data"
            else:
                verify_state, verify_email = check_account_state(account_info.get('id'))
                log("[INFO] [REF-VERIFY] after login state=" + str(verify_state) + " email=" + str(verify_email))
                if verify_state != 'ok':
                    out["error"] = "Login verify failed after re-login"
    elif verify_state in ('unknown', 'error'):
        log("[WARN] [REF-VERIFY] cannot verify current account reliably; keep profile, try login without clearing")
        login_ok = login_google_chrome(
            account_info=account_info,
            chrome_portable=chrome_path,
            profile_dir=(user_data_dir or None),
            worker_id=worker_id,
            proxy_arg=proxy_arg,
        )
        if not login_ok:
            out["error"] = "Login failed after soft verify retry"
        else:
            verify_state, verify_email = check_account_state(account_info.get('id'))
            log("[INFO] [REF-VERIFY] after soft retry state=" + str(verify_state) + " email=" + str(verify_email))
            if verify_state != 'ok':
                out["error"] = "Login verify failed after soft retry"

    if not out["error"]:
        warmup_flow_home()
        api = build_api()
        target_url = existing_project_url or (f"https://labs.google/fx/vi/tools/flow/project/{{existing_project_id}}" if existing_project_id else None)
        setup_ok = api.setup(
            wait_for_project=True,
            timeout=120,
            warm_up=False,
            project_url=target_url,
            skip_mode_selection=False,
        )
        if not setup_ok:
            out["error"] = "Reference setup failed after login verify"

    if not out["error"]:
        out["logged_in"] = True
        capture_ok = api._capture_tokens(capture_prompt, timeout=20)
        if not capture_ok:
            raw = api.driver.run_js(\"\"\"
                return {{
                    tk: window._tk || '',
                    pj: window._pj || '',
                    rct: window._rct || '',
                    url: window._url || ''
                }};
            \"\"\") or {{}}
            raw_tk = str(raw.get('tk') or '').strip()
            raw_pj = str(raw.get('pj') or '').strip()
            if raw_tk and raw_pj:
                out["ok"] = True
                out["token"] = raw_tk
                out["project_id"] = raw_pj
                out["project_url"] = str(raw.get('url') or '') or (f'https://labs.google/fx/vi/tools/flow/project/{{raw_pj}}')
                out["error"] = "Fallback success without recaptcha token"
            else:
                out["error"] = "Reference token capture failed"
        else:
            pid = api.project_id or existing_project_id
            out["ok"] = True
            out["token"] = (api.bearer_token or '').replace('Bearer ', '').strip()
            out["project_id"] = pid or ''
            out["project_url"] = existing_project_url or (f'https://labs.google/fx/vi/tools/flow/project/{{pid}}' if pid else '')
finally:
    if not keep_chrome_open:
        try:
            if api:
                api.close()
        except Exception:
            pass
    else:
        log("[INFO] [FLOWKIT] Keeping Chrome open for FlowKit extension")
print("__RESULT__=" + json.dumps(out, ensure_ascii=False))
"""
            self.log("[REF] Trigger one real image request to capture token...", "INFO")
            data = self._run_reference_subprocess(script, timeout=420)
            if data.get("ok"):
                result.ok = True
                result.logged_in = bool(data.get("logged_in", True))
                result.token = str(data.get("token") or "")
                result.project_id = str(data.get("project_id") or "")
                result.project_url = str(data.get("project_url") or "")
            else:
                result.error = str(data.get("error") or "Reference token capture failed")
            return result
        except Exception as exc:
            result.error = str(exc)
            return result
