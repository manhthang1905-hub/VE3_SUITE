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
        # v1.0.635: Track connect failures de detect IPv6 chet
        self._connect_failures = 0
        self._connect_successes = 0

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
        self.ipv6_address = ipv6_address
        # v1.0.635: Reset counters khi doi IPv6
        self._connect_failures = 0
        self._connect_successes = 0
        self._connect_fail_logged = 0
        self.log(f"[IPv6-Proxy] → Now using: {ipv6_address}")

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
            return sock

        except Exception as e:
            # v1.0.635: Track failures
            self._connect_failures += 1
            # v1.0.611: Log target de debug connectivity
            if not getattr(self, '_connect_fail_logged', 0) or getattr(self, '_connect_fail_logged', 0) < 3:
                self.log(f"[IPv6-Proxy] IPv6 connect failed to {host}:{port}: {e}")
                self._connect_fail_logged = getattr(self, '_connect_fail_logged', 0) + 1
            else:
                self.log(f"[IPv6-Proxy] IPv6 connect failed: {e}")
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
            try:
                remote.close()
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
