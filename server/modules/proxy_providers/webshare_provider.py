#!/usr/bin/env python3
"""
WebshareProvider - Proxy provider dung Webshare.io Rotating Residential.
========================================================================

Webshare Rotating Residential:
- Endpoint: p.webshare.io:80
- Doi IP bang cach doi session ID trong username
- Nhanh (~1s) vi chi can restart proxy bridge

Flow:
  Chrome → http://127.0.0.1:{port} (local bridge)
    → p.webshare.io:80 (Webshare proxy, auth = username-session-{id}:password)
    → Target website

Doi IP:
  Tang session_id → username moi → IP moi
  Khong can netsh, khong can IPv6 list
"""

import time
import socket
import threading
import struct
import select
from typing import Optional, Callable
from modules.proxy_providers.base_provider import ProxyProvider


class WebshareProxyBridge:
    """
    Local HTTP proxy bridge voi authentication.

    Chrome ket noi localhost:{port} (khong can auth)
    Bridge forward qua Webshare voi username:password (co auth)
    """

    def __init__(self, listen_port: int, remote_host: str, remote_port: int,
                 username: str, password: str, log_func: Callable = print):
        self.listen_port = listen_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.username = username
        self.password = password
        self.log = log_func
        self._server_socket = None
        self._running = False
        self._thread = None

    def start(self) -> bool:
        """Start proxy bridge."""
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
            return True

        except Exception as e:
            self.log(f"[WS-Bridge] Start failed: {e}")
            return False

    def stop(self):
        """Stop proxy bridge."""
        self._running = False
        if self._server_socket:
            try:
                self._server_socket.close()
            except:
                pass
            self._server_socket = None

    def update_auth(self, username: str, password: str):
        """Cap nhat auth (khi doi session ID)."""
        self.username = username
        self.password = password

    def _accept_loop(self):
        """Accept incoming connections."""
        while self._running:
            try:
                client_socket, addr = self._server_socket.accept()
                handler = threading.Thread(
                    target=self._handle_client,
                    args=(client_socket,),
                    daemon=True
                )
                handler.start()
            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    pass

    def _handle_client(self, client_socket: socket.socket):
        """
        Handle HTTP CONNECT proxy request.
        Chrome gui: CONNECT host:port HTTP/1.1
        Bridge forward qua Webshare voi Proxy-Authorization header.
        """
        try:
            client_socket.settimeout(30)
            # Doc request tu Chrome
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = client_socket.recv(4096)
                if not chunk:
                    client_socket.close()
                    return
                data += chunk

            first_line = data.split(b"\r\n")[0].decode('utf-8', errors='replace')
            parts = first_line.split()

            if len(parts) < 2:
                client_socket.close()
                return

            method = parts[0].upper()

            if method == "CONNECT":
                # HTTPS tunnel
                target = parts[1]
                self._handle_connect(client_socket, target)
            else:
                # HTTP request - forward truc tiep
                self._handle_http(client_socket, data)

        except Exception:
            pass
        finally:
            try:
                client_socket.close()
            except:
                pass

    def _handle_connect(self, client_socket: socket.socket, target: str):
        """Handle CONNECT (HTTPS tunnel) qua Webshare proxy."""
        try:
            # Ket noi toi Webshare proxy
            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote.settimeout(30)
            remote.connect((self.remote_host, self.remote_port))

            # Gui CONNECT voi auth toi Webshare
            import base64
            auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            connect_req = (
                f"CONNECT {target} HTTP/1.1\r\n"
                f"Host: {target}\r\n"
                f"Proxy-Authorization: Basic {auth}\r\n"
                f"Proxy-Connection: keep-alive\r\n"
                f"\r\n"
            )
            remote.send(connect_req.encode())

            # Doc response tu Webshare
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = remote.recv(4096)
                if not chunk:
                    remote.close()
                    client_socket.send(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    return
                response += chunk

            status_line = response.split(b"\r\n")[0].decode('utf-8', errors='replace')

            if "200" in status_line:
                # Tunnel established - tra loi Chrome
                client_socket.send(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                # Relay data 2 chieu
                self._relay(client_socket, remote)
            else:
                client_socket.send(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                remote.close()

        except Exception:
            try:
                client_socket.send(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except:
                pass

    def _handle_http(self, client_socket: socket.socket, data: bytes):
        """Handle HTTP request (non-CONNECT) qua Webshare proxy."""
        try:
            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote.settimeout(30)
            remote.connect((self.remote_host, self.remote_port))

            # Inject Proxy-Authorization header
            import base64
            auth = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()

            # Tim vi tri cuoi cua dong dau tien
            header_end = data.find(b"\r\n")
            rest = data[header_end:]

            # Them auth header
            auth_header = f"\r\nProxy-Authorization: Basic {auth}"
            modified = data[:header_end] + auth_header.encode() + rest

            remote.send(modified)

            # Relay response ve Chrome
            self._relay(client_socket, remote)

        except Exception:
            try:
                client_socket.send(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except:
                pass

    def _relay(self, client: socket.socket, remote: socket.socket):
        """Relay data giua client va remote."""
        try:
            client.setblocking(False)
            remote.setblocking(False)

            # TCP_NODELAY: giam delay
            for s in (client, remote):
                try:
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass

            while True:
                # 300s: Google Flow co the mat vai phut xu ly
                ready, _, _ = select.select([client, remote], [], [], 300)
                if not ready:
                    break
                for sock in ready:
                    try:
                        data = sock.recv(262144)
                        if not data:
                            return
                        target = remote if sock is client else client
                        target.sendall(data)
                    except Exception:
                        return
        except Exception:
            pass
        finally:
            try:
                remote.close()
            except:
                pass


class WebshareProvider(ProxyProvider):
    """
    Proxy provider dung Webshare.io Rotating Residential.

    Config:
        webshare:
            rotating_host: p.webshare.io
            rotating_port: 80
            rotating_username: jhvbehdf-residential-rotate
            rotating_password: cf1bi3yvq0t1
            machine_id: 1
    """

    def __init__(self, config: dict = None, log_func: Callable = print):
        super().__init__(config, log_func)
        ws_cfg = (config or {}).get('webshare', {})

        self.rotating_host = ws_cfg.get('rotating_host', 'p.webshare.io')
        self.rotating_port = ws_cfg.get('rotating_port', 80)
        self.base_username = ws_cfg.get('rotating_username', '')
        self.password = ws_cfg.get('rotating_password', '')
        self.machine_id = ws_cfg.get('machine_id', 1)

        self._session_id = 0
        self._bridge = None  # WebshareProxyBridge instance
        self._current_username = ''

    def setup(self, worker_id: int = 0, port: int = 8800) -> bool:
        """
        Khoi tao Webshare proxy bridge.

        Tao local HTTP proxy tai localhost:{port}
        Forward qua p.webshare.io voi auth.
        """
        self.worker_id = worker_id
        self.port = port

        if not self.base_username or not self.password:
            self.log("[PROXY-WS] Thieu username/password!")
            return False

        # Tinh session ID duy nhat cho worker nay
        # machine_id * 30000 + worker_id * 1000 + timestamp_component
        self._session_id = (self.machine_id - 1) * 30000 + worker_id * 1000 + 1
        self._current_username = self._build_username()

        self.log(f"[PROXY-WS] Setup worker {worker_id}: {self.rotating_host}:{self.rotating_port}")
        self.log(f"[PROXY-WS] Username: {self._current_username}")
        self.log(f"[PROXY-WS] Local port: {port}")

        # Tao proxy bridge
        self._bridge = WebshareProxyBridge(
            listen_port=port,
            remote_host=self.rotating_host,
            remote_port=self.rotating_port,
            username=self._current_username,
            password=self.password,
            log_func=self.log,
        )

        if self._bridge.start():
            self._ready = True
            self.log(f"[PROXY-WS] [v] Bridge started on localhost:{port}")
            return True

        self.log("[PROXY-WS] [x] Bridge start failed!")
        return False

    def rotate(self, reason: str = "403") -> bool:
        """
        Doi IP bang cach tang session ID.

        Webshare Rotating: doi session = doi IP moi.
        Chi can update username trong bridge, khong can restart.
        """
        self._session_id += 1
        self._current_username = self._build_username()

        self.log(f"[PROXY-WS] Rotate ({reason}): session → {self._session_id}")
        self.log(f"[PROXY-WS] New username: {self._current_username}")

        if self._bridge:
            self._bridge.update_auth(self._current_username, self.password)

        return True

    def get_chrome_arg(self) -> str:
        """Tra ve HTTP proxy URL cho Chrome."""
        return f"http://127.0.0.1:{self.port}"

    def get_current_ip(self) -> str:
        """Tra ve session info (Webshare khong biet IP that)."""
        return f"webshare-session-{self._session_id}"

    def stop(self):
        """Dung proxy bridge."""
        if self._bridge:
            self._bridge.stop()
            self._bridge = None
        self._ready = False
        self.log("[PROXY-WS] Stopped")

    def get_type(self) -> str:
        return "webshare"

    def test_connectivity(self) -> bool:
        """Test ket noi qua Webshare proxy."""
        try:
            import requests
            proxies = {
                "http": f"http://{self._current_username}:{self.password}@{self.rotating_host}:{self.rotating_port}",
                "https": f"http://{self._current_username}:{self.password}@{self.rotating_host}:{self.rotating_port}",
            }
            resp = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=15)
            if resp.status_code == 200:
                ip = resp.json().get('origin', 'unknown')
                self.log(f"[PROXY-WS] [v] Test OK: IP = {ip}")
                return True
            self.log(f"[PROXY-WS] [x] Test failed: HTTP {resp.status_code}")
            return False
        except Exception as e:
            self.log(f"[PROXY-WS] [x] Test failed: {e}")
            return False

    def _build_username(self) -> str:
        """
        Tao username voi session ID.

        Format Webshare Rotating:
        - Random mode: username-rotate (khong can session)
        - Sticky mode: username-session-{id} (giu IP trong session)

        Khi rotate: doi session_id → Webshare tra IP moi.
        """
        base = self.base_username

        # Neu username ket thuc bang "-rotate" → Random mode
        # Doi sang Sticky mode voi session de co the rotate duoc
        if base.endswith('-rotate'):
            base = base[:-7]  # Bo "-rotate"

        return f"{base}-session-{self._session_id}"
