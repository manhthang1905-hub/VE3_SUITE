#!/usr/bin/env python3
"""
IPv6PoolProvider - Proxy provider dung IPv6 Pool API.
=====================================================

Lay IPv6 tu Pool API server (MikroTik) thay vi doc file ipv6.txt.
Pool API quan ly: get_ip, rotate_ip, release_ip, burn_ip.

Moi worker lay 1 IP tu pool → bind vao SOCKS5 proxy → Chrome dung proxy.
Khi 403 → rotate_ip (burn cu, lay moi) qua API.
"""

from typing import Callable
from modules.proxy_providers.base_provider import ProxyProvider


class IPv6PoolProvider(ProxyProvider):
    """Proxy provider dung IPv6 Pool HTTP API."""

    def __init__(self, config: dict = None, log_func: Callable = print):
        super().__init__(config, log_func)
        self._client = None       # IPv6PoolClient instance
        self._proxy = None        # IPv6SocksProxy instance
        self._current_ip = None   # IPv6 hien tai
        self._worker_name = ""    # Ten worker (de Pool tracking)

    def setup(self, worker_id: int = 0, port: int = 1088) -> bool:
        """
        Khoi tao IPv6 Pool proxy.

        - Ket noi Pool API
        - Lay 1 IPv6 tu pool
        - Start SOCKS5 proxy bind vao IPv6 do

        Args:
            worker_id: 0 = Chrome 1, 1 = Chrome 2, ...
            port: Local SOCKS5 port
        """
        self.worker_id = worker_id
        self.port = port

        try:
            from modules.ipv6_pool_client import IPv6PoolClient

            # Doc config
            mikrotik_cfg = self.config.get('mikrotik', {})
            api_url = mikrotik_cfg.get('pool_api_url', '')
            timeout = mikrotik_cfg.get('pool_api_timeout', 5)
            worker_name = mikrotik_cfg.get('worker_name', 'vm1')
            self._worker_name = f"{worker_name}_chrome{worker_id}"

            if not api_url:
                self.log("[PROXY-Pool] pool_api_url KHONG DUOC CAU HINH!")
                return False

            self._client = IPv6PoolClient(
                api_url=api_url,
                timeout=timeout,
                log_func=self.log,
            )

            # Test ket noi
            if not self._client.ping():
                self.log(f"[PROXY-Pool] Khong ket noi duoc toi {api_url}")
                return False

            if worker_id == 0:
                # v1.0.603: Thu register IP cu truoc (neu VM da co tu lan truoc)
                existing_ip = self._get_existing_ipv6()
                result = None

                if existing_ip:
                    self.log(f"[PROXY-Pool] Tim thay IPv6 cu: {existing_ip}, dang ky voi pool...")
                    result = self._client.register_ip(existing_ip, worker=self._worker_name)
                    if result:
                        self.log(f"[PROXY-Pool] REGISTER OK: {result['ip']}")
                    else:
                        self.log(f"[PROXY-Pool] REGISTER failed, se lay IP moi")

                # Neu khong register duoc → lay IP moi
                if not result:
                    result = self._client.get_ip(worker=self._worker_name)

                if not result:
                    self.log("[PROXY-Pool] Khong lay duoc IP tu pool!")
                    return False

                # v1.0.578: Pool API tra ve dict {"ip": "...", "gateway": "..."}
                ip = result["ip"] if isinstance(result, dict) else result
                self._current_ip = ip
                self.log(f"[PROXY-Pool] Got IP: {ip}")

                from modules.ipv6_proxy import start_ipv6_proxy
                self._proxy = start_ipv6_proxy(
                    ipv6_address=ip,
                    port=port,
                    log_func=self.log,
                )
                if not self._proxy:
                    self.log("[PROXY-Pool] Khong tao duoc SOCKS5 proxy!")
                    # Tra IP ve pool vi khong dung duoc
                    self._client.release_ip(ip, worker=self._worker_name)
                    return False

                self._ready = True
                self.log(f"[PROXY-Pool] [v] Worker {worker_id}: IPv6={ip}, port={port}")
                return True

            else:
                # Chrome 2+: Reuse proxy tu Chrome 1
                import socket
                try:
                    _test = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    _test.settimeout(2)
                    _test.connect(('127.0.0.1', port))
                    _test.close()
                    self._ready = True
                    self.log(f"[PROXY-Pool] [v] Worker {worker_id}: Reuse proxy port {port}")
                    return True
                except Exception:
                    self.log(f"[PROXY-Pool] Worker {worker_id}: Proxy port {port} chua san sang")
                    return False

        except ImportError as e:
            self.log(f"[PROXY-Pool] Import error: {e}")
            return False
        except Exception as e:
            self.log(f"[PROXY-Pool] Setup error: {e}")
            return False

    def rotate(self, reason: str = "403") -> bool:
        """
        Doi IPv6 qua Pool API.
        Burn IP cu, lay IP moi, cap nhat SOCKS5 proxy.
        """
        if not self._client:
            self.log("[PROXY-Pool] Client chua khoi tao!")
            return False

        # Chi worker 0 rotate
        if self.worker_id != 0:
            self.log(f"[PROXY-Pool] Worker {self.worker_id}: Skip rotate (worker 0 se lam)")
            return True

        old_ip = self._current_ip
        if not old_ip:
            # Chua co IP → lay moi
            result = self._client.get_ip(worker=self._worker_name)
        else:
            # Rotate: burn cu + lay moi
            result = self._client.rotate_ip(old_ip, reason=reason, worker=self._worker_name)

        # v1.0.578: Pool API tra ve dict {"ip": "...", "gateway": "..."}
        if result:
            new_ip = result["ip"] if isinstance(result, dict) else result
            self._current_ip = new_ip
            # Cap nhat SOCKS5 proxy
            if self._proxy and hasattr(self._proxy, 'set_ipv6'):
                self._proxy.set_ipv6(new_ip)
            self.log(f"[PROXY-Pool] [v] Rotated ({reason}): {old_ip} → {new_ip}")
            return True

        self.log(f"[PROXY-Pool] [x] Rotate failed ({reason})")
        return False

    def get_chrome_arg(self) -> str:
        return f"socks5://127.0.0.1:{self.port}"

    def get_current_ip(self) -> str:
        return self._current_ip or "unknown"

    def stop(self):
        """Dung proxy, tra IP ve pool."""
        if self._current_ip and self._client:
            self._client.release_ip(self._current_ip, worker=self._worker_name)
            self.log(f"[PROXY-Pool] Released IP: {self._current_ip}")
        if self._proxy and hasattr(self._proxy, 'stop'):
            self._proxy.stop()
            self._proxy = None
        self._ready = False
        self.log("[PROXY-Pool] Stopped")

    def get_type(self) -> str:
        return "ipv6_pool"

    def _get_existing_ipv6(self) -> str:
        """
        v1.0.603: Kiem tra VM co IPv6 tu lan chay truoc khong.
        Doc tu Windows interface (netsh).
        """
        import subprocess
        try:
            mikrotik_cfg = self.config.get('mikrotik', {})
            prefix = mikrotik_cfg.get('prefix', '')
            if not prefix:
                return ""

            # Lay danh sach IPv6 tren may
            result = subprocess.run(
                'netsh interface ipv6 show address',
                capture_output=True, text=True, timeout=10
            )
            if result.returncode != 0:
                return ""

            # Tim IPv6 co prefix cua pool (vd: 2001:ee0:b004:30)
            prefix_check = prefix.rstrip(":")
            for line in result.stdout.splitlines():
                line = line.strip()
                if prefix_check in line and "::" not in line.split()[-1] if line.split() else False:
                    # Tim address dang: "2001:ee0:b004:30xx:xxxx:xxxx:xxxx:xxxx"
                    parts = line.split()
                    for part in parts:
                        if prefix_check in part and "::" not in part:
                            return part
            return ""
        except Exception:
            return ""

    def test_connectivity(self) -> bool:
        """Test ket noi toi Pool API."""
        if self._client:
            return self._client.ping()
        return False
