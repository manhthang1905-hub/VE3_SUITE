#!/usr/bin/env python3
"""
IPv6 Pool Client - Goi HTTP API de lay/doi IPv6.
==================================================

Dung cho VM va Server ket noi toi IPv6 Pool API Server.

Usage:
    from modules.ipv6_pool_client import IPv6PoolClient

    client = IPv6PoolClient("http://192.168.88.x:8765")
    ip = client.get_ip("vm1_chrome1")
    new_ip = client.rotate_ip(ip, reason="403", worker="vm1_chrome1")
    client.release_ip(ip, worker="vm1_chrome1")
"""

import json
import time
import requests
from typing import Optional, Dict


class IPv6PoolClient:
    """
    Client goi IPv6 Pool HTTP API.

    Args:
        api_url: URL cua API server (vd: "http://192.168.88.100:8765")
        timeout: Timeout cho moi request (giay)
        max_retries: So lan retry neu API khong phan hoi
        log_func: Ham log
    """

    def __init__(
        self,
        api_url: str = "http://192.168.88.1:8765",
        timeout: int = 5,
        max_retries: int = 2,
        log_func=print,
    ):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.log = log_func
        self._session = requests.Session()

    # =====================================================================
    # MAIN API METHODS
    # =====================================================================

    def get_ip(self, worker: str = "unknown") -> Optional[Dict]:
        """
        Lay 1 IPv6 address + gateway tu pool.

        Args:
            worker: Ten worker (vd: "vm1_chrome1", "server_chrome3")

        Returns:
            Dict {"ip": "...", "gateway": "..."} hoac None neu het/loi
            Backward compat: str(result) tra ve IP
        """
        resp = self._get(f"/api/get_ip?worker={worker}")
        if resp and resp.get("success"):
            ip = resp["ip"]
            gateway = resp.get("gateway", "")
            self.log(f"[IPv6Client] GET: {ip} gw={gateway} (worker={worker})")
            return {"ip": ip, "gateway": gateway}
        elif resp:
            self.log(f"[IPv6Client] GET failed: {resp.get('error', 'unknown')}")
        return None

    def register_ip(self, ip: str, worker: str = "unknown") -> Optional[Dict]:
        """
        v1.0.603: Dang ky IP dang dung voi pool.

        VM goi khi khoi dong va da co IPv6 tu lan truoc.
        Pool match theo subnet, danh dau in_use.

        Args:
            ip: IPv6 address VM dang dung
            worker: Ten worker

        Returns:
            Dict {"ip": "...", "gateway": "..."} hoac None
        """
        resp = self._post("/api/register_ip", {
            "ip": ip,
            "worker": worker,
        })
        if resp and resp.get("success"):
            pool_ip = resp["ip"]
            gateway = resp.get("gateway", "")
            self.log(f"[IPv6Client] REGISTER: {ip} → {pool_ip} gw={gateway} (worker={worker})")
            return {"ip": pool_ip, "gateway": gateway}
        elif resp:
            self.log(f"[IPv6Client] REGISTER failed: {resp.get('error', 'unknown')}")
        return None

    def rotate_ip(self, ip: str, reason: str = "403", worker: str = "unknown") -> Optional[Dict]:
        """
        Burn IP cu, lay IP moi + gateway.

        Args:
            ip: IPv6 address hien tai
            reason: Ly do doi (vd: "403")
            worker: Ten worker

        Returns:
            Dict {"ip": "...", "gateway": "..."} hoac None
        """
        resp = self._post("/api/rotate_ip", {
            "ip": ip,
            "reason": reason,
            "worker": worker,
        })
        if resp and resp.get("success"):
            new_ip = resp["new_ip"]
            gateway = resp.get("gateway", "")
            self.log(f"[IPv6Client] ROTATE: {ip} → {new_ip} gw={gateway} (worker={worker})")
            return {"ip": new_ip, "gateway": gateway}
        elif resp:
            self.log(f"[IPv6Client] ROTATE failed: {resp.get('error', 'unknown')}")
        return None

    def release_ip(self, ip: str, worker: str = "unknown") -> bool:
        """
        Tra IP ve pool (IP van OK, khong bi 403).

        Args:
            ip: IPv6 address
            worker: Ten worker
        """
        resp = self._post("/api/release_ip", {
            "ip": ip,
            "worker": worker,
        })
        if resp and resp.get("success"):
            self.log(f"[IPv6Client] RELEASE: {ip} (worker={worker})")
            return True
        return False

    def burn_ip(self, ip: str, reason: str = "403", worker: str = "unknown") -> bool:
        """
        Danh dau IP la burned (khong dung lai).

        Args:
            ip: IPv6 address
            reason: Ly do burn
            worker: Ten worker
        """
        resp = self._post("/api/burn_ip", {
            "ip": ip,
            "reason": reason,
            "worker": worker,
        })
        if resp and resp.get("success"):
            self.log(f"[IPv6Client] BURN: {ip} (worker={worker})")
            return True
        return False

    def get_status(self) -> Optional[Dict]:
        """Lay trang thai pool."""
        return self._get("/api/status")

    def ping(self) -> bool:
        """Test ket noi toi API server."""
        resp = self._get("/api/ping")
        return resp is not None and resp.get("ok", False)

    # =====================================================================
    # HTTP HELPERS
    # =====================================================================

    def _get(self, path: str) -> Optional[Dict]:
        """GET request voi retry."""
        url = f"{self.api_url}{path}"
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.json()
                else:
                    self.log(f"[IPv6Client] GET {path} → {resp.status_code}")
                    return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None
            except requests.exceptions.ConnectionError:
                if attempt < self.max_retries:
                    time.sleep(1)
                    continue
                self.log(f"[IPv6Client] Cannot connect to {self.api_url}")
            except requests.exceptions.Timeout:
                if attempt < self.max_retries:
                    continue
                self.log(f"[IPv6Client] Timeout: {url}")
            except Exception as e:
                self.log(f"[IPv6Client] Error: {e}")
                break
        return None

    def _post(self, path: str, data: dict) -> Optional[Dict]:
        """POST request voi retry."""
        url = f"{self.api_url}{path}"
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.post(
                    url,
                    json=data,
                    timeout=self.timeout,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 200:
                    return resp.json()
                else:
                    self.log(f"[IPv6Client] POST {path} → {resp.status_code}")
                    return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else None
            except requests.exceptions.ConnectionError:
                if attempt < self.max_retries:
                    time.sleep(1)
                    continue
                self.log(f"[IPv6Client] Cannot connect to {self.api_url}")
            except requests.exceptions.Timeout:
                if attempt < self.max_retries:
                    continue
                self.log(f"[IPv6Client] Timeout: {url}")
            except Exception as e:
                self.log(f"[IPv6Client] Error: {e}")
                break
        return None
