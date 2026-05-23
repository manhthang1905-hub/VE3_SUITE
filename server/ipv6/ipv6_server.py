#!/usr/bin/env python3
"""
IPv6 Pool HTTP API Server
==========================

API server cho VM/Server lay va doi IPv6 tu dong.

Endpoints:
    GET  /api/get_ip?worker=vm1_chrome1   → Lay 1 IP
    POST /api/register_ip                  → VM dang ky IP dang dung
    POST /api/rotate_ip                    → Burn IP cu, lay IP moi
    POST /api/release_ip                   → Tra IP ve pool
    POST /api/burn_ip                      → Danh dau burned
    GET  /api/status                       → Trang thai pool

Chay doc lap:
    python -m ipv6.ipv6_server

Hoac tich hop vao GUI (start_api_server()).
"""

import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs
from typing import Optional

# Pool instance - set by start_api_server()
_pool = None
_log_func = print

# v1.0.562: Statistics tracking
_stats = {
    "total_get": 0,       # So lan get_ip
    "total_rotate": 0,    # So lan rotate_ip
    "total_burn": 0,      # So lan burn_ip
    "total_release": 0,   # So lan release_ip
    "workers": {},         # {worker_name: {"get": N, "rotate": N, "burn": N, "current_ip": "..."}}
    "started_at": None,    # Thoi gian bat dau
}
_stats_lock = threading.Lock()


def _track_stat(action: str, worker: str = "unknown", ip: str = ""):
    """Track 1 action cho thong ke."""
    with _stats_lock:
        _stats[f"total_{action}"] = _stats.get(f"total_{action}", 0) + 1
        if worker not in _stats["workers"]:
            _stats["workers"][worker] = {"get": 0, "rotate": 0, "burn": 0, "release": 0, "current_ip": ""}
        _stats["workers"][worker][action] = _stats["workers"][worker].get(action, 0) + 1
        if action in ("get", "rotate") and ip:
            _stats["workers"][worker]["current_ip"] = ip


def get_api_stats() -> dict:
    """Lay thong ke API (thread-safe)."""
    with _stats_lock:
        return dict(_stats)


def set_pool(pool, log_func=print):
    """Set pool instance cho API server."""
    global _pool, _log_func
    _pool = pool
    _log_func = log_func


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """v1.0.625: Multi-threaded HTTP server - tranh bi dong khi rotate_ip goi router."""
    daemon_threads = True


