#!/usr/bin/env python3
"""
IPv6 Local Proxy - SOCKS5 proxy để Chrome dùng IPv6
===================================================

Tạo local SOCKS5 proxy tại localhost:1088
Proxy sẽ route traffic qua IPv6 address được chỉ định.

Chrome kết nối: localhost:1088 (IPv4)
Proxy kết nối ra ngoài: IPv6 address

Như vậy RDP vẫn dùng IPv4, Chrome dùng IPv6.
"""

import sys
import os

# Fix Windows encoding issues
if sys.platform == "win32":
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass
    if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass


import socket
import threading
import select
import struct
import time
from typing import Optional, Tuple, Callable


def setup_ipv6_on_interface(new_ip, gateway, iface="Ethernet", old_ip="", log_func=print):
    """Full IPv6 setup: delete old → add new → route → NDP ping. Reusable."""
    import subprocess as _sp
    if old_ip:
        try:
            _sp.run(f'netsh interface ipv6 delete address "{iface}" {old_ip}',
                    shell=True, capture_output=True, timeout=10)
        except Exception:
            pass
    try:
        _sp.run(f'netsh interface ipv6 add address "{iface}" {new_ip}',
                shell=True, capture_output=True, timeout=10)
    except Exception:
        pass
    if gateway:
        onlink = ':'.join(gateway.split(':')[:4]) + '::/64'
        try:
            _sp.run(f'netsh interface ipv6 add route {onlink} "{iface}" {gateway}',
                    shell=True, capture_output=True, timeout=10)
        except Exception:
            pass
        # Update default route to new gateway (critical after rotate —
        # old gateway deleted → ALL IPv6 dies without this)
        # Delete ALL existing ::/0 routes on this interface first,
        # then add new one. Prevents stale/duplicate default routes.
        try:
            show = _sp.run(f'netsh interface ipv6 show route prefix=::/0 interface="{iface}"',
                           shell=True, capture_output=True, text=True, timeout=10)
            for line in (show.stdout or "").splitlines():
                if '::' in line and '/' not in line:
                    parts = line.split()
                    for p in parts:
                        if '::' in p and p != '::/0':
                            _sp.run(f'netsh interface ipv6 delete route ::/0 "{iface}" {p}',
                                    shell=True, capture_output=True, timeout=5)
        except Exception:
            pass
        try:
            _sp.run(f'netsh interface ipv6 add route ::/0 "{iface}" {gateway}',
                    shell=True, capture_output=True, timeout=10)
        except Exception:
            pass
    gw = gateway or ':'.join(new_ip.split(':')[:4]) + '::1'
    try:
        _sp.run(f'ping -6 -n 2 -w 3000 -S {new_ip} {gw}',
                shell=True, capture_output=True, timeout=10)
    except Exception:
        pass
    log_func(f"[IPv6] Setup: {new_ip} via {gw}")


# Global NDP keepalive threads — can be stopped/restarted per port
_ndp_keepalive_threads = {}
_ndp_keepalive_stop = {}


def start_ndp_keepalive(ipv6, gateway, port_key, log_func=print):
    """Start or restart NDP keepalive thread for an IP."""
    stop_ndp_keepalive(port_key)
    _ndp_keepalive_stop[port_key] = threading.Event()

    def _loop():
        gw = gateway or ':'.join(ipv6.split(':')[:4]) + '::1'
        import subprocess as _sp
        while not _ndp_keepalive_stop[port_key].is_set():
            try:
                _sp.run(f'ping -6 -n 1 -w 3000 -S {ipv6} {gw}',
                        shell=True, capture_output=True, timeout=10)
            except Exception:
                pass
            _ndp_keepalive_stop[port_key].wait(20)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    _ndp_keepalive_threads[port_key] = t


def stop_ndp_keepalive(port_key):
    """Stop NDP keepalive thread for a port."""
    if port_key in _ndp_keepalive_stop:
        _ndp_keepalive_stop[port_key].set()
    _ndp_keepalive_threads.pop(port_key, None)
    _ndp_keepalive_stop.pop(port_key, None)


