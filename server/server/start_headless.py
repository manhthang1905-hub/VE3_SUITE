"""
Headless server launcher.

Doc config tu config/server_gui.json, nap vao server_settings,
roi auto-start Flask + Chrome workers khong can GUI.
"""
import argparse
import json
import sys
import threading
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOOL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(TOOL_DIR))


def _parse_accounts(raw_accounts):
    accounts = []
    for line in raw_accounts or []:
        parts = str(line).split("|")
        if len(parts) >= 2:
            accounts.append({
                "id": parts[0].strip(),
                "password": parts[1].strip(),
                "totp_secret": parts[2].strip() if len(parts) >= 3 else "",
            })
    return accounts


def _build_proxy_config(data):
    proxy_type = data.get("proxy_type", "none")
    return {
        "proxy_provider": {
            "type": proxy_type,
            "webshare": {
                "rotating_host": "p.webshare.io",
                "rotating_port": 80,
                "rotating_username": data.get("ws_username", "").strip(),
                "rotating_password": data.get("ws_password", "").strip(),
                "machine_id": int(data.get("ws_machine_id", 1) or 1),
            },
            "proxyxoay": {
                "api_keys": data.get("px_keys", []) or [],
                "proxy_type": data.get("px_type", "socks5"),
            },
        }
    }


def load_gui_config():
    cfg_path = TOOL_DIR / "config" / "server_gui.json"
    if not cfg_path.exists():
        return {}
    with open(cfg_path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    from server.app import app, server_settings, settings_lock, _do_start_workers, cleanup_old_tasks

    parser = argparse.ArgumentParser(description="Headless Chrome Server")
    parser.add_argument("--chrome", type=int, default=None, help="Override chrome count")
    parser.add_argument("--pool-api-url", type=str, default=None, help="Override IPv6 pool API URL")
    parser.add_argument("--no-ipv6", action="store_true", help="Disable IPv6")
    args = parser.parse_args()

    gui_cfg = load_gui_config()
    gui_accounts = _parse_accounts(gui_cfg.get("accounts_raw", []))
    use_ipv6 = False if args.no_ipv6 else bool(gui_cfg.get("use_ipv6", True))
    chrome_count = int(args.chrome if args.chrome is not None else (gui_cfg.get("chrome_count", 0) or 0))
    extra_ipv6 = gui_cfg.get("ipv6_list", []) or []
    pool_api_url = (args.pool_api_url if args.pool_api_url is not None else gui_cfg.get("pool_api_url", "")).strip()
    proxy_config = _build_proxy_config(gui_cfg)

    with settings_lock:
        server_settings["use_ipv6"] = use_ipv6
        server_settings["chrome_count"] = chrome_count
        server_settings["extra_ipv6"] = extra_ipv6
        server_settings["gui_accounts"] = gui_accounts
        server_settings["mode"] = "gop"
        server_settings["started"] = True
        server_settings["proxy_config"] = proxy_config
        server_settings["pool_api_url"] = pool_api_url

    print("=" * 60)
    print("  CHROME SERVER - HEADLESS")
    print("=" * 60)
    print()
    print(f"  IPv6:   {'BAT' if use_ipv6 else 'TAT'}")
    print(f"  Chrome: {chrome_count or 'TAT CA'}")
    print(f"  Pool:   {pool_api_url or '(none)'}")
    print(f"  Accounts from GUI: {len(gui_accounts)}")
    print()
    print("  Auto-start: Chrome dang setup...")
    print("  Dashboard: http://0.0.0.0:5000/")
    print()
    print("=" * 60)

    threading.Thread(target=cleanup_old_tasks, daemon=True).start()
    threading.Thread(target=_do_start_workers, daemon=True).start()

    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
