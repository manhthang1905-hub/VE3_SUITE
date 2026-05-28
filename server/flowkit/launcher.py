"""
FlowKit Launcher — starts Chrome instances, agents, and gateway.
Supports fingerprint injection and Chrome restart for 403 recovery.

Usage:
    python launcher.py              # Start all enabled instances + gateway
    python launcher.py --gateway    # Start only gateway (agents already running)
    python launcher.py --agent 1    # Start only agent #1
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import yaml

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.yaml"

if not CONFIG_PATH.exists():
    print(f"[FATAL] config.yaml not found: {CONFIG_PATH}")
    sys.exit(1)
with open(CONFIG_PATH) as f:
    CONFIG = yaml.safe_load(f)


# ─── Screen / Window Layout ────────────────────────────────

def _get_screen_size() -> tuple[int, int]:
    """Get usable screen area (excluding taskbar)."""
    try:
        import ctypes
        import ctypes.wintypes
        rect = ctypes.wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w > 0 and h > 0:
            return w, h
        user32 = ctypes.windll.user32
        return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    except Exception:
        return 1920, 1040


def _calc_chrome_layout(slot: int, total_slots: int) -> tuple[int, int, int, int]:
    """Calculate (x, y, width, height) for Chrome window at given slot.

    Layout: GUI occupies left column, Chrome windows fill remaining
    space in a grid (cols x rows).
    """
    scr_w, scr_h = _get_screen_size()

    layout = CONFIG.get("chrome_layout", {})
    gui_width = layout.get("gui_width", 700)
    cols = layout.get("cols", 3)
    rows = layout.get("rows", 0)

    if rows <= 0:
        rows = max(1, -(-total_slots // cols))  # ceil division

    usable_w = max(300, scr_w - gui_width)
    cell_w = max(320, usable_w // cols)
    cell_h = max(180, scr_h // rows)

    col = slot % cols
    row = slot // cols
    x = gui_width + col * cell_w
    y = row * cell_h

    return x, y, cell_w, cell_h


def _resolve_chrome_slot(instance_name: str) -> int:
    """Map instance name to slot index (0-based).

    Derives slot from the instance number: flowkit-1 -> 0, flowkit-2 -> 1, etc.
    """
    import re
    m = re.search(r"(\d+)$", instance_name)
    if m:
        return int(m.group(1)) - 1
    return 0


def resolve_path(rel_path: str) -> Path:
    """Resolve path relative to flowkit directory."""
    return (BASE_DIR / rel_path).resolve()


# ─── Fingerprint ─────────────────────────────────────────────

def generate_fingerprint(ext_dir: str | Path, instance_name: str = "") -> int:
    """Generate unique fingerprint JS and write to extension directory.

    Returns the seed used.
    """
    from fingerprint_data import build_fingerprint_js, get_unique_seed

    ext_dir = Path(ext_dir)
    seed = get_unique_seed()
    js_code = build_fingerprint_js(seed)

    fp_path = ext_dir / "fp_inject.js"
    fp_path.write_text(js_code, encoding="utf-8")

    seed_path = BASE_DIR / "config" / f".fingerprint_seed_{seed}"
    seed_path.parent.mkdir(parents=True, exist_ok=True)
    seed_path.write_text(
        f"{instance_name}|{seed}|{int(time.time())}",
        encoding="utf-8",
    )

    print(f"  Fingerprint: seed={seed} -> {fp_path.name}")
    return seed


# ─── Chrome Process Management ───────────────────────────────

_chrome_processes: Dict[str, subprocess.Popen] = {}
_chrome_seeds: Dict[str, int] = {}


def clean_chrome_profile(chrome_dir: Path):
    """Deep clean Chrome profile — delete everything except Secure Preferences.

    Secure Preferences contains extension registration (location:4).
    Everything else is recreated by Chrome on startup.
    """
    import json as _json
    import shutil as _shutil
    import math as _math

    default_dir = chrome_dir / "Data" / "profile" / "Default"
    if not default_dir.exists():
        return

    sec_prefs_file = default_dir / "Secure Preferences"
    sec_prefs_data = None
    if sec_prefs_file.exists():
        try:
            sec_prefs_data = sec_prefs_file.read_text(encoding="utf-8")
        except Exception:
            pass

    for item in default_dir.iterdir():
        try:
            if item.is_dir():
                _shutil.rmtree(item)
            else:
                item.unlink()
        except Exception:
            pass

    if sec_prefs_data:
        sec_prefs_file.write_text(sec_prefs_data, encoding="utf-8")

    print(f"  Profile cleaned: kept only Secure Preferences")


def _write_chrome_prefs(chrome_dir: Path):
    """Write fresh Preferences with session restore prevention. Remove any zoom data."""
    import json as _json

    prefs_file = chrome_dir / "Data" / "profile" / "Default" / "Preferences"
    prefs_file.parent.mkdir(parents=True, exist_ok=True)

    prefs = {}
    if prefs_file.exists():
        try:
            prefs = _json.loads(prefs_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    prefs.setdefault("profile", {})["exit_type"] = "Normal"
    prefs["profile"]["exited_cleanly"] = True
    prefs.setdefault("session", {})["restore_on_startup"] = 5
    # Remove Chrome-level zoom — zoom is handled by CSS in fp_inject.js
    prefs.pop("partition", None)
    prefs_file.write_text(_json.dumps(prefs, ensure_ascii=False), encoding="utf-8")


def start_chrome(instance: dict, new_fingerprint: bool = True, clean: bool = False) -> Optional[subprocess.Popen]:
    """Start Chrome Portable with extension loaded.

    Uses GoogleChromePortable.exe wrapper (not chrome.exe directly)
    because the wrapper correctly handles profile setup and MV3
    service worker activation.

    clean=True: deep clean profile (delete all except Secure Preferences)
    """
    chrome_dir = resolve_path(instance["chrome_path"]).parent.parent.parent
    portable_exe = chrome_dir / "GoogleChromePortable.exe"
    ext_dir = resolve_path(instance["extension_dir"])
    ipv6 = instance.get("ipv6", "")
    name = instance["name"]

    if not portable_exe.exists():
        print(f"[ERROR] ChromePortable not found: {portable_exe}")
        return None

    if clean:
        clean_chrome_profile(chrome_dir)

    _write_chrome_prefs(chrome_dir)

    if new_fingerprint:
        seed = generate_fingerprint(ext_dir, name)
        _chrome_seeds[name] = seed

    debug_port = 19200 + (instance["api_port"] - 8100)
    args = [
        str(portable_exe),
        f"--load-extension={ext_dir}",
        f"--remote-debugging-port={debug_port}",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    # Window layout
    enabled_instances = [i for i in CONFIG.get("instances", []) if i.get("enabled", True)]
    slot = _resolve_chrome_slot(name)
    total = len(enabled_instances)
    x, y, w, h = _calc_chrome_layout(slot, total)
    args.append(f"--window-position={x},{y}")
    args.append(f"--window-size={w},{h}")

    if ipv6:
        proxy_port = 1081 + (instance["api_port"] - 8100)
        args.append(f"--proxy-server=socks5://127.0.0.1:{proxy_port}")

    args.append("https://labs.google/fx/tools/flow")

    print(f"[{name}] Starting Chrome: {portable_exe.name}")
    print(f"  Dir: {chrome_dir}")
    print(f"  Extension: {ext_dir}")
    print(f"  Window: slot={slot} pos=({x},{y}) size={w}x{h}")
    if ipv6:
        print(f"  IPv6 via local proxy: 127.0.0.1:{proxy_port}")

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    print(f"  PID: {proc.pid}")

    _chrome_processes[name] = proc
    return proc


def arrange_chrome_windows(instances: list = None):
    """Position all Chrome windows in grid layout using Win32 API.

    Matches Chrome windows to instances by extension dir in command line.
    """
    if sys.platform != "win32":
        return

    import ctypes
    import ctypes.wintypes

    if instances is None:
        instances = [i for i in CONFIG.get("instances", []) if i.get("enabled", True)]

    total = len(instances)
    if total == 0:
        return

    ext_to_slot = {}
    for inst in instances:
        name = inst["name"]
        ext_dir = inst.get("extension_dir", "")
        slot = _resolve_chrome_slot(name)
        ext_marker = ext_dir.replace("/", "\\").split("\\")[-1] if ext_dir else ""
        chrome_dir_name = resolve_path(inst["chrome_path"]).parent.parent.parent.name
        ext_to_slot[name] = {
            "slot": slot,
            "ext_marker": ext_marker,
            "chrome_dir": chrome_dir_name,
        }

    pid_to_name = {}
    try:
        result = subprocess.run(
            ["wmic", "process", "where", "name='chrome.exe'",
             "get", "processid,commandline"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.split("\n"):
            line = line.strip()
            if not line or "ProcessId" in line:
                continue
            for name, info in ext_to_slot.items():
                marker = info["ext_marker"]
                chrome_dir = info["chrome_dir"]
                if marker and marker in line:
                    parts = line.rsplit(None, 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        pid_to_name[int(parts[1])] = name
                elif chrome_dir and chrome_dir in line:
                    parts = line.rsplit(None, 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        pid_to_name.setdefault(int(parts[1]), name)
    except Exception as e:
        print(f"[Layout] WMIC error: {e}")
        return

    if not pid_to_name:
        print("[Layout] No Chrome processes found to arrange")
        return

    user32 = ctypes.windll.user32
    EnumWindows = user32.EnumWindows
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    IsWindowVisible = user32.IsWindowVisible
    SetWindowPos = user32.SetWindowPos
    HWND_TOP = 0
    SWP_SHOWWINDOW = 0x0040

    arranged = set()
    windows = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def enum_cb(hwnd, lparam):
        if not IsWindowVisible(hwnd):
            return True
        pid = ctypes.wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_val = pid.value
        if pid_val in pid_to_name:
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                windows.append((hwnd, pid_val))
        return True

    EnumWindows(enum_cb, 0)

    for hwnd, pid_val in windows:
        name = pid_to_name.get(pid_val)
        if not name or name in arranged:
            continue
        info = ext_to_slot.get(name)
        if not info:
            continue
        x, y, w, h = _calc_chrome_layout(info["slot"], total)
        SetWindowPos(hwnd, HWND_TOP, x, y, w, h, SWP_SHOWWINDOW)
        arranged.add(name)
        print(f"  [Layout] {name}: pos=({x},{y}) size={w}x{h}")

    print(f"[Layout] Arranged {len(arranged)}/{total} Chrome windows")


def kill_chrome(instance_name: str) -> bool:
    """Kill Chrome process for a specific instance.

    GoogleChromePortable.exe is a wrapper that exits after launching chrome.exe.
    So we kill by finding chrome.exe processes whose command line includes
    this instance's extension directory (unique per instance).
    """
    killed_any = False

    # Method 1: Kill tracked Popen if still running
    proc = _chrome_processes.get(instance_name)
    if proc and proc.poll() is None:
        print(f"[{instance_name}] Killing wrapper PID {proc.pid}...")
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=10,
                )
            else:
                proc.terminate()
                proc.wait(timeout=5)
            killed_any = True
        except Exception:
            pass

    # Method 2: Find chrome.exe by extension dir in command line (Windows)
    if sys.platform == "win32":
        # Get the extension dir for this instance from config
        cfg = CONFIG.get("instances", [])
        ext_marker = ""
        for inst_cfg in cfg:
            if inst_cfg.get("name") == instance_name:
                ext_marker = inst_cfg.get("extension_dir", "")
                break

        if ext_marker:
            try:
                result = subprocess.run(
                    ["wmic", "process", "where",
                     f"name='chrome.exe' and commandline like '%{ext_marker}%'",
                     "get", "processid"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in result.stdout.strip().split("\n"):
                    line = line.strip()
                    if line.isdigit():
                        pid = int(line)
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(pid)],
                            capture_output=True, timeout=10,
                        )
                        killed_any = True
                        print(f"[{instance_name}] Killed chrome.exe PID {pid}")
            except Exception as e:
                print(f"[{instance_name}] WMIC kill error: {e}")

        if not killed_any:
            # Method 3: Kill by Chrome profile directory
            chrome_path = ""
            for inst_cfg in cfg:
                if inst_cfg.get("name") == instance_name:
                    chrome_path = inst_cfg.get("chrome_path", "")
                    break
            if chrome_path:
                chrome_dir = str(resolve_path(chrome_path).parent.parent.parent)
                profile_marker = chrome_dir.replace("\\", "/").split("/")[-1]
                try:
                    result = subprocess.run(
                        ["wmic", "process", "where",
                         f"name='chrome.exe' and commandline like '%{profile_marker}%'",
                         "get", "processid"],
                        capture_output=True, text=True, timeout=10,
                    )
                    for line in result.stdout.strip().split("\n"):
                        line = line.strip()
                        if line.isdigit():
                            pid = int(line)
                            subprocess.run(
                                ["taskkill", "/F", "/T", "/PID", str(pid)],
                                capture_output=True, timeout=10,
                            )
                            killed_any = True
                            print(f"[{instance_name}] Killed chrome.exe PID {pid} (by profile)")
                except Exception as e:
                    print(f"[{instance_name}] Profile kill error: {e}")

    _chrome_processes.pop(instance_name, None)
    return killed_any


def restart_chrome(instance: dict, new_ipv6: str = "", clean: bool = True) -> Optional[subprocess.Popen]:
    """Kill Chrome and restart with new fingerprint + clean profile.

    If new_ipv6 is provided, Chrome starts with that IPv6 proxy.
    clean=True: deep clean profile (keeps only Secure Preferences for extension).
    """
    name = instance["name"]
    print(f"[{name}] === RESTART CHROME (recovery, clean={clean}) ===")

    kill_chrome(name)
    time.sleep(3)

    if new_ipv6:
        instance = {**instance, "ipv6": new_ipv6}

    return start_chrome(instance, new_fingerprint=True, clean=clean)


def get_chrome_pid(instance_name: str) -> Optional[int]:
    """Get Chrome PID for an instance (None if not running)."""
    proc = _chrome_processes.get(instance_name)
    if proc and proc.poll() is None:
        return proc.pid
    return None


def get_instance_seed(instance_name: str) -> int:
    """Get current fingerprint seed for an instance."""
    return _chrome_seeds.get(instance_name, 0)


# ─── Agent ───────────────────────────────────────────────────

def start_agent(instance: dict) -> subprocess.Popen:
    """Start FlowKit agent for an instance."""
    api_port = instance["api_port"]
    ws_port = instance["ws_port"]
    name = instance["name"]

    env = os.environ.copy()
    env["API_PORT"] = str(api_port)
    env["WS_PORT"] = str(ws_port)
    env["API_HOST"] = "127.0.0.1"
    env["WS_HOST"] = "127.0.0.1"
    env["INSTANCE_NAME"] = name

    print(f"[{name}] Starting agent: API={api_port}, WS={ws_port}")

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agent.main:app",
         "--host", "127.0.0.1", "--port", str(api_port),
         "--log-level", "info"],
        env=env,
        cwd=str(BASE_DIR),
        stdout=sys.stdout,
        stderr=sys.stderr,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    print(f"  PID: {proc.pid}")
    return proc


# ─── Gateway ─────────────────────────────────────────────────

def start_gateway() -> subprocess.Popen:
    """Start the gateway."""
    gateway_port = CONFIG.get("gateway_port", 5100)
    print(f"[Gateway] Starting on port {gateway_port}")

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "gateway:app",
         "--host", "0.0.0.0", "--port", str(gateway_port),
         "--log-level", "info"],
        cwd=str(BASE_DIR),
        stdout=sys.stdout,
        stderr=sys.stderr,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    print(f"  PID: {proc.pid}")
    return proc


# ─── Main ────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="FlowKit Server Launcher")
    parser.add_argument("--gateway", action="store_true", help="Start only gateway")
    parser.add_argument("--agent", type=int, help="Start only agent N (1-based)")
    parser.add_argument("--no-chrome", action="store_true", help="Don't start Chrome (already running)")
    parser.add_argument("--no-gateway", action="store_true", help="Don't start gateway")
    args = parser.parse_args()

    processes = []
    instances_cfg = [i for i in CONFIG.get("instances", []) if i.get("enabled", True)]

    if args.gateway:
        proc = start_gateway()
        if proc:
            processes.append(proc)
    elif args.agent:
        idx = args.agent - 1
        if 0 <= idx < len(instances_cfg):
            inst = instances_cfg[idx]
            if not args.no_chrome:
                chrome_proc = start_chrome(inst)
                if chrome_proc:
                    processes.append(chrome_proc)
                time.sleep(3)
            proc = start_agent(inst)
            if proc:
                processes.append(proc)
        else:
            print(f"[ERROR] Agent {args.agent} not found (have {len(instances_cfg)} instances)")
            sys.exit(1)
    else:
        # Start all
        print("=" * 60)
        print("  FlowKit Server — Starting all components")
        print("=" * 60)
        print(f"  Instances: {len(instances_cfg)}")
        print(f"  Gateway port: {CONFIG.get('gateway_port', 5100)}")
        print("=" * 60)

        # Step 1: Start Chrome instances
        if not args.no_chrome:
            print("\n[Phase 1] Starting Chrome instances...")
            for inst in instances_cfg:
                chrome_proc = start_chrome(inst)
                if chrome_proc:
                    processes.append(chrome_proc)
                time.sleep(2)

            print("\n  Waiting 5s for Chrome to initialize...")
            time.sleep(5)

            print("\n[Phase 1.5] Arranging Chrome windows...")
            arrange_chrome_windows(instances_cfg)

        # Step 2: Start agents
        print("\n[Phase 2] Starting FlowKit agents...")
        for inst in instances_cfg:
            proc = start_agent(inst)
            if proc:
                processes.append(proc)
            time.sleep(1)

        print("\n  Waiting 3s for agents to bind ports...")
        time.sleep(3)

        # Step 3: Start gateway
        if not args.no_gateway:
            print("\n[Phase 3] Starting Gateway...")
            proc = start_gateway()
            if proc:
                processes.append(proc)

        print("\n" + "=" * 60)
        print("  FlowKit Server READY")
        print(f"  Gateway: http://0.0.0.0:{CONFIG.get('gateway_port', 5100)}")
        for inst in instances_cfg:
            seed = _chrome_seeds.get(inst["name"], 0)
            print(f"  {inst['name']}: API={inst['api_port']} WS={inst['ws_port']} FP={seed}")
        print("=" * 60)
        print("\n  Press Ctrl+C to stop all.\n")

    # Wait for processes
    try:
        while processes:
            for proc in processes[:]:
                if proc.poll() is not None:
                    processes.remove(proc)
                    print(f"[WARN] Process {proc.pid} exited with code {proc.returncode}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[Shutdown] Stopping all processes...")
        for proc in processes:
            try:
                if sys.platform == "win32":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
            except Exception:
                pass
        time.sleep(2)
        for proc in processes:
            try:
                proc.kill()
            except Exception:
                pass
        print("[Shutdown] Done.")


if __name__ == "__main__":
    main()
