"""
FlowKit Multi-Instance Launcher

Starts 7 independent FlowKit agents, each bound to its own:
  - Chrome Portable (unique Google account)
  - Extension copy (unique WS + HTTP ports)
  - Database (isolated data dir)

Usage:
    python flowkit_launcher.py --config launcher_config.yaml
    python flowkit_launcher.py --chrome-dir "D:\\VE3_SUITE" --base-port 8100
    python flowkit_launcher.py --config launcher_config.yaml --agents-only
    python flowkit_launcher.py --config launcher_config.yaml --status
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request
import yaml
from pathlib import Path

AGENT_DIR = Path(__file__).parent
EXTENSION_BASE = AGENT_DIR.parent.parent / "flowkit_extensions"
DATA_BASE = AGENT_DIR / "flowkit_data"


def load_config(config_path: str) -> list[dict]:
    """Load launcher config from YAML."""
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    instances = cfg.get("instances", [])
    for inst in instances:
        if "chrome_dir" in inst and "chrome_bin" not in inst:
            inst["chrome_bin"] = os.path.join(inst["chrome_dir"], "App", "Chrome-bin", "chrome.exe")
            inst["user_data_dir"] = os.path.join(inst["chrome_dir"], "Data", "profile")
    return instances


def discover_chrome_instances(chrome_dir: str, base_port: int = 8100) -> list[dict]:
    """Auto-discover Chrome Portable - Copy (N) instances."""
    instances = []
    pattern = os.path.join(chrome_dir, "GoogleChromePortable - Copy (*)")
    dirs = sorted(glob.glob(pattern))

    for i, chrome_dir_path in enumerate(dirs):
        chrome_bin = os.path.join(chrome_dir_path, "App", "Chrome-bin", "chrome.exe")
        if not os.path.isfile(chrome_bin):
            continue
        instances.append({
            "name": f"flowkit-{i + 1}",
            "chrome_dir": chrome_dir_path,
            "chrome_bin": chrome_bin,
            "user_data_dir": os.path.join(chrome_dir_path, "Data", "profile"),
            "port": base_port + i,
            "ws_port": 9222 + i,
        })

    return instances


def get_instance_data_dir(instance: dict) -> Path:
    """Get isolated data directory for an instance."""
    data_dir = DATA_BASE / instance["name"]
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_extension_dir(instance: dict) -> str:
    """Get the per-instance extension directory."""
    ext_dir = EXTENSION_BASE / f"ext_{instance['port']}"
    if ext_dir.is_dir():
        return str(ext_dir)
    return str(AGENT_DIR / "extension")


def start_agent(instance: dict) -> subprocess.Popen:
    """Start a FlowKit agent subprocess with isolated environment."""
    data_dir = get_instance_data_dir(instance)

    env = os.environ.copy()
    env["API_PORT"] = str(instance["port"])
    env["WS_PORT"] = str(instance["ws_port"])
    env["FLOW_AGENT_DIR"] = str(data_dir)

    proc = subprocess.Popen(
        [sys.executable, "-m", "agent.main"],
        cwd=str(AGENT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    return proc


def start_chrome(instance: dict) -> subprocess.Popen:
    """Launch Chrome with FlowKit extension (uses chrome.exe directly).

    GoogleChromePortable.exe wrapper does NOT pass --load-extension,
    so we call chrome.exe binary directly with --user-data-dir.
    """
    chrome_bin = instance["chrome_bin"]
    user_data_dir = instance["user_data_dir"]
    ext_dir = get_extension_dir(instance)

    cmd = [
        chrome_bin,
        f"--user-data-dir={user_data_dir}",
        f"--load-extension={ext_dir}",
        "--no-first-run",
        "--disable-default-apps",
        "https://labs.google/fx/tools/flow",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def check_agent_health(port: int, timeout: int = 3) -> dict:
    """Check agent health, returns health dict or None."""
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout)
        return json.loads(r.read())
    except Exception:
        return None


def wait_for_agent(port: int, timeout: int = 30) -> bool:
    """Wait for agent HTTP endpoint to be ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        health = check_agent_health(port)
        if health and health.get("status") == "ok":
            return True
        time.sleep(1)
    return False


