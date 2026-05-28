"""
FlowKit Launcher — starts Chrome instances, agents, and gateway.

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

import yaml

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.yaml"

if not CONFIG_PATH.exists():
    print(f"[FATAL] config.yaml not found: {CONFIG_PATH}")
    sys.exit(1)
with open(CONFIG_PATH) as f:
    CONFIG = yaml.safe_load(f)


def resolve_path(rel_path: str) -> Path:
    """Resolve path relative to flowkit directory."""
    return (BASE_DIR / rel_path).resolve()


def start_chrome(instance: dict) -> subprocess.Popen:
    """Start Chrome Portable with extension loaded.

    Uses GoogleChromePortable.exe wrapper (not chrome.exe directly)
    because the wrapper correctly handles profile setup and MV3
    service worker activation.
    """
    # Use the Portable wrapper, not chrome.exe directly
    chrome_dir = resolve_path(instance["chrome_path"]).parent.parent.parent
    portable_exe = chrome_dir / "GoogleChromePortable.exe"
    ext_dir = resolve_path(instance["extension_dir"])
    ipv6 = instance.get("ipv6", "")

    if not portable_exe.exists():
        print(f"[ERROR] ChromePortable not found: {portable_exe}")
        return None

    # GoogleChromePortable.exe passes extra args to chrome.exe
    args = [
        str(portable_exe),
        f"--load-extension={ext_dir}",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
    ]

    # IPv6 proxy support
    if ipv6:
        args.append(f"--proxy-server=socks5://[{ipv6}]:1080")

    print(f"[{instance['name']}] Starting Chrome: {portable_exe.name}")
    print(f"  Dir: {chrome_dir}")
    print(f"  Extension: {ext_dir}")
    if ipv6:
        print(f"  IPv6 proxy: {ipv6}")

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    print(f"  PID: {proc.pid}")
    return proc


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

    agent_script = BASE_DIR / "agent" / "main.py"

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
            print(f"  {inst['name']}: API={inst['api_port']} WS={inst['ws_port']}")
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
