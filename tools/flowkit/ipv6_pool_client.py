"""IPv6 Pool Client — copied from server/modules/ipv6_pool_client.py"""
import time
import requests
from typing import Optional, Dict


class IPv6PoolClient:
    def __init__(self, api_url: str = "http://192.168.88.146:8765",
                 timeout: int = 5, max_retries: int = 2, log_func=print):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.log = log_func
        self._session = requests.Session()

    def get_ip(self, worker: str = "unknown") -> Optional[Dict]:
        resp = self._get(f"/api/get_ip?worker={worker}")
        if resp and resp.get("success"):
            return {"ip": resp["ip"], "gateway": resp.get("gateway", "")}
        return None

    def rotate_ip(self, ip: str, reason: str = "403", worker: str = "unknown") -> Optional[Dict]:
        resp = self._post("/api/rotate_ip", {"ip": ip, "reason": reason, "worker": worker})
        if resp and resp.get("success"):
            return {"ip": resp["new_ip"], "gateway": resp.get("gateway", "")}
        return None

    def release_ip(self, ip: str, worker: str = "unknown") -> bool:
        resp = self._post("/api/release_ip", {"ip": ip, "worker": worker})
        return bool(resp and resp.get("success"))

    def burn_ip(self, ip: str, reason: str = "403", worker: str = "unknown") -> bool:
        resp = self._post("/api/burn_ip", {"ip": ip, "reason": reason, "worker": worker})
        return bool(resp and resp.get("success"))

    def get_status(self) -> Optional[Dict]:
        return self._get("/api/status")

    def ping(self) -> bool:
        resp = self._get("/api/ping")
        return resp is not None and resp.get("ok", False)

    def _get(self, path: str) -> Optional[Dict]:
        url = f"{self.api_url}{path}"
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.json()
                return None
            except requests.exceptions.ConnectionError:
                if attempt < self.max_retries:
                    time.sleep(1)
                    continue
            except Exception:
                break
        return None

    def _post(self, path: str, data: dict) -> Optional[Dict]:
        url = f"{self.api_url}{path}"
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.post(url, json=data, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.json()
                return None
            except requests.exceptions.ConnectionError:
                if attempt < self.max_retries:
                    time.sleep(1)
                    continue
            except Exception:
                break
        return None
