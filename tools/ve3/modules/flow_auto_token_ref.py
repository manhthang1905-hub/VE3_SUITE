from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

try:
    import pyautogui as pag

    pag.FAILSAFE = True
    pag.PAUSE = 0.1
except Exception:  # pragma: no cover
    pag = None

try:
    import pyperclip
except Exception:  # pragma: no cover
    pyperclip = None


class FlowAutoTokenRef:
    """
    Ported from reference repo `ve3-tool-simple`.

    Main behavior:
    - Open Flow in a real Chrome profile
    - Inject capture JS
    - If no project is known, create a new project
    - Switch to image mode
    - Send a harmless image prompt
    - Capture bearer token + project_id from Flow requests

    This module is intentionally standalone so it can be tested before being
    wired into VE3_SUITE production flow.
    """

    FLOW_URL = "https://labs.google/fx/vi/tools/flow"
    DEFAULT_REFRESH_PROMPT = "simple neutral product photo, white mug on wooden table, natural daylight"

    def __init__(
        self,
        chrome_path: str,
        profile_path: str,
        headless: bool = False,
        callback: Optional[Callable[[str], None]] = None,
    ):
        self.chrome_path = chrome_path
        self.profile_path = profile_path
        self.headless = headless
        self.callback = callback

    def log(self, msg: str) -> None:
        if self.callback:
            self.callback(msg)

    def _portable_user_data_dir(self) -> str:
        chrome_exe = Path(self.chrome_path or "")
        if not chrome_exe.exists():
            return ""
        chrome_dir = chrome_exe.parent
        for candidate in (chrome_dir / "Data" / "profile", chrome_dir / "User Data"):
            if candidate.exists():
                return str(candidate)
        return ""

    def _profile_args(self) -> list[str]:
        args: list[str] = []
        portable_dir = self._portable_user_data_dir()
        if portable_dir:
            args.append(f"--user-data-dir={portable_dir}")
            self.log(f"[AUTH] Using portable profile: {portable_dir}")
            external = str(self.profile_path or "").strip()
            if external and Path(external).exists() and Path(external) != Path(portable_dir):
                self.log(f"[AUTH] Ignore external profile_path because portable profile is the source of truth: {external}")
            return args

        if not self.profile_path:
            return args

        profile_path = Path(self.profile_path)
        if not profile_path.exists():
            return args

        default_folder = profile_path / "Default"
        if default_folder.exists():
            args.append(f"--user-data-dir={profile_path}")
            self.log(f"[AUTH] Portable profile not found, fallback user-data-dir: {profile_path}")
        else:
            args.extend(
                [
                    f"--user-data-dir={profile_path.parent}",
                    f"--profile-directory={profile_path.name}",
                ]
            )
            self.log(f"[AUTH] Portable profile not found, fallback profile: {profile_path.parent} / {profile_path.name}")
        return args

    def open_chrome(self, url: str) -> bool:
        try:
            cmd = [self.chrome_path]
            cmd.extend(self._profile_args())
            if self.headless:
                cmd.extend(["--start-minimized", "--window-position=-32000,-32000"])
            cmd.append(url)
            subprocess.Popen(cmd, shell=False)
            return True
        except Exception as exc:
            self.log(f"[AUTH] Open Chrome failed: {exc}")
            return False

    def _devtools_eval(self, js: str, settle: float = 0.5) -> bool:
        if not pag or not pyperclip:
            return False
        try:
            pag.hotkey("ctrl", "shift", "j")
            time.sleep(1.0)
            pyperclip.copy(js)
            time.sleep(0.2)
            pag.hotkey("ctrl", "v")
            time.sleep(0.2)
            pag.press("enter")
            time.sleep(settle)
            pag.hotkey("ctrl", "shift", "j")
            time.sleep(0.4)
            return True
        except Exception as exc:
            self.log(f"[AUTH] DevTools eval failed: {exc}")
            return False

    def inject_capture_only(self) -> bool:
        if not pag or not pyperclip:
            return False
        capture_script = (
            "window._tk=null;window._pj=null;"
            "(function(){if(window.__ve3cap)return;window.__ve3cap=1;"
            "var f=window.fetch;window.fetch=function(u,o){var s=u?u.toString():'';"
            "if(s.includes('flowMedia')||s.includes('aisandbox')){var h=o&&o.headers?o.headers:{};"
            "var a=h.Authorization||h.authorization||'';"
            "if(a.startsWith('Bearer ')){window._tk=a.substring(7);"
            "var m=s.match(/\\/projects\\/([^\\/]+)\\//);if(m)window._pj=m[1];}}"
            "return f.apply(this,arguments);};})();"
        )
        self.log("[AUTH] Inject capture script...")
        return self._devtools_eval(capture_script, settle=0.6)

    def click_new_project_js(self) -> bool:
        js = (
            "(function(){var texts=['Dự án mới','Du an moi','New project'];"
            "var nodes=document.querySelectorAll('button,[role=\"button\"]');"
            "for(var i=0;i<nodes.length;i++){var t=(nodes[i].textContent||'').trim();"
            "for(var j=0;j<texts.length;j++){if(t.includes(texts[j])){nodes[i].click();return true;}}}"
            "return false;})();"
        )
        self.log("[AUTH] Click create project...")
        return self._devtools_eval(js, settle=0.8)

    def click_image_mode_js(self) -> bool:
        js = (
            "(async function(){"
            "var dd=document.querySelector('button[role=\"combobox\"]');"
            "if(dd){dd.click();await new Promise(r=>setTimeout(r,500));}"
            "var all=document.querySelectorAll('*');"
            "for(var i=0;i<all.length;i++){var t=(all[i].textContent||'').trim();"
            "if(t==='Tạo hình ảnh'||t.includes('Tạo hình ảnh từ văn bản')||t==='Image generation'||t.includes('Create image'))"
            "{var r=all[i].getBoundingClientRect();if(r.height>10&&r.height<120){all[i].click();return true;}}}"
            "return false;})();"
        )
        self.log("[AUTH] Select image mode...")
        return self._devtools_eval(js, settle=1.0)

    def focus_textarea_js(self) -> bool:
        js = "(function(){var ta=document.querySelector('textarea');if(ta){ta.focus();ta.click();return true;}return false;})();"
        self.log("[AUTH] Focus textarea...")
        return self._devtools_eval(js, settle=0.6)

    def send_prompt_manual(self, prompt: str) -> bool:
        if not pag or not pyperclip:
            return False
        try:
            pyperclip.copy(prompt)
            time.sleep(0.2)
            pag.hotkey("ctrl", "v")
            time.sleep(0.4)
            pag.press("enter")
            time.sleep(0.3)
            self.log("[AUTH] Prompt sent.")
            return True
        except Exception as exc:
            self.log(f"[AUTH] Send prompt failed: {exc}")
            return False

    def get_token_from_devtools(self) -> Tuple[Optional[str], Optional[str]]:
        if not pag or not pyperclip:
            return None, None
        try:
            pag.hotkey("ctrl", "shift", "j")
            time.sleep(1.0)
            pyperclip.copy("copy(JSON.stringify({t:window._tk,p:window._pj}))")
            time.sleep(0.2)
            pag.hotkey("ctrl", "v")
            time.sleep(0.2)
            pag.press("enter")
            time.sleep(0.8)
            pag.hotkey("ctrl", "shift", "j")
            time.sleep(0.3)
            text = pyperclip.paste()
            if not text or not text.startswith("{"):
                return None, None
            import json

            data = json.loads(text)
            token = data.get("t")
            project_id = data.get("p")
            if token and len(str(token)) > 50:
                return str(token), str(project_id or "")
            return None, None
        except Exception:
            return None, None

    def extract_token(
        self,
        project_id: str = "",
        project_url: str = "",
        callback: Optional[Callable[[str], None]] = None,
        timeout: int = 90,
        prompt: str = "",
    ) -> Tuple[Optional[str], Optional[str], str]:
        self.callback = callback or self.callback

        if not pag:
            return None, None, "Missing pyautogui"
        if not pyperclip:
            return None, None, "Missing pyperclip"

        try:
            if project_url:
                url = project_url
                self.log(f"[AUTH] Open existing project URL: {url[:60]}...")
            elif project_id:
                url = f"{self.FLOW_URL}/project/{project_id}"
                self.log(f"[AUTH] Open existing project ID: {project_id[:20]}...")
            else:
                url = self.FLOW_URL
                self.log("[AUTH] Open Flow home for new project...")

            if not self.open_chrome(url):
                return None, None, "Could not open Chrome"

            self.log("[AUTH] Wait page load (12s)...")
            time.sleep(12)

            self.inject_capture_only()
            time.sleep(1)

            if not project_id and not project_url:
                self.click_new_project_js()
                self.log("[AUTH] Wait project creation (5s)...")
                time.sleep(5)
            else:
                self.log("[AUTH] Existing project mode -> trigger image to refresh token.")

            self.click_image_mode_js()
            time.sleep(2)
            self.focus_textarea_js()
            time.sleep(1)
            self.send_prompt_manual(prompt or self.DEFAULT_REFRESH_PROMPT)

            self.log("[AUTH] Wait Flow request to finish (20s minimum)...")
            time.sleep(20)

            check_deadline = time.time() + timeout
            while time.time() < check_deadline:
                token, proj = self.get_token_from_devtools()
                if token:
                    self.log("[AUTH] Token captured.")
                    return token, proj or project_id, ""
                time.sleep(3)

            return None, None, "Could not capture token from Flow"
        except Exception as exc:
            return None, None, f"Auth error: {exc}"
