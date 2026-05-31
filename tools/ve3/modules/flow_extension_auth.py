"""
Flow Extension Auth — get token/project/media via FlowKit extension API.

Multi-instance: each server (sv1, sv2...) gets its own Chrome + agent,
exactly like FlowKit GUI. Ports per instance:
  sv1: Chrome Copy (1) + ext_8100 + agent 8100/ws 9222 + debug 19200
  sv2: Chrome Copy (2) + ext_8101 + agent 8101/ws 9223 + debug 19201
  ...

Startup runs ONCE (from VE3 GUI or first worker), sets up ALL instances.
Workers call the correct agent based on their server binding.
"""
import base64
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Tuple, List, Dict

try:
    import httpx
except ImportError:
    import requests as httpx

logger = logging.getLogger(__name__)


# ─── Global instance manager (shared across all workers) ──────

class _ExtensionInstanceManager:
    """Manages Chrome + agent instances — y het FlowKit GUI."""

    _lock = threading.Lock()
    _started = False
    _instances: Dict[str, dict] = {}  # server_name → {api_port, agent_proc, chrome_proc}

    @classmethod
    def is_instance_ready(cls, api_port: int) -> bool:
        try:
            r = httpx.get(f"http://127.0.0.1:{api_port}/health", timeout=3)
            data = r.json() if hasattr(r, 'json') else r.json()
            return data.get("extension_connected", False) and data.get("flow_key_present", False)
        except Exception:
            return False

    @classmethod
    def start_all(cls, servers: List[dict], suite_root: str, log_func=None):
        """Start ALL server instances — y het FlowKit GUI _start_all."""
        with cls._lock:
            if cls._started:
                return
            cls._started = True

        log = log_func or (lambda m: print(m))
        suite = Path(suite_root)
        flowkit_dir = suite / "tools" / "flowkit"
        if not flowkit_dir.exists():
            flowkit_dir = suite / "server" / "flowkit"

        sys.path.insert(0, str(flowkit_dir))

        for i, srv in enumerate(servers):
            name = srv.get("name", f"sv{i+1}")
            chrome_path = srv.get("chrome_path", "")
            if not chrome_path or not Path(chrome_path).exists():
                continue

            chrome_dir = Path(chrome_path).parent
            bundle = srv.get("flow_account_bundle", "")
            account = None
            if bundle:
                parts = bundle.strip().split("|")
                if len(parts) >= 2:
                    account = {
                        "id": parts[0].strip(),
                        "password": parts[1].strip(),
                        "totp_secret": parts[2].strip() if len(parts) >= 3 else "",
                    }

            api_port = 8100 + i
            ws_port = 9222 + i
            debug_port = 19200 + i
            ext_dir = suite / "server" / "flowkit" / "flowkit_extensions" / f"ext_{api_port}"
            if not ext_dir.exists():
                ext_dir = flowkit_dir / "flowkit_extensions" / f"ext_{api_port}"
            if not ext_dir.exists():
                log(f"[ExtAuth] {name}: ext_{api_port} not found, skip")
                continue

            # Check if already running
            if cls.is_instance_ready(api_port):
                log(f"[ExtAuth] {name}: already running on port {api_port}")
                cls._instances[name] = {"api_port": api_port}
                continue

            log(f"[ExtAuth] {name}: starting ({chrome_dir.name}, port {debug_port}, account {account['id'] if account else '?'})")

            # Step 1: setup_chrome — login + verify Flow + kill (y het FlowKit)
            try:
                from chrome_setup import setup_chrome
                from launcher import _write_chrome_prefs

                for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                    try: (chrome_dir / "Data" / "profile" / lock_name).unlink()
                    except: pass
                _write_chrome_prefs(chrome_dir)

                ok = setup_chrome(
                    chrome_dir=chrome_dir, ext_dir=ext_dir, port=debug_port,
                    account=account, proxy_arg="",
                    log_func=lambda msg, n=name: log(f"[ExtAuth] [{n}] {msg}"),
                    instance_name=name,
                )
                if not ok:
                    log(f"[ExtAuth] {name}: setup_chrome FAILED")
                    continue
            except Exception as e:
                log(f"[ExtAuth] {name}: setup error: {e}")
                continue

            # Step 2: Start agent (y het FlowKit GUI _start_agent)
            env = os.environ.copy()
            env.update({"API_PORT": str(api_port), "WS_PORT": str(ws_port), "INSTANCE_NAME": name})
            try:
                agent_proc = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "agent.main:app",
                     "--host", "127.0.0.1", "--port", str(api_port), "--log-level", "warning"],
                    cwd=str(flowkit_dir), env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=0x08000000)
                time.sleep(3)
            except Exception as e:
                log(f"[ExtAuth] {name}: agent error: {e}")
                continue

            # Step 3: Start Chrome subprocess with extension (y het FlowKit GUI _start_chrome)
            args = [
                str(chrome_path),
                f"--load-extension={ext_dir}",
                f"--remote-debugging-port={debug_port}",
                "--no-first-run", "--no-default-browser-check",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "https://labs.google/fx/tools/flow?hl=en",
            ]
            try:
                chrome_proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                               creationflags=0x08000000)
                time.sleep(5)
            except Exception as e:
                log(f"[ExtAuth] {name}: Chrome error: {e}")
                continue

            # Wait for extension connect
            ready = False
            for _ in range(15):
                if cls.is_instance_ready(api_port):
                    ready = True
                    break
                time.sleep(2)

            if ready:
                cls._instances[name] = {"api_port": api_port, "agent_proc": agent_proc, "chrome_proc": chrome_proc}
                log(f"[ExtAuth] {name}: READY (port {api_port})")
            else:
                log(f"[ExtAuth] {name}: extension not connected after 30s")

    @classmethod
    def get_agent_port(cls, server_name: str) -> Optional[int]:
        inst = cls._instances.get(server_name)
        if inst:
            return inst["api_port"]
        return None


