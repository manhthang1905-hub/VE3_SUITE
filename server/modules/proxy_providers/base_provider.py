#!/usr/bin/env python3
"""
ProxyProvider - Abstract base class cho tat ca proxy providers.
================================================================

Moi provider (IPv6, Webshare, ...) implement interface nay.
Chrome chi can goi: setup() → get_chrome_arg() → rotate() → stop()

Usage:
    from modules.proxy_providers import create_provider
    provider = create_provider(config)
    provider.setup(worker_id=0, port=1088)
    chrome_arg = provider.get_chrome_arg()  # "--proxy-server=..."
    provider.rotate("403")
    provider.stop()
"""

from abc import ABC, abstractmethod
from typing import Optional, Callable


class ProxyProvider(ABC):
    """Abstract base class cho proxy providers."""

    def __init__(self, config: dict = None, log_func: Callable = print):
        self.config = config or {}
        self.log = log_func
        self.worker_id = 0
        self.port = 0
        self._ready = False

    @abstractmethod
    def setup(self, worker_id: int = 0, port: int = 1088) -> bool:
        """
        Khoi tao proxy cho worker.

        Args:
            worker_id: ID cua Chrome worker (0, 1, 2, ...)
            port: Local port de Chrome ket noi

        Returns:
            True neu setup thanh cong
        """
        pass

    @abstractmethod
    def rotate(self, reason: str = "403") -> bool:
        """
        Doi IP moi.

        Args:
            reason: Ly do doi IP ("403", "timeout", "manual", ...)

        Returns:
            True neu doi thanh cong
        """
        pass

    @abstractmethod
    def get_chrome_arg(self) -> str:
        """
        Tra ve Chrome argument de ket noi proxy.

        Returns:
            String nhu "socks5://127.0.0.1:1088" hoac "http://127.0.0.1:8800"
        """
        pass

    @abstractmethod
    def get_current_ip(self) -> str:
        """
        Tra ve IP hien tai (de log/hien thi).

        Returns:
            IP string hoac "unknown"
        """
        pass

    @abstractmethod
    def stop(self):
        """Dung proxy, giai phong resources."""
        pass

    def is_ready(self) -> bool:
        """Check proxy da san sang chua."""
        return self._ready

    @abstractmethod
    def get_type(self) -> str:
        """Tra ve loai provider: 'ipv6', 'webshare', 'none'."""
        pass

    def test_connectivity(self) -> bool:
        """
        Test ket noi proxy (optional, mac dinh True).
        Override neu provider can test.
        """
        return True

    def has_ttl(self) -> bool:
        """
        Provider nay co TTL (proxy het han sau 1 thoi gian) hay khong?
        Override = True cho provider co TTL (vd: ProxyXoay).
        Mac dinh: False (IPv6, Webshare khong co TTL).
        """
        return False

    def get_ttl(self) -> int:
        """
        Tra ve so giay con lai cua proxy hien tai.
        Mac dinh: 9999 (khong co TTL = luon song).
        Override cho provider co TTL.
        """
        return 9999

    def ensure_proxy_alive(self, min_ttl: int = 120) -> bool:
        """
        Dam bao proxy con song it nhat min_ttl giay.
        Neu TTL < min_ttl → tu dong rotate lay proxy moi.
        Mac dinh: luon True (provider khong co TTL).
        Override cho provider co TTL.

        Args:
            min_ttl: So giay toi thieu can

        Returns:
            True neu proxy OK (con du TTL hoac rotate thanh cong)
        """
        if not self.has_ttl():
            return True
        ttl = self.get_ttl()
        if ttl >= min_ttl:
            return True
        self.log(f"[PROXY] TTL={ttl}s < {min_ttl}s → rotate truoc...")
        return self.rotate("ttl_low")