class IPv6APIHandler(BaseHTTPRequestHandler):
    """HTTP request handler cho IPv6 Pool API."""

    def log_message(self, format, *args):
        """Override de dung log_func thay vi stderr."""
        _log_func(f"[API] {args[0]} {args[1]}")

    def _send_json(self, data: dict, status: int = 200):
        """Gui JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _read_body(self) -> dict:
        """Doc JSON body tu request."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                body = self.rfile.read(length)
                return json.loads(body)
        except Exception:
            pass
        return {}

    # =====================================================================
    # GET endpoints
    # =====================================================================

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/api/get_ip":
            self._handle_get_ip(params)
        elif path == "/api/status":
            self._handle_status()
        elif path == "/api/stats":
            self._handle_stats()
        elif path == "/api/ping":
            self._send_json({"ok": True, "time": time.strftime("%H:%M:%S")})
        else:
            self._send_json({"error": "Not found"}, 404)

    def _handle_get_ip(self, params: dict):
        """GET /api/get_ip?worker=xxx → Lay 1 IP + gateway tu pool."""
        if not _pool:
            self._send_json({"error": "Pool not initialized"}, 503)
            return

        worker = params.get("worker", ["unknown"])[0]
        result = _pool.get_ip()

        if result:
            ip, gateway = result
            # Tim entry de lay them thong tin
            entry = None
            for e in _pool.pool:
                if e["address"] == ip:
                    entry = e
                    break

            _log_func(f"[API] GET_IP: {ip} gw={gateway} → {worker}")
            _track_stat("get", worker, ip)
            self._send_json({
                "success": True,
                "ip": ip,
                "gateway": gateway,
                "subnet": entry.get("subnet_hex", "") if entry else "",
                "worker": worker,
            })
        else:
            _log_func(f"[API] GET_IP: EMPTY → {worker}")
            self._send_json({"success": False, "error": "No available IPs"}, 200)

    def _handle_status(self):
        """GET /api/status → Trang thai pool + stats."""
        if not _pool:
            self._send_json({"error": "Pool not initialized"}, 503)
            return

        status = _pool.get_status()
        entries = []
        for e in _pool.pool:
            entries.append({
                "ip": e.get("address", ""),
                "subnet": e.get("subnet_hex", ""),
                "status": e.get("status", ""),
                "use_count": e.get("use_count", 0),
            })

        # v1.0.562: Include API stats
        api_stats = get_api_stats()

        self._send_json({
            "success": True,
            **status,
            "entries": entries,
            "api_stats": {
                "total_get": api_stats.get("total_get", 0),
                "total_rotate": api_stats.get("total_rotate", 0),
                "total_burn": api_stats.get("total_burn", 0),
                "total_release": api_stats.get("total_release", 0),
                "workers": api_stats.get("workers", {}),
            },
        })

    def _handle_stats(self):
        """GET /api/stats → Thong ke API chi tiet."""
        api_stats = get_api_stats()
        pool_status = _pool.get_status() if _pool else {}

        self._send_json({
            "success": True,
            "pool": pool_status,
            "api": {
                "total_get": api_stats.get("total_get", 0),
                "total_rotate": api_stats.get("total_rotate", 0),
                "total_burn": api_stats.get("total_burn", 0),
                "total_release": api_stats.get("total_release", 0),
                "started_at": api_stats.get("started_at"),
            },
            "workers": api_stats.get("workers", {}),
        })

    # =====================================================================
    # POST endpoints
    # =====================================================================

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()

        if path == "/api/register_ip":
            self._handle_register(body)
        elif path == "/api/rotate_ip":
            self._handle_rotate(body)
        elif path == "/api/release_ip":
            self._handle_release(body)
        elif path == "/api/burn_ip":
            self._handle_burn(body)
        else:
            self._send_json({"error": "Not found"}, 404)

    def _handle_register(self, body: dict):
        """POST /api/register_ip {ip, worker} → VM dang ky IP dang dung."""
        if not _pool:
            self._send_json({"error": "Pool not initialized"}, 503)
            return

        ip = body.get("ip", "")
        worker = body.get("worker", "unknown")

        if not ip:
            self._send_json({"error": "Missing 'ip' field"}, 400)
            return

        result = _pool.register_ip(ip, worker=worker)
        if result:
            pool_ip, gateway = result
            _log_func(f"[API] REGISTER: {worker} → {pool_ip} gw={gateway}")
            _track_stat("get", worker, pool_ip)
            self._send_json({
                "success": True,
                "ip": pool_ip,
                "gateway": gateway,
                "worker": worker,
                "registered": True,
            })
        else:
            _log_func(f"[API] REGISTER: {worker} → {ip} not found, use get_ip")
            self._send_json({"success": False, "error": "IP not in pool, use get_ip"}, 200)

    def _handle_rotate(self, body: dict):
        """POST /api/rotate_ip {ip, reason, worker} → Burn cu, tra IP moi."""
        if not _pool:
            self._send_json({"error": "Pool not initialized"}, 503)
            return

        ip = body.get("ip", "")
        reason = body.get("reason", "403")
        worker = body.get("worker", "unknown")

        if not ip:
            self._send_json({"error": "Missing 'ip' field"}, 400)
            return

        result = _pool.rotate_ip(ip, reason=reason)
        if result:
            new_ip, gateway = result
            _log_func(f"[API] ROTATE: {ip} → {new_ip} gw={gateway} ({worker}, {reason})")
            _track_stat("rotate", worker, new_ip)
            self._send_json({
                "success": True,
                "old_ip": ip,
                "new_ip": new_ip,
                "gateway": gateway,
                "worker": worker,
            })
        else:
            _log_func(f"[API] ROTATE FAILED: {ip} ({worker})")
            self._send_json({"success": False, "error": "No available IPs"}, 200)

    def _handle_release(self, body: dict):
        """POST /api/release_ip {ip, worker} → Tra IP ve pool."""
        if not _pool:
            self._send_json({"error": "Pool not initialized"}, 503)
            return

        ip = body.get("ip", "")
        worker = body.get("worker", "unknown")

        if not ip:
            self._send_json({"error": "Missing 'ip' field"}, 400)
            return

        _pool.release_ip(ip)
        _log_func(f"[API] RELEASE: {ip} ({worker})")
        _track_stat("release", worker)
        self._send_json({"success": True, "ip": ip})

    def _handle_burn(self, body: dict):
        """POST /api/burn_ip {ip, reason, worker} → Danh dau burned."""
        if not _pool:
            self._send_json({"error": "Pool not initialized"}, 503)
            return

        ip = body.get("ip", "")
        reason = body.get("reason", "403")
        worker = body.get("worker", "unknown")

        if not ip:
            self._send_json({"error": "Missing 'ip' field"}, 400)
            return

        _pool.burn_ip(ip, reason=reason)
        _log_func(f"[API] BURN: {ip} ({worker}, {reason})")
        _track_stat("burn", worker)
        self._send_json({"success": True, "ip": ip})


# =========================================================================
# SERVER START/STOP
# =========================================================================

_server: Optional[HTTPServer] = None
_server_thread: Optional[threading.Thread] = None