# ─── FlowExtensionAuth — per-project auth client ─────────────

class FlowExtensionAuth:
    """Get Flow auth data via correct server's agent API."""

    def __init__(self, agent_url: str = "http://127.0.0.1:8100", log_func=None,
                 chrome_path: str = "", extension_dir: str = "", suite_root: str = ""):
        self.agent_url = agent_url.rstrip("/")
        self._log = log_func or (lambda msg, *a: logger.info(msg))
        self.chrome_path = chrome_path
        self.extension_dir = extension_dir
        self.suite_root = suite_root

    @staticmethod
    def start_all_instances(servers: list, suite_root: str, log_func=None):
        """Start ALL extension instances — call from VE3 GUI once."""
        _ExtensionInstanceManager.start_all(servers, suite_root, log_func)

    @staticmethod
    def get_agent_url_for_server(server_name: str) -> Optional[str]:
        port = _ExtensionInstanceManager.get_agent_port(server_name)
        if port:
            return f"http://127.0.0.1:{port}"
        return None

    def is_ready(self, timeout: int = 5) -> bool:
        try:
            r = httpx.get(f"{self.agent_url}/health", timeout=timeout)
            data = r.json()
            return data.get("extension_connected", False) and data.get("flow_key_present", False)
        except Exception:
            return False

    def wait_ready(self, timeout: int = 60) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_ready():
                return True
            time.sleep(3)
        return False

    def get_token(self) -> Optional[str]:
        try:
            r = httpx.get(f"{self.agent_url}/api/get-token", timeout=10)
            data = r.json()
            if data.get("success"):
                return data["token"]
        except Exception as e:
            self._log(f"[ExtAuth] Token error: {e}")
        return None

    def get_project_id(self) -> Optional[str]:
        try:
            r = httpx.post(f"{self.agent_url}/api/extract-project-id", json={}, timeout=15)
            data = r.json()
            if data.get("success"):
                return data.get("data", {}).get("projectId", "")
        except Exception as e:
            self._log(f"[ExtAuth] Project ID error: {e}")
        return None

    def ensure_project(self) -> Optional[str]:
        try:
            r = httpx.post(f"{self.agent_url}/api/ensure-project", json={}, timeout=60)
            data = r.json()
            if data.get("success"):
                time.sleep(2)
                return self.get_project_id()
        except Exception as e:
            self._log(f"[ExtAuth] Ensure project error: {e}")
        return None

    def upload_image(self, image_path: str, bearer_token: str, project_id: str) -> Optional[str]:
        image_path = Path(image_path)
        if not image_path.exists():
            return None
        try:
            with open(image_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")
            suffix = image_path.suffix.lower()
            mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
            r = httpx.post(f"{self.agent_url}/api/upload-image", json={
                "bearer_token": bearer_token, "image_base64": img_b64,
                "mime_type": mime_map.get(suffix, "image/png"), "project_id": project_id,
            }, timeout=60)
            data = r.json()
            if data.get("success"):
                return data.get("media_name", "")
        except Exception as e:
            self._log(f"[ExtAuth] Upload error: {e}")
        return None

    def get_all(self) -> Tuple[Optional[str], Optional[str]]:
        token = self.get_token()
        project_id = self.get_project_id()
        if not project_id and token:
            project_id = self.ensure_project()
        return token, project_id