class IPv6SocksProxy:
    """Simple SOCKS5 proxy that routes traffic through IPv6."""

    SOCKS_VERSION = 5

    def __init__(
        self,
        listen_port: int = 1088,
        ipv6_address: str = None,
        log_func: Callable = print
    ):
        """
        Initialize IPv6 SOCKS5 proxy.

        Args:
            listen_port: Port to listen on (localhost)
            ipv6_address: IPv6 address to bind outgoing connections
            log_func: Logging function
        """
        self.listen_port = listen_port
        self.ipv6_address = ipv6_address
        self.log = log_func
        self._server_socket = None
        self._running = False
        self._thread = None
        self._rotate_thread = None
        # v1.0.635: Track connect failures de detect IPv6 chet
        self._connect_failures = 0
        self._connect_successes = 0
        self._last_fail_time = 0
        self._fail_in_window = 0
        self._rotating = False
        self.worker_name = ""
        self.pool_url = ""
        self.iface = "Ethernet"

    def start(self) -> bool:
        """Start the proxy server in background thread."""
        if self._running:
            return True

        try:
            self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_socket.bind(('127.0.0.1', self.listen_port))
            self._server_socket.listen(100)
            self._server_socket.settimeout(1.0)

            self._running = True
            self._thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._thread.start()

            # Watch override file for IP changes (recovery writes here)
            self._override_thread = threading.Thread(target=self._watch_override_file, daemon=True)
            self._override_thread.start()

            self.log(f"[IPv6-Proxy] [v] Started on localhost:{self.listen_port}")
            if self.ipv6_address:
                self.log(f"[IPv6-Proxy] → Routing via: {self.ipv6_address}")
            return True

        except Exception as e:
            self.log(f"[IPv6-Proxy] [x] Failed to start: {e}")
            return False

    def stop(self):
        """Stop the proxy server."""
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except:
                pass
        self.log("[IPv6-Proxy] Stopped")

    def set_ipv6(self, ipv6_address: str):
        """Update IPv6 address for outgoing connections."""
        old_ip = self.ipv6_address
        self.ipv6_address = ipv6_address
        self._connect_failures = 0
        self._connect_successes = 0
        self._connect_fail_logged = 0
        self._fail_in_window = 0
        self._rotating = False
        self.log(f"[IPv6-Proxy] → Now using: {ipv6_address}")

    def _auto_rotate_ipv6(self):
        """Auto rotate IPv6 when connect timeout detected (3 fails in 30s)."""
        if self._rotating or not self.pool_url or not self.ipv6_address:
            return
        self._rotating = True
        old_ip = self.ipv6_address
        self.log(f"[IPv6-Proxy] AUTO ROTATE: {old_ip} dead, requesting new IP...")

        try:
            import requests
            resp = requests.post(f"{self.pool_url}/api/rotate_ip", json={
                "ip": old_ip,
                "reason": "timeout",
                "worker": self.worker_name or f"flowkit_proxy_{self.listen_port}",
            }, timeout=10)
            data = resp.json()
            new_ip = data.get("new_ip") or data.get("ip", "")
            new_gw = data.get("gateway", "")

            if not new_ip or new_ip == old_ip:
                self.log(f"[IPv6-Proxy] Pool returned no new IP")
                self._rotating = False
                return

            # Full IPv6 setup: delete old → add new → route → NDP
            setup_ipv6_on_interface(new_ip, new_gw, self.iface, old_ip, self.log)
            time.sleep(2)

            # Update proxy binding
            self.set_ipv6(new_ip)

            # Restart NDP keepalive for new IP
            start_ndp_keepalive(new_ip, new_gw, self.listen_port, self.log)

            # Update override file
            try:
                from pathlib import Path
                Path(f".ipv6_override_{self.listen_port}").write_text(new_ip)
            except Exception:
                pass

            self.log(f"[IPv6-Proxy] ROTATED: {old_ip} → {new_ip}")

        except Exception as e:
            self.log(f"[IPv6-Proxy] Rotate error: {e}")
            self._rotating = False

    def _watch_override_file(self):
        """Poll .ipv6_override_{port} file for IP changes from recovery (1s interval)."""
        from pathlib import Path
        override_file = Path(__file__).parent / f".ipv6_override_{self.listen_port}"
        last_mtime = 0
        while self._running:
            try:
                if override_file.exists():
                    mtime = override_file.stat().st_mtime
                    if mtime != last_mtime:
                        new_ip = override_file.read_text().strip()
                        if new_ip and new_ip != self.ipv6_address:
                            self.log(f"[IPv6-Proxy] Override detected: {self.ipv6_address} -> {new_ip}")
                            self.set_ipv6(new_ip)
                            start_ndp_keepalive(new_ip, "", self.listen_port, self.log)
                        last_mtime = mtime
            except Exception:
                pass
            time.sleep(1)

    def _accept_loop(self):
        """Accept incoming connections."""
        while self._running:
            try:
                client_socket, addr = self._server_socket.accept()
                # Handle each connection in a new thread
                handler = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket,),
                    daemon=True
                )
                handler.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    self.log(f"[IPv6-Proxy] Accept error: {e}")

    def _handle_client(self, client_socket: socket.socket):
        """Handle SOCKS5 client connection."""
        try:
            # SOCKS5 handshake
            if not self._socks5_handshake(client_socket):
                client_socket.close()
                return

            # Get target address
            target = self._socks5_get_target(client_socket)
            if not target:
                client_socket.close()
                return

            host, port = target

            # Connect to target via IPv6
            remote_socket = self._connect_via_ipv6(host, port)
            if not remote_socket:
                # Send failure response
                client_socket.send(b'\x05\x04\x00\x01\x00\x00\x00\x00\x00\x00')
                client_socket.close()
                return

            # Send success response
            client_socket.send(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')

            # Relay data
            self._relay(client_socket, remote_socket)

        except Exception as e:
            pass
        finally:
            try:
                client_socket.close()
            except:
                pass

    def _socks5_handshake(self, sock: socket.socket) -> bool:
        """Perform SOCKS5 handshake."""
        try:
            # Receive greeting
            data = sock.recv(256)
            if len(data) < 2 or data[0] != self.SOCKS_VERSION:
                return False

            # Send response (no auth required)
            sock.send(b'\x05\x00')
            return True
        except:
            return False

    def _socks5_get_target(self, sock: socket.socket) -> Optional[Tuple[str, int]]:
        """Get target address from SOCKS5 request."""
        try:
            # Receive request
            data = sock.recv(4)
            if len(data) < 4:
                return None

            version, cmd, _, atyp = data

            if version != self.SOCKS_VERSION or cmd != 1:  # Only CONNECT
                return None

            # Get address
            if atyp == 1:  # IPv4
                addr_data = sock.recv(4)
                host = socket.inet_ntoa(addr_data)
            elif atyp == 3:  # Domain
                length = sock.recv(1)[0]
                host = sock.recv(length).decode('utf-8')
            elif atyp == 4:  # IPv6
                addr_data = sock.recv(16)
                host = socket.inet_ntop(socket.AF_INET6, addr_data)
            else:
                return None

            # Get port
            port_data = sock.recv(2)
            port = struct.unpack('!H', port_data)[0]

            return (host, port)
        except:
            return None

    def _connect_via_ipv6(self, host: str, port: int) -> Optional[socket.socket]:
        """Connect to target - CHỈ dùng IPv6 và BIND vào source IPv6 cụ thể."""
        try:
            # Thử resolve IPv6 trước (AF_INET6)
            addrinfo = None
            try:
                addrinfo = socket.getaddrinfo(host, port, socket.AF_INET6, socket.SOCK_STREAM)
            except Exception:
                pass

            # Fallback: resolve bằng AF_UNSPEC (DNS qua IPv4 cũng được) rồi lọc IPv6
            if not addrinfo:
                try:
                    all_addrs = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
                    addrinfo = [a for a in all_addrs if a[0] == socket.AF_INET6]
                except Exception:
                    pass

            if not addrinfo:
                # v1.0.587: KHONG fallback IPv4 - chi dung IPv6
                # Truoc (v1.0.572): fallback IPv4 → Chrome van hien IPv4
                # Sau: Khong co AAAA record → tu choi ket noi (Chrome chi thay IPv6)
                return None

            family, socktype, proto, canonname, sockaddr = addrinfo[0]
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(30)

            # === QUAN TRỌNG: BIND vào IPv6 cụ thể để ép dùng đúng source IP ===
            if self.ipv6_address:
                try:
                    # Bind socket vào IPv6 address đã chọn (port 0 = OS chọn port tự do)
                    sock.bind((self.ipv6_address, 0, 0, 0))  # (host, port, flowinfo, scope_id)
                except Exception as bind_err:
                    # Chi log 1 lan dau, tranh spam
                    if not getattr(self, '_bind_warned', False):
                        self.log(f"[IPv6-Proxy] Bind warning (chi log 1 lan): {bind_err}")
                        self._bind_warned = True
                    # Vẫn thử connect dù bind fail

            sock.connect(sockaddr)
            # TCP_NODELAY cho outbound: giam delay khi gui du lieu
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass
            # v1.0.635: Track success
            self._connect_successes += 1
            self._connect_failures = 0  # Reset failures on success
            # Clear health file on success
            try:
                from pathlib import Path
                health_file = Path(__file__).parent / f".proxy_health_{self.listen_port}"
                if health_file.exists():
                    health_file.unlink()
            except Exception:
                pass
            return sock

        except Exception as e:
            try:
                sock.close()
            except Exception:
                pass
            self._connect_failures += 1
            if not getattr(self, '_connect_fail_logged', 0) or getattr(self, '_connect_fail_logged', 0) < 3:
                self.log(f"[IPv6-Proxy] IPv6 connect failed to {host}:{port}: {e}")
                self._connect_fail_logged = getattr(self, '_connect_fail_logged', 0) + 1
            else:
                self.log(f"[IPv6-Proxy] IPv6 connect failed: {e}")
            # Track fails in 30s window → auto rotate after 3
            now = time.time()
            if now - self._last_fail_time > 30:
                self._fail_in_window = 0
            self._last_fail_time = now
            self._fail_in_window += 1
            if self._fail_in_window >= 3 and not self._rotating and self.pool_url:
                t = threading.Thread(target=self._auto_rotate_ipv6, daemon=True)
                t.start()
            # Write health file for gateway to detect dead IPv6
            try:
                from pathlib import Path
                health_file = Path(__file__).parent / f".proxy_health_{self.listen_port}"
                health_file.write_text(str(self._connect_failures))
            except Exception:
                pass
            return None

    def _relay(self, client: socket.socket, remote: socket.socket):
        """Relay data between client and remote."""
        try:
            client.setblocking(False)
            remote.setblocking(False)

            # TCP_NODELAY: tat Nagle's algorithm → giam delay ~40ms/packet
            for s in (client, remote):
                try:
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass

            while True:
                # 300s timeout: Google Flow co the mat 2-3 phut xu ly anh/video
                ready, _, _ = select.select([client, remote], [], [], 300)

                if not ready:
                    break

                for sock in ready:
                    try:
                        # 262144 bytes (256KB): giam round-trip khi tai anh/video lon
                        data = sock.recv(262144)
                        if not data:
                            return

                        # sendall: dam bao GUI TOAN BO data (send co the chi gui 1 phan)
                        target = remote if sock is client else client
                        target.sendall(data)
                    except Exception:
                        return
        except Exception:
            pass
        finally:
            for s in (remote, client):
                try:
                    s.close()
                except Exception:
                    pass


# Global proxy instance
_proxy_instance: Optional[IPv6SocksProxy] = None


def get_ipv6_proxy(port: int = 1088, log_func: Callable = print) -> IPv6SocksProxy:
    """Get or create global IPv6 proxy instance."""
    global _proxy_instance

    if _proxy_instance is None:
        _proxy_instance = IPv6SocksProxy(listen_port=port, log_func=log_func)

    return _proxy_instance


def start_ipv6_proxy(ipv6_address: str, port: int = 1088, log_func: Callable = print) -> Optional[IPv6SocksProxy]:
    """Start IPv6 proxy with specified address."""
    proxy = get_ipv6_proxy(port, log_func)
    proxy.set_ipv6(ipv6_address)

    if not proxy._running:
        if proxy.start():
            return proxy
        return None

    return proxy
