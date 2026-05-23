from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


try:
    import yaml
except Exception:  # pragma: no cover - optional at runtime
    yaml = None


FlowLogFunc = Callable[[str, str], None]


@dataclass
class FlowAuthProfile:
    name: str
    chrome_path: str
    user_data_dir: str
    profile_directory: str = ""
    flow_url: str = "https://labs.google/fx/vi/tools/flow"
    project_url_template: str = "https://labs.google/fx/vi/tools/flow/project/{project_id}"
    enabled: bool = True
    email: str = ""
    notes: str = ""


@dataclass
class FlowAuthState:
    token: str = ""
    project_id: str = ""
    project_url: str = ""
    acquired_at: float = 0.0
    source: str = ""
    profile_name: str = ""
    last_error: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.token and self.project_id)


class FlowAuthManager:
    """
    Standalone auth/token module for VE3_SUITE.

    Purpose:
    - Keep token/project automation separate from current VE3 worker logic.
    - Reuse an existing logged-in Chrome profile.
    - Either open an existing Flow project to refresh token, or create a new project.
    - Persist account/project token cache outside workbook logic.
    """

    FLOW_CAPTURE_JS = r"""
window.__ve3_auth = window.__ve3_auth || { token: null, project_id: null, hits: [] };
(function() {
  if (window.__ve3_auth_installed) return;
  window.__ve3_auth_installed = true;

  function saveHit(url, authValue) {
    try {
      if (!authValue || !String(authValue).startsWith('Bearer ')) return;
      const token = String(authValue).slice(7);
      let projectId = null;
      const match = String(url || '').match(/\/projects\/([^\/]+)\//);
      if (match) projectId = match[1];
      window.__ve3_auth.token = token;
      if (projectId) window.__ve3_auth.project_id = projectId;
      window.__ve3_auth.hits.push({ url: String(url || ''), project_id: projectId, ts: Date.now() });
      if (window.__ve3_auth.hits.length > 20) {
        window.__ve3_auth.hits = window.__ve3_auth.hits.slice(-20);
      }
    } catch (e) {}
  }

  const origFetch = window.fetch;
  window.fetch = function(...args) {
    const [url, options] = args;
    const headers = (options && options.headers) || {};
    const auth = headers.Authorization || headers.authorization || '';
    if (String(url || '').includes('aisandbox-pa.googleapis.com') || String(url || '').includes('flowMedia')) {
      saveHit(url, auth);
    }
    return origFetch.apply(this, args);
  };

  const origOpen = XMLHttpRequest.prototype.open;
  const origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__ve3_auth_url = url;
    return origOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function(name, value) {
    if ((name || '').toLowerCase() === 'authorization') {
      saveHit(this.__ve3_auth_url || '', value);
    }
    return origSetHeader.apply(this, arguments);
  };
})();
"""

    CREATE_PROJECT_JS = r"""
(() => {
  const texts = ['Dự án mới', 'Du an moi', 'New project'];
  const nodes = Array.from(document.querySelectorAll('button,[role="button"]'));
  for (const node of nodes) {
    const text = (node.textContent || '').trim();
    if (texts.some(t => text.includes(t))) {
      node.click();
      return true;
    }
  }
  return false;
})()
"""

    SELECT_IMAGE_MODE_JS = r"""
(() => {
  const buttons = Array.from(document.querySelectorAll('button,[role="button"],[role="option"]'));
  const modeTexts = ['Tạo hình ảnh', 'Tạo hình ảnh từ văn bản', 'Image generation', 'Create image'];
  for (const node of buttons) {
    const text = (node.textContent || '').trim();
    if (modeTexts.some(t => text.includes(t))) {
      node.click();
      return true;
    }
  }
  return false;
})()
"""

    FOCUS_TEXTAREA_JS = r"""
(() => {
  const ta = document.querySelector('textarea');
  if (!ta) return false;
  ta.focus();
  ta.click();
  return true;
})()
"""

    DEFAULT_REFRESH_PROMPT = "simple neutral product photo, white mug on wooden table, natural daylight"

    def __init__(
        self,
        suite_root: Path | str,
        config_path: Optional[Path | str] = None,
        cache_path: Optional[Path | str] = None,
        log_func: Optional[FlowLogFunc] = None,
    ):
        self.suite_root = Path(suite_root)
        self.config_path = Path(config_path) if config_path else self.suite_root / "tools" / "ve3" / "config" / "flow_auth.yaml"
        self.cache_path = Path(cache_path) if cache_path else self.suite_root / "config" / "flow_auth_cache.json"
        self.log_func = log_func or (lambda msg, level="INFO": None)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, msg: str, level: str = "INFO") -> None:
        self.log_func(msg, level)

    def load_profiles(self) -> List[FlowAuthProfile]:
        if yaml is None:
            raise RuntimeError("Missing PyYAML. Cannot load flow_auth.yaml")
        if not self.config_path.exists():
            return []
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        profiles = []
        for item in data.get("profiles", []):
            try:
                profile = FlowAuthProfile(**item)
            except TypeError:
                continue
            if profile.enabled:
                profiles.append(profile)
        return profiles

    def load_cache(self) -> Dict[str, Any]:
        if not self.cache_path.exists():
            return {"profiles": {}, "projects": {}}
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"profiles": {}, "projects": {}}
            data.setdefault("profiles", {})
            data.setdefault("projects", {})
            return data
        except Exception:
            return {"profiles": {}, "projects": {}}

    def save_cache(self, cache: Dict[str, Any]) -> None:
        self.cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_cached_state(self, profile_name: str = "", project_key: str = "") -> FlowAuthState:
        cache = self.load_cache()
        raw: Dict[str, Any] = {}
        if project_key:
            raw = cache.get("projects", {}).get(project_key, {}) or {}
        if not raw and profile_name:
            raw = cache.get("profiles", {}).get(profile_name, {}) or {}
        return FlowAuthState(**{k: raw.get(k, getattr(FlowAuthState(), k)) for k in FlowAuthState.__dataclass_fields__.keys()})

    def save_state(self, state: FlowAuthState, profile_name: str = "", project_key: str = "") -> None:
        cache = self.load_cache()
        if profile_name:
            cache.setdefault("profiles", {})[profile_name] = asdict(state)
        if project_key:
            cache.setdefault("projects", {})[project_key] = asdict(state)
        self.save_cache(cache)

    def choose_profile(self, profile_name: str = "") -> Optional[FlowAuthProfile]:
        profiles = self.load_profiles()
        if not profiles:
            return None
        if profile_name:
            for profile in profiles:
                if profile.name == profile_name:
                    return profile
        return profiles[0]

    def acquire(
        self,
        profile_name: str = "",
        project_key: str = "",
        existing_project_id: str = "",
        create_new_project: bool = False,
        refresh_prompt: str = "",
        timeout: int = 120,
    ) -> FlowAuthState:
        """
        Main entry point.

        Priority:
        1. Open existing Flow project and trigger a harmless image request to refresh token.
        2. If no project id is known and create_new_project=True, open Flow home and create a new project.
        """
        profile = self.choose_profile(profile_name)
        if not profile:
            return FlowAuthState(last_error="No enabled flow auth profile configured")

        cached = self.get_cached_state(profile_name=profile.name, project_key=project_key)
        project_id = existing_project_id or cached.project_id
        prompt = refresh_prompt or self.DEFAULT_REFRESH_PROMPT

        try:
            token, final_project_id, error = self._extract_with_selenium(
                profile=profile,
                project_id=project_id,
                create_new_project=create_new_project and not bool(project_id),
                prompt=prompt,
                timeout=timeout,
            )
            if error:
                state = FlowAuthState(
                    token="",
                    project_id=project_id,
                    project_url=profile.project_url_template.format(project_id=project_id) if project_id else "",
                    acquired_at=time.time(),
                    source="selenium",
                    profile_name=profile.name,
                    last_error=error,
                )
                self.save_state(state, profile_name=profile.name, project_key=project_key)
                return state

            state = FlowAuthState(
                token=token,
                project_id=final_project_id,
                project_url=profile.project_url_template.format(project_id=final_project_id) if final_project_id else "",
                acquired_at=time.time(),
                source="selenium",
                profile_name=profile.name,
                last_error="",
            )
            self.save_state(state, profile_name=profile.name, project_key=project_key)
            return state
        except Exception as exc:
            state = FlowAuthState(
                token="",
                project_id=project_id,
                project_url=profile.project_url_template.format(project_id=project_id) if project_id else "",
                acquired_at=time.time(),
                source="selenium",
                profile_name=profile.name,
                last_error=str(exc),
            )
            self.save_state(state, profile_name=profile.name, project_key=project_key)
            return state

    def _extract_with_selenium(
        self,
        profile: FlowAuthProfile,
        project_id: str,
        create_new_project: bool,
        prompt: str,
        timeout: int,
    ) -> Tuple[str, str, str]:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            from selenium.webdriver.common.by import By
            from selenium.webdriver.common.keys import Keys
            from webdriver_manager.chrome import ChromeDriverManager
        except Exception as exc:
            return "", "", f"Missing selenium dependency: {exc}"

        options = Options()
        options.binary_location = profile.chrome_path
        options.add_argument(f"--user-data-dir={profile.user_data_dir}")
        if profile.profile_directory:
            options.add_argument(f"--profile-directory={profile.profile_directory}")
        options.add_argument(f"--remote-debugging-port={random.randint(9222, 9322)}")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1600,1000")
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option("useAutomationExtension", False)

        driver = None
        try:
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
            url = profile.project_url_template.format(project_id=project_id) if project_id else profile.flow_url
            self.log(f"[AUTH] Open Flow: {url}", "INFO")
            driver.get(url)
            time.sleep(6)

            # If redirected to Google login, we intentionally do not automate credentials here.
            # We reuse the chosen Chrome profile. The operator can pre-login once.
            current_url = driver.current_url
            if "accounts.google.com" in current_url:
                return "", project_id, "Chrome profile is not logged in to Google Flow"

            driver.execute_script(self.FLOW_CAPTURE_JS)
            time.sleep(1)

            if create_new_project:
                self.log("[AUTH] Creating new Flow project...", "INFO")
                try:
                    driver.execute_script(self.CREATE_PROJECT_JS)
                except Exception:
                    pass
                time.sleep(5)

            try:
                driver.execute_script(self.SELECT_IMAGE_MODE_JS)
            except Exception:
                pass
            time.sleep(2)

            try:
                driver.execute_script(self.FOCUS_TEXTAREA_JS)
            except Exception:
                pass
            time.sleep(1)

            textarea = None
            for _ in range(8):
                try:
                    textarea = driver.find_element(By.TAG_NAME, "textarea")
                    if textarea:
                        break
                except Exception:
                    time.sleep(1)
            if textarea is None:
                return "", project_id, "Could not find Flow textarea to trigger token refresh"

            textarea.clear()
            textarea.send_keys(prompt)
            textarea.send_keys(Keys.ENTER)
            self.log("[AUTH] Prompt sent to trigger token capture.", "INFO")

            deadline = time.time() + timeout
            last_project_id = project_id
            while time.time() < deadline:
                try:
                    captured = driver.execute_script(
                        "return window.__ve3_auth ? {token: window.__ve3_auth.token || '', project_id: window.__ve3_auth.project_id || '', hits: window.__ve3_auth.hits || []} : null;"
                    )
                except Exception:
                    captured = None
                if captured:
                    token = str(captured.get("token") or "").strip()
                    cap_project = str(captured.get("project_id") or "").strip()
                    if cap_project:
                        last_project_id = cap_project
                    if token and last_project_id:
                        self.log(f"[AUTH] Captured token for project {last_project_id[:8]}...", "SUCCESS")
                        return token, last_project_id, ""
                time.sleep(2)

            return "", last_project_id, "Timed out while waiting for Flow token capture"
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