def _kill_port_holder(port: int, log_func=print):
    """
    v1.0.681: Kill process dang giu port (process cu con sot lai).
    Giai phong port de API server moi co the bind.
    """
    import subprocess
    import os

    my_pid = os.getpid()
    try:
        # Tim PID dang LISTEN tren port
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTEN" in line:
                parts = line.strip().split()
                if parts:
                    try:
                        pid = int(parts[-1])
                        if pid != my_pid and pid > 0:
                            os.kill(pid, 9)
                            log_func(f"[API] Killed old process PID {pid} on port {port}")
                    except (ValueError, OSError) as e:
                        log_func(f"[API] Cannot kill PID: {e}")
    except Exception as e:
        log_func(f"[API] Port cleanup skip: {e}")


def _ensure_firewall_rule(port: int, log_func=print):
    """
    v1.0.565: Tu dong them Windows Firewall rule cho API port.
    Chay khi API server start de VM/Server co the ket noi.
    """
    import subprocess
    import sys

    if sys.platform != "win32":
        return

    rule_name = f"IPv6 Pool API (port {port})"
    try:
        # Check rule da ton tai chua
        check = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
            capture_output=True, text=True, timeout=10
        )
        if check.returncode == 0 and rule_name in check.stdout:
            return  # Da co rule

        # Them rule moi
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={rule_name}", "dir=in", "action=allow",
             "protocol=TCP", f"localport={port}"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            log_func(f"[API] Firewall rule added: {rule_name}")
        else:
            log_func(f"[API] Firewall rule failed (can quyen Admin): {result.stderr.strip()}")
    except Exception as e:
        log_func(f"[API] Firewall check skipped: {e}")


def start_api_server(pool, host: str = "0.0.0.0", port: int = 8765, log_func=print) -> bool:
    """
    Start HTTP API server trong background thread.

    Args:
        pool: IPv6Pool instance
        host: Bind address (0.0.0.0 = tat ca interface)
        port: Port (default 8765)
        log_func: Ham log

    Returns:
        True neu start thanh cong
    """
    global _server, _server_thread

    if _server:
        # v1.0.680: Luon cap nhat log_func khi server da chay
        # Fix: truoc day log di ra print() thay vi GUI
        set_pool(pool, log_func)
        log_func("[API] Server da chay roi!")
        return True

    set_pool(pool, log_func)
    with _stats_lock:
        _stats["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # v1.0.565: Tu dong mo firewall cho port API
    _ensure_firewall_rule(port, log_func)

    try:
        _server = ThreadingHTTPServer((host, port), IPv6APIHandler)
        _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
        _server_thread.start()
        log_func(f"[API] Server started: http://{host}:{port}")
        return True
    except OSError as e:
        # v1.0.681: Port bi chiem boi process cu → kill va thu lai
        log_func(f"[API] Port {port} bi chiem, dang giai phong...")
        _kill_port_holder(port, log_func)
        import time as _t
        _t.sleep(1)
        try:
            _server = ThreadingHTTPServer((host, port), IPv6APIHandler)
            _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
            _server_thread.start()
            log_func(f"[API] Server started (sau cleanup): http://{host}:{port}")
            return True
        except OSError as e2:
            log_func(f"[API] Start failed: {e2}")
            _server = None
            return False


def stop_api_server():
    """Stop API server."""
    global _server, _server_thread
    if _server:
        _server.shutdown()
        _server = None
        _server_thread = None
        _log_func("[API] Server stopped")


def is_running() -> bool:
    """Check API server dang chay."""
    return _server is not None


# =========================================================================
# STANDALONE MODE
# =========================================================================

if __name__ == "__main__":
    import sys
    import argparse
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from ipv6 import create_pool

    parser = argparse.ArgumentParser(description="IPv6 Pool HTTP API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8765, help="Port")
    args = parser.parse_args()

    # Load config
    config_file = Path(__file__).parent / "config_test.json"
    if not config_file.exists():
        print(f"[!] Chua co config: {config_file}")
        print(f"    Chay truoc: python -m ipv6.test_pool --detect")
        sys.exit(1)

    with open(config_file, "r") as f:
        config = json.load(f)

    pool = create_pool(config)
    pool.init()

    print(f"\nIPv6 Pool API Server")
    print(f"====================")
    print(f"  http://{args.host}:{args.port}")
    print(f"")
    print(f"Endpoints:")
    print(f"  GET  /api/get_ip?worker=xxx")
    print(f"  POST /api/rotate_ip  {{ip, reason, worker}}")
    print(f"  POST /api/release_ip {{ip, worker}}")
    print(f"  POST /api/burn_ip    {{ip, reason, worker}}")
    print(f"  GET  /api/status")
    print(f"  GET  /api/ping")
    print(f"\nCtrl+C de dung")

    start_api_server(pool, args.host, args.port)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDang dung...")
        stop_api_server()