def wait_for_extension(port: int, timeout: int = 120) -> bool:
    """Wait for extension to connect to agent."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        health = check_agent_health(port)
        if health and health.get("extension_connected"):
            return True
        time.sleep(3)
    return False


def print_status(instances: list[dict]):
    """Print status of all instances."""
    print(f"{'Name':<12} {'Port':<6} {'WS':<6} {'Agent':<8} {'Extension':<11} {'Account'}")
    print("-" * 70)
    for inst in instances:
        health = check_agent_health(inst["port"])
        agent_ok = "OK" if health else "DOWN"
        ext_ok = "OK" if health and health.get("extension_connected") else "---"
        account = inst.get("account", "")
        print(f"{inst['name']:<12} {inst['port']:<6} {inst['ws_port']:<6} {agent_ok:<8} {ext_ok:<11} {account}")


def main():
    parser = argparse.ArgumentParser(description="FlowKit Multi-Instance Launcher")
    parser.add_argument("--config", type=str, default="launcher_config.yaml",
                        help="Path to launcher_config.yaml (default: launcher_config.yaml)")
    parser.add_argument("--chrome-dir", type=str, help="Auto-discover Chrome instances in directory")
    parser.add_argument("--base-port", type=int, default=8100, help="Base API port (default: 8100)")
    parser.add_argument("--agents-only", action="store_true",
                        help="Start agents only (Chrome already running)")
    parser.add_argument("--status", action="store_true", help="Show status of all instances")
    parser.add_argument("--ve3-config", action="store_true", help="Print VE3 flowkit_server_list")
    args = parser.parse_args()

    # Load instances
    if args.chrome_dir:
        instances = discover_chrome_instances(args.chrome_dir, args.base_port)
    else:
        config_path = args.config
        if not os.path.isabs(config_path):
            config_path = str(AGENT_DIR / config_path)
        if not os.path.isfile(config_path):
            print(f"Config not found: {config_path}")
            sys.exit(1)
        instances = load_config(config_path)

    if not instances:
        print("No instances found!")
        sys.exit(1)

    # Status mode
    if args.status:
        print_status(instances)
        return

    # VE3 config output
    if args.ve3_config:
        print("flowkit_server_list:")
        for inst in instances:
            print(f'  - url: "http://127.0.0.1:{inst["port"]}"')
            print(f'    name: "{inst["name"]}"')
            print(f'    enabled: true')
        return

    # Launch
    print(f"Starting {len(instances)} FlowKit instances...")
    print(f"Data dir: {DATA_BASE}")
    print(f"Extensions: {EXTENSION_BASE}")
    print()

    agents = []
    chromes = []

    try:
        for inst in instances:
            name = inst["name"]
            port = inst["port"]

            # Check if agent already running on this port
            if check_agent_health(port):
                print(f"[{name}] Agent already running on port {port}, skipping")
                continue

            print(f"[{name}] Starting agent (port={port}, ws={inst['ws_port']})...")
            agent_proc = start_agent(inst)
            agents.append((inst, agent_proc))

            if not wait_for_agent(port):
                print(f"[{name}] FAILED to start agent!")
                continue

            print(f"[{name}] Agent ready")

            if not args.agents_only:
                print(f"[{name}] Launching Chrome...")
                chrome_proc = start_chrome(inst)
                chromes.append(chrome_proc)

                if wait_for_extension(port, timeout=60):
                    print(f"[{name}] READY (extension connected)")
                else:
                    print(f"[{name}] Agent OK, extension not yet connected")
                    print(f"         Open Chrome and navigate to Flow tab to connect")

            print()

        # Summary
        print("=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print_status(instances)
        print()
        print("VE3 config (add to .excel_runtime_config.yaml):")
        print("  generation_backend: flowkit")
        print("  flowkit_server_list:")
        for inst in instances:
            print(f'    - url: "http://127.0.0.1:{inst["port"]}"')
            print(f'      name: "{inst["name"]}"')
            print(f'      enabled: true')
        print("=" * 60)

        if agents:
            print("Press Ctrl+C to stop all agents.")
            while True:
                time.sleep(5)
                for inst, proc in agents:
                    if proc.poll() is not None:
                        print(f"[{inst['name']}] Agent exited (code {proc.returncode})")

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        for proc in chromes:
            try:
                proc.terminate()
            except Exception:
                pass
        for inst, proc in agents:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        print("All instances stopped.")


if __name__ == "__main__":
    main()
