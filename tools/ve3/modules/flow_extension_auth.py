"""
Flow Extension Auth — get token/project/media via FlowKit extension API.

Replaces Chrome UI automation (DrissionPage) with simple HTTP calls
to FlowKit agent running on localhost. Extension handles:
- Bearer token capture (from Flow page webRequest)
- reCAPTCHA solving (automatic)
- Project creation (click "New project")
- Image upload (API call through browser context)

Usage:
    auth = FlowExtensionAuth("http://127.0.0.1:8100")
    if auth.is_ready():
        token = auth.get_token()
        project_id = auth.get_project_id()
        media_id = auth.upload_image("path/to/image.png", token, project_id)
"""
import base64
import logging
import time
from pathlib import Path
from typing import Optional, Tuple

try:
    import httpx
except ImportError:
    import requests as httpx

logger = logging.getLogger(__name__)


import threading as _threading

class FlowExtensionAuth:
    """Get Flow auth data via FlowKit Chrome extension API."""

    _agent_proc = None
    _chrome_started = False
    _start_lock = _threading.Lock()

    def __init__(self, agent_url: str = "http://127.0.0.1:8100", log_func=None,
                 chrome_path: str = "", extension_dir: str = "", suite_root: str = ""):
        self.agent_url = agent_url.rstrip("/")
        self._log = log_func or (lambda msg, *a: logger.info(msg))
        self.chrome_path = chrome_path
        self.extension_dir = extension_dir
        self.suite_root = suite_root

    def auto_start(self) -> bool:
        """Auto-start Chrome (login) + agent — same flow as FlowKit GUI."""
        if self.is_ready(timeout=3):
            return True

        with FlowExtensionAuth._start_lock:
            # Re-check after acquiring lock (another thread may have started)
            if self.is_ready(timeout=3):
                return True
            return self._do_auto_start()

    def _do_auto_start(self) -> bool:
        import subprocess, sys, os, time

        suite = Path(self.suite_root) if self.suite_root else Path(__file__).parent.parent.parent
        flowkit_dir = suite / "tools" / "flowkit"
        if not flowkit_dir.exists():
            flowkit_dir = suite / "server" / "flowkit"

        # Find Chrome
        chrome_exe = self.chrome_path
        if not chrome_exe:
            for d in sorted(suite.glob("GoogleChromePortable*")):
                exe = d / "GoogleChromePortable.exe"
                if exe.exists():
                    chrome_exe = str(exe)
                    break
        if not chrome_exe or not Path(chrome_exe).exists():
            self._log("[ExtAuth] Chrome not found")
            return False

        chrome_dir = Path(chrome_exe).parent
        ext_dir = self.extension_dir
        if not ext_dir:
            for d in [suite / "server" / "flowkit" / "flowkit_extensions" / "ext_8100",
                      flowkit_dir / "flowkit_extensions" / "ext_8100"]:
                if d.exists():
                    ext_dir = str(d)
                    break
        if not ext_dir:
            self._log("[ExtAuth] Extension dir not found")
            return False

        self._log("[ExtAuth] Auto-starting (login + agent + Chrome)...")
        sys.path.insert(0, str(flowkit_dir))

        # Account: use _account if set by caller, else read from settings
        account = getattr(self, '_account', None)
        if not account:
            try:
                import yaml
                sf = suite / "tools" / "ve3" / "config" / "settings.yaml"
                if sf.exists():
                    cfg = yaml.safe_load(sf.read_text(encoding="utf-8"))
                    accs = cfg.get("flow_accounts", [])
                    if accs:
                        a = accs[0]
                        account = {"id": a.get("email", ""), "password": a.get("password", ""),
                                   "totp_secret": a.get("totp_secret", "")}
            except Exception:
                pass

        # Fixed ports — must match extension ext_8100 hardcoded ws://127.0.0.1:9222
        debug_port = 19200
        api_port = 8100
        ws_port = 9222

        # Step 1: setup_chrome — login + verify Flow + kill (y het UI mode _bridge_for_account)
        if not FlowExtensionAuth._chrome_started:
            try:
                from chrome_setup import setup_chrome
                from launcher import _write_chrome_prefs

                for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                    try: (chrome_dir / "Data" / "profile" / lock).unlink()
                    except: pass
                _write_chrome_prefs(chrome_dir)

                self._log(f"[ExtAuth] Login {account.get('id','?') if account else '?'} via {chrome_dir.name} (port {debug_port})")
                ok = setup_chrome(
                    chrome_dir=chrome_dir, ext_dir=Path(ext_dir), port=debug_port,
                    account=account, proxy_arg="",
                    log_func=lambda msg: self._log(f"[ExtAuth] {msg}"),
                    instance_name="ve3-main",
                )
                if not ok:
                    self._log("[ExtAuth] setup_chrome FAILED")
                    return False
                self._log("[ExtAuth] Login + Flow OK")
                FlowExtensionAuth._chrome_started = True
            except Exception as e:
                self._log(f"[ExtAuth] Setup error: {e}")
                return False

        # Step 2: Start agent
        if FlowExtensionAuth._agent_proc is None or FlowExtensionAuth._agent_proc.poll() is not None:
            env = os.environ.copy()
            env.update({"API_PORT": str(api_port), "WS_PORT": str(ws_port), "INSTANCE_NAME": "ve3-main"})
            try:
                FlowExtensionAuth._agent_proc = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "agent.main:app",
                     "--host", "127.0.0.1", "--port", str(api_port), "--log-level", "warning"],
                    cwd=str(flowkit_dir), env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=0x08000000)
                self._log(f"[ExtAuth] Agent started: port {api_port}")
                # Update agent_url to match actual port
                self.agent_url = f"http://127.0.0.1:{api_port}"
                time.sleep(3)
            except Exception as e:
                self._log(f"[ExtAuth] Agent error: {e}")
                return False

        # Step 3: Start Chrome subprocess with extension
        args = [
            str(chrome_exe),
            f"--load-extension={ext_dir}",
            f"--remote-debugging-port={debug_port}",
            "--no-first-run", "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "https://labs.google/fx/tools/flow?hl=en",
        ]
        try:
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=0x08000000)
            self._log(f"[ExtAuth] Chrome started (debug={debug_port})")
            time.sleep(8)
        except Exception as e:
            self._log(f"[ExtAuth] Chrome error: {e}")
            return False

        return self.wait_ready(timeout=30)

    def is_ready(self, timeout: int = 5) -> bool:
        """Check if extension is connected and has token."""
        try:
            r = httpx.get(f"{self.agent_url}/health", timeout=timeout)
            if hasattr(r, 'json'):
                data = r.json()
            else:
                data = r.json()
            return data.get("extension_connected", False) and data.get("flow_key_present", False)
        except Exception:
            return False

    def wait_ready(self, timeout: int = 60) -> bool:
        """Wait for extension to be ready."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_ready():
                return True
            time.sleep(3)
        return False

    def get_token(self) -> Optional[str]:
        """Get bearer token from extension."""
        try:
            r = httpx.get(f"{self.agent_url}/api/get-token", timeout=10)
            data = r.json()
            if data.get("success"):
                token = data["token"]
                self._log(f"[ExtAuth] Token: {token[:25]}... ({len(token)} chars)")
                return token
            self._log(f"[ExtAuth] No token: {data.get('error', '?')}")
        except Exception as e:
            self._log(f"[ExtAuth] Token error: {e}")
        return None

    def get_project_id(self) -> Optional[str]:
        """Get current project ID from Flow tab URL."""
        try:
            r = httpx.post(f"{self.agent_url}/api/extract-project-id", json={}, timeout=15)
            data = r.json()
            if data.get("success"):
                pid = data.get("data", {}).get("projectId", "")
                if pid:
                    self._log(f"[ExtAuth] Project: {pid}")
                    return pid
            self._log(f"[ExtAuth] No project ID: {data.get('error', '?')}")
        except Exception as e:
            self._log(f"[ExtAuth] Project ID error: {e}")
        return None

    def ensure_project(self) -> Optional[str]:
        """Create new project if needed, return project ID."""
        try:
            r = httpx.post(f"{self.agent_url}/api/ensure-project", json={}, timeout=60)
            data = r.json()
            if data.get("success"):
                self._log(f"[ExtAuth] Project ensured: {data.get('data', {})}")
                time.sleep(2)
                return self.get_project_id()
            self._log(f"[ExtAuth] Ensure project failed: {data.get('error', '?')}")
        except Exception as e:
            self._log(f"[ExtAuth] Ensure project error: {e}")
        return None

    def upload_image(self, image_path: str, bearer_token: str, project_id: str) -> Optional[str]:
        """Upload image → get media_id."""
        image_path = Path(image_path)
        if not image_path.exists():
            self._log(f"[ExtAuth] Image not found: {image_path}")
            return None

        try:
            with open(image_path, "rb") as f:
                img_bytes = f.read()

            img_b64 = base64.b64encode(img_bytes).decode("utf-8")

            suffix = image_path.suffix.lower()
            mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
            mime_type = mime_map.get(suffix, "image/png")

            r = httpx.post(f"{self.agent_url}/api/upload-image", json={
                "bearer_token": bearer_token,
                "image_base64": img_b64,
                "mime_type": mime_type,
                "project_id": project_id,
            }, timeout=60)
            data = r.json()

            if data.get("success"):
                media_name = data.get("media_name", "")
                if media_name:
                    self._log(f"[ExtAuth] Upload OK: {image_path.name} → {media_name}")
                    return media_name
                self._log(f"[ExtAuth] Upload OK but no media_name")
            else:
                self._log(f"[ExtAuth] Upload failed: {data.get('error', '?')}")
        except Exception as e:
            self._log(f"[ExtAuth] Upload error: {e}")
        return None

    def get_all(self) -> Tuple[Optional[str], Optional[str]]:
        """Get token + project_id in one call."""
        token = self.get_token()
        project_id = self.get_project_id()
        if not project_id and token:
            project_id = self.ensure_project()
        return token, project_id
