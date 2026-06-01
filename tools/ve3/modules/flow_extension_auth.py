"""
Flow Extension Auth — get token/project/media via FlowKit extension API.

Ports per instance:
  sv1: agent 8100 / ws 9222 / debug 19200 / Chrome Copy (1)
  sv2: agent 8101 / ws 9223 / debug 19201 / Chrome Copy (2)
  ...

Startup: parallel with semaphore (y het FlowKit GUI _start_all).
Chrome on-demand: open when needed, close after auth done.
"""
import base64
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional, Tuple, List, Dict

try:
    import httpx
except ImportError:
    import requests as httpx

logger = logging.getLogger(__name__)


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


class _ExtensionInstanceManager:
    _lock = threading.Lock()
    _instances: Dict[str, dict] = {}

    @classmethod
    def is_instance_ready(cls, api_port: int) -> bool:
        try:
            r = httpx.get(f"http://127.0.0.1:{api_port}/health", timeout=3)
            data = r.json()
            return data.get("extension_connected", False) and data.get("flow_key_present", False)
        except Exception:
            return False

    @classmethod
    def _is_agent_alive(cls, api_port: int) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{api_port}/health", timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    @classmethod
    def _kill_on_port(cls, port: int, log=None):
        log = log or (lambda m: None)
        try:
            result = subprocess.run(
                ['netstat', '-ano', '-p', 'TCP'],
                capture_output=True, text=True, timeout=5, creationflags=0x08000000)
            for line in result.stdout.splitlines():
                if f':{port} ' not in line or 'LISTENING' not in line:
                    continue
                pid = line.strip().split()[-1]
                if pid.isdigit() and int(pid) > 0:
                    subprocess.run(['taskkill', '/F', '/T', '/PID', pid],
                                   capture_output=True, timeout=5, creationflags=0x08000000)
                    log(f"[ExtAuth] Killed PID {pid} on port {port}")
                    time.sleep(1)
        except Exception:
            pass

    @classmethod
    def _kill_chrome_for_dir(cls, chrome_dir: Path):
        dir_name = chrome_dir.name
        try:
            subprocess.run(
                ['wmic', 'process', 'where',
                 f"name='chrome.exe' and CommandLine like '%{dir_name}%'",
                 'call', 'terminate'],
                capture_output=True, timeout=10, creationflags=0x08000000)
        except Exception:
            pass
        try:
            subprocess.run(
                ['wmic', 'process', 'where',
                 f"name='GoogleChromePortable.exe' and CommandLine like '%{dir_name}%'",
                 'call', 'terminate'],
                capture_output=True, timeout=10, creationflags=0x08000000)
        except Exception:
            pass

    @classmethod
    def _wait_all_ready(cls, servers: List[dict], timeout: int = 300, log=None):
        log = log or (lambda m: None)
        deadline = time.time() + timeout
        while time.time() < deadline:
            all_ok = True
            for i in range(len(servers)):
                if not cls.is_instance_ready(8100 + i):
                    all_ok = False
                    break
            if all_ok:
                for i, srv in enumerate(servers):
                    cls._instances[srv.get("name", f"sv{i+1}")] = {"api_port": 8100 + i}
                log("[ExtAuth] All instances ready")
                return True
            time.sleep(3)
        return False

    # ─── Startup ───────────────────────────────────────────────

    @classmethod
    def start_all(cls, servers: List[dict], suite_root: str, log_func=None, ready_event=None):
        """Start all instances — PID lock + parallel with semaphore."""
        log = log_func or (lambda m: print(m))
        suite = Path(suite_root)

        all_ready = True
        for i in range(len(servers)):
            if not cls.is_instance_ready(8100 + i):
                all_ready = False
                break
        if all_ready:
            log("[ExtAuth] All instances already running")
            for i, srv in enumerate(servers):
                cls._instances[srv.get("name", f"sv{i+1}")] = {"api_port": 8100 + i}
            if ready_event:
                ready_event.set()
            return

        lock_file = suite / ".extension_startup.lock"
        try:
            if lock_file.exists():
                old_pid = int(lock_file.read_text().strip())
                if _is_pid_alive(old_pid) and old_pid != os.getpid():
                    log(f"[ExtAuth] PID {old_pid} already starting — waiting...")
                    if cls._wait_all_ready(servers, timeout=300, log=log):
                        if ready_event:
                            ready_event.set()
                        return
                    log("[ExtAuth] Wait timeout — taking over")
        except Exception:
            pass

        try:
            lock_file.write_text(str(os.getpid()))
        except Exception:
            pass

        try:
            cls._do_start_all(servers, suite, log, ready_event)
        finally:
            try:
                lock_file.unlink(missing_ok=True)
            except Exception:
                pass

    @classmethod
    def _do_start_all(cls, servers: List[dict], suite: Path, log, ready_event=None):
        """Start instances in PARALLEL — y het FlowKit GUI _start_all."""
        flowkit_dir = suite / "tools" / "flowkit"
        if not flowkit_dir.exists():
            flowkit_dir = suite / "server" / "flowkit"
        sys.path.insert(0, str(flowkit_dir))

        setup_concurrency = max(1, int(os.getenv("CHROME_SETUP_CONCURRENCY", "6")))
        setup_stagger = max(0.0, float(os.getenv("CHROME_SETUP_STAGGER_SEC", "3.0")))
        setup_sem = threading.Semaphore(setup_concurrency)
        ready_count = [0]
        ready_lock = threading.Lock()
        if ready_event is None:
            ready_event = threading.Event()

        pipeline_threads = []
        for i, srv in enumerate(servers):
            t = threading.Thread(
                target=cls._start_single_instance,
                args=(i, srv, suite, flowkit_dir, log, setup_sem, ready_count, ready_lock, ready_event),
                daemon=True,
            )
            t.start()
            pipeline_threads.append(t)
            if setup_stagger > 0 and i < len(servers) - 1:
                time.sleep(setup_stagger)

        for t in pipeline_threads:
            t.join(timeout=180)

        with ready_lock:
            total = ready_count[0]
        log(f"[ExtAuth] Startup done — {total}/{len(servers)} instances ready")

    @classmethod
    def _start_single_instance(cls, index, srv, suite, flowkit_dir, log,
                                setup_sem, ready_count, ready_lock, ready_event):
        """Start ONE instance — runs in its own thread."""
        name = srv.get("name", f"sv{index+1}")
        chrome_path = srv.get("chrome_path", "")
        if not chrome_path or not Path(chrome_path).exists():
            log(f"[ExtAuth] {name}: chrome_path not found, skip")
            return

        chrome_dir = Path(chrome_path).parent
        api_port = 8100 + index
        ws_port = 9222 + index
        debug_port = 19200 + index

        if cls.is_instance_ready(api_port):
            log(f"[ExtAuth] {name}: already ready (port {api_port})")
            cls._instances[name] = {"api_port": api_port, "chrome_dir": str(chrome_dir)}
            with ready_lock:
                ready_count[0] += 1
            ready_event.set()
            return

        ext_dir = suite / "flowkit_extensions" / f"ext_{api_port}"
        if not ext_dir.exists():
            ext_dir = suite / "server" / "flowkit" / "flowkit_extensions" / f"ext_{api_port}"
        if not ext_dir.exists():
            ext_dir = flowkit_dir / "flowkit_extensions" / f"ext_{api_port}"
        if not ext_dir.exists():
            log(f"[ExtAuth] {name}: ext_{api_port} not found, skip")
            return

        bundle = srv.get("flow_account_bundle", "")
        account = None
        if bundle:
            parts = bundle.strip().split("|")
            if len(parts) >= 2:
                account = {"id": parts[0].strip(), "password": parts[1].strip(),
                           "totp_secret": parts[2].strip() if len(parts) >= 3 else ""}

        log(f"[ExtAuth] {name}: starting (port {api_port}/{ws_port}/{debug_port})")

        # Kill old
        cls._kill_on_port(api_port, log)
        cls._kill_on_port(ws_port, log)
        cls._kill_chrome_for_dir(chrome_dir)
        time.sleep(2)

        # Step 1: setup_chrome — semaphore limits concurrency (y het FlowKit)
        with setup_sem:
            try:
                from chrome_setup import setup_chrome
                from launcher import _write_chrome_prefs

                for lk in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                    try:
                        (chrome_dir / "Data" / "profile" / lk).unlink()
                    except Exception:
                        pass
                _write_chrome_prefs(chrome_dir)

                ok = setup_chrome(
                    chrome_dir=chrome_dir, ext_dir=ext_dir, port=debug_port,
                    account=account, proxy_arg="",
                    log_func=lambda msg, n=name: log(f"[ExtAuth] [{n}] {msg}"),
                    instance_name=name,
                )
                if not ok:
                    log(f"[ExtAuth] {name}: setup_chrome FAILED")
                    return
            except Exception as e:
                log(f"[ExtAuth] {name}: setup error: {e}")
                return

        # Step 2: Start agent + wait ready
        cls._kill_on_port(api_port, log)
        cls._kill_on_port(ws_port, log)
        time.sleep(1)

        env = os.environ.copy()
        env.update({"API_PORT": str(api_port), "WS_PORT": str(ws_port), "INSTANCE_NAME": name})
        try:
            agent_proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "agent.main:app",
                 "--host", "127.0.0.1", "--port", str(api_port), "--log-level", "warning"],
                cwd=str(flowkit_dir), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        except Exception as e:
            log(f"[ExtAuth] {name}: agent error: {e}")
            return

        agent_ready = False
        for _ in range(20):
            if cls._is_agent_alive(api_port):
                agent_ready = True
                break
            time.sleep(1)
        if agent_ready:
            log(f"[ExtAuth] {name}: Agent ready (port {api_port})")
        else:
            log(f"[ExtAuth] {name}: Agent not ready after 20s")

        # Step 3: Start Chrome
        profile_dir = chrome_dir / "Data" / "profile"
        sw_cache = profile_dir / "Default" / "Service Worker"
        if sw_cache.exists():
            try:
                shutil.rmtree(sw_cache)
            except Exception:
                pass
        for lk in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (profile_dir / lk).unlink()
            except Exception:
                pass
        try:
            from launcher import _write_chrome_prefs
            _write_chrome_prefs(chrome_dir)
        except Exception:
            pass

        args = [
            str(chrome_path),
            f"--load-extension={ext_dir}",
            f"--remote-debugging-port={debug_port}",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "--no-first-run",
            "--no-default-browser-check",
            "https://labs.google/fx/tools/flow",
        ]
        try:
            chrome_proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            time.sleep(8)
        except Exception as e:
            log(f"[ExtAuth] {name}: Chrome error: {e}")
            return

        # Step 4: Apply CDP
        try:
            from chrome_setup import apply_chrome_cdp
            apply_chrome_cdp(
                debug_port=debug_port, ext_dir=ext_dir, instance_name=name,
                log_func=lambda msg, n=name: log(f"[ExtAuth] [{n}] {msg}"),
            )
        except Exception as e:
            log(f"[ExtAuth] {name}: CDP error: {e}")

        # Step 5: Wait extension connect + token
        ready = False
        for _ in range(30):
            if cls.is_instance_ready(api_port):
                ready = True
                break
            time.sleep(2)

        if ready:
            cls._instances[name] = {"api_port": api_port, "agent_proc": agent_proc,
                                    "chrome_proc": chrome_proc, "chrome_dir": str(chrome_dir),
                                    "ext_dir": str(ext_dir), "debug_port": debug_port}
            log(f"[ExtAuth] {name}: READY (port {api_port})")
            with ready_lock:
                ready_count[0] += 1
            ready_event.set()
        else:
            try:
                h = httpx.get(f"http://127.0.0.1:{api_port}/health", timeout=3).json()
                if h.get("extension_connected"):
                    cls._instances[name] = {"api_port": api_port, "agent_proc": agent_proc,
                                            "chrome_proc": chrome_proc, "chrome_dir": str(chrome_dir),
                                            "ext_dir": str(ext_dir), "debug_port": debug_port}
                    log(f"[ExtAuth] {name}: connected, token pending")
                    with ready_lock:
                        ready_count[0] += 1
                    ready_event.set()
                else:
                    log(f"[ExtAuth] {name}: extension NOT connected")
            except Exception:
                log(f"[ExtAuth] {name}: agent unreachable")

    # ─── Port lookups ──────────────────────────────────────────

    @classmethod
    def get_agent_port(cls, server_name: str) -> Optional[int]:
        inst = cls._instances.get(server_name)
        if inst:
            return inst["api_port"]
        return None

    @classmethod
    def get_agent_port_by_index(cls, server_index: int) -> Optional[int]:
        port = 8100 + server_index
        if cls.is_instance_ready(port):
            return port
        return None

    # ─── On-demand start for single server ─────────────────────

    @classmethod
    def start_one(cls, server_index: int, srv: dict, suite_root: str, log=None):
        """Start a SINGLE instance on-demand with correct port. No PID lock needed."""
        log = log or (lambda m: print(m))
        suite = Path(suite_root)
        api_port = 8100 + server_index

        if cls.is_instance_ready(api_port):
            name = srv.get("name", f"sv{server_index+1}")
            cls._instances[name] = {"api_port": api_port}
            return True

        flowkit_dir = suite / "tools" / "flowkit"
        if not flowkit_dir.exists():
            flowkit_dir = suite / "server" / "flowkit"
        sys.path.insert(0, str(flowkit_dir))

        dummy_sem = threading.Semaphore(1)
        dummy_count = [0]
        dummy_lock = threading.Lock()
        dummy_event = threading.Event()
        cls._start_single_instance(
            server_index, srv, suite, flowkit_dir, log,
            dummy_sem, dummy_count, dummy_lock, dummy_event,
        )
        return cls.is_instance_ready(api_port) or cls._is_agent_alive(api_port)

    # ─── Chrome lifecycle ──────────────────────────────────────

    @classmethod
    def release_chrome(cls, server_name: str, log=None):
        """Kill Chrome — agent keeps token in RAM."""
        log = log or (lambda m: None)
        inst = cls._instances.get(server_name)
        if not inst or inst.get("chrome_released"):
            return
        chrome_dir = inst.get("chrome_dir")
        if chrome_dir:
            cls._kill_chrome_for_dir(Path(chrome_dir))
            inst["chrome_released"] = True
            log(f"[ExtAuth] {server_name}: Chrome released (agent keeps token)")

    @classmethod
    def reopen_chrome_for_instance(cls, server_name: str, suite_root: str, log=None):
        """Reopen Chrome for instance — agent already running, just reconnect extension."""
        log = log or (lambda m: None)
        inst = cls._instances.get(server_name)
        if not inst:
            log(f"[ExtAuth] {server_name}: no instance info, cannot reopen")
            return False

        chrome_dir = Path(inst.get("chrome_dir", ""))
        ext_dir = Path(inst.get("ext_dir", ""))
        debug_port = inst.get("debug_port", 0)
        api_port = inst.get("api_port", 0)

        if not chrome_dir.exists() or not ext_dir.exists() or not debug_port:
            log(f"[ExtAuth] {server_name}: missing config for reopen")
            return False

        log(f"[ExtAuth] {server_name}: reopening Chrome...")

        # Kill leftover Chrome
        cls._kill_chrome_for_dir(chrome_dir)
        time.sleep(2)

        # Clear locks + SW cache
        profile_dir = chrome_dir / "Data" / "profile"
        for lk in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (profile_dir / lk).unlink()
            except Exception:
                pass
        sw_cache = profile_dir / "Default" / "Service Worker"
        if sw_cache.exists():
            try:
                shutil.rmtree(sw_cache)
            except Exception:
                pass

        # Check if agent still alive, if not restart
        if not cls._is_agent_alive(api_port):
            log(f"[ExtAuth] {server_name}: agent dead, need full restart")
            return False

        # Launch Chrome with extension
        chrome_path = chrome_dir / "GoogleChromePortable.exe"
        if not chrome_path.exists():
            for f in chrome_dir.glob("*.exe"):
                if "portable" in f.name.lower() or "chrome" in f.name.lower():
                    chrome_path = f
                    break

        args = [
            str(chrome_path),
            f"--load-extension={ext_dir}",
            f"--remote-debugging-port={debug_port}",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "--no-first-run",
            "--no-default-browser-check",
            "https://labs.google/fx/tools/flow",
        ]
        try:
            chrome_proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            time.sleep(8)
        except Exception as e:
            log(f"[ExtAuth] {server_name}: Chrome reopen error: {e}")
            return False

        # Apply CDP
        try:
            sys.path.insert(0, str(Path(suite_root) / "server" / "flowkit"))
            sys.path.insert(0, str(Path(suite_root) / "tools" / "flowkit"))
            from chrome_setup import apply_chrome_cdp
            apply_chrome_cdp(
                debug_port=debug_port, ext_dir=ext_dir, instance_name=server_name,
                log_func=lambda msg, n=server_name: log(f"[ExtAuth] [{n}] {msg}"),
            )
        except Exception:
            pass

        # Wait extension reconnect + token
        ready = False
        for _ in range(30):
            if cls.is_instance_ready(api_port):
                ready = True
                break
            time.sleep(2)

        if ready:
            inst["chrome_proc"] = chrome_proc
            inst["chrome_released"] = False
            log(f"[ExtAuth] {server_name}: Chrome reopened — READY")
            return True
        else:
            log(f"[ExtAuth] {server_name}: Chrome reopened but extension not connected")
            inst["chrome_proc"] = chrome_proc
            inst["chrome_released"] = False
            return cls._is_agent_alive(api_port)


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
    def start_all_instances(servers: list, suite_root: str, log_func=None, ready_event=None):
        _ExtensionInstanceManager.start_all(servers, suite_root, log_func, ready_event)

    @staticmethod
    def get_agent_url_for_server(server_name: str) -> Optional[str]:
        port = _ExtensionInstanceManager.get_agent_port(server_name)
        if port:
            return f"http://127.0.0.1:{port}"
        return None

    @staticmethod
    def get_agent_url_by_index(server_index: int) -> Optional[str]:
        port = _ExtensionInstanceManager.get_agent_port_by_index(server_index)
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
