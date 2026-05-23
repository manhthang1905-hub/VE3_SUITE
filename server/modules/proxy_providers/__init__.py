#!/usr/bin/env python3
"""
Proxy Providers - Pluggable proxy system cho VE3 Tool.
=======================================================

Ho tro nhieu loai proxy:
- "none": Khong dung proxy (mac dinh)
- "ipv6": IPv6 SOCKS5 rotation (giu nguyen logic cu)
- "webshare": Webshare.io Rotating Residential

Usage:
    from modules.proxy_providers import create_provider

    # Tu config
    provider = create_provider(config)
    provider.setup(worker_id=0, port=1088)

    # Chrome
    chrome_arg = provider.get_chrome_arg()
    # → "socks5://127.0.0.1:1088" (IPv6)
    # → "http://127.0.0.1:8800" (Webshare)

    # Doi IP
    provider.rotate("403")

    # Cleanup
    provider.stop()

Config format (settings.yaml):
    proxy_provider:
      type: "ipv6"  # "none" | "ipv6" | "webshare"

      ipv6:
        interface_name: Ethernet
        local_proxy_port: 1088

      webshare:
        rotating_host: p.webshare.io
        rotating_port: 80
        rotating_username: jhvbehdf-residential-rotate
        rotating_password: cf1bi3yvq0t1
        machine_id: 1
"""

from typing import Optional, Callable
from modules.proxy_providers.base_provider import ProxyProvider


class NoneProvider(ProxyProvider):
    """No-op provider - khong dung proxy."""

    def setup(self, worker_id: int = 0, port: int = 0) -> bool:
        self.worker_id = worker_id
        self._ready = True
        return True

    def rotate(self, reason: str = "403") -> bool:
        return False  # Khong co gi de rotate

    def get_chrome_arg(self) -> str:
        return ""  # Khong co proxy arg

    def get_current_ip(self) -> str:
        return "direct"

    def stop(self):
        self._ready = False

    def get_type(self) -> str:
        return "none"


def create_provider(config: dict = None, log_func: Callable = print) -> ProxyProvider:
    """
    Factory function - tao provider theo config.

    Args:
        config: Dict cau hinh (tu settings.yaml hoac GUI)
            - proxy_provider.type: "none" | "ipv6" | "webshare"
            - proxy_provider.ipv6: {...}
            - proxy_provider.webshare: {...}
        log_func: Ham log

    Returns:
        ProxyProvider instance
    """
    config = config or {}

    # Doc tu proxy_provider section
    pp_config = config.get('proxy_provider', {})
    provider_type = pp_config.get('type', 'none').lower()

    # Backward compat: neu khong co proxy_provider section,
    # check ipv6_rotation.enabled (logic cu)
    if not pp_config:
        ipv6_cfg = config.get('ipv6_rotation', {})
        if ipv6_cfg.get('enabled', False):
            provider_type = 'ipv6'

    # v1.0.574: ipv6_pool cung dung IPv6Provider (ipv6_rotator da co pool mode)
    # ipv6_rotator tu dong detect pool_api_url va dung Pool API
    # CHI apply khi chua co pp_config hoac dang la ipv6 (khong override webshare/proxyxoay)
    if provider_type == 'ipv6' or (not pp_config and provider_type not in ('webshare', 'proxyxoay')):
        mikrotik_cfg = config.get('mikrotik', {})
        pool_url = mikrotik_cfg.get('pool_api_url', '')
        if pool_url:
            provider_type = 'ipv6'  # Dung IPv6Provider, rotator se detect pool mode

    if provider_type in ('ipv6_pool', 'ipv6'):
        from modules.proxy_providers.ipv6_provider import IPv6Provider
        provider = IPv6Provider(config=config, log_func=log_func)
        log_func(f"[PROXY] Provider: IPv6")
        return provider

    elif provider_type == 'webshare':
        from modules.proxy_providers.webshare_provider import WebshareProvider
        ws_config = pp_config.get('webshare', {})
        # Backward compat: doc tu webshare_proxy section cu
        if not ws_config:
            ws_config = config.get('webshare_proxy', {})
        provider = WebshareProvider(config={'webshare': ws_config}, log_func=log_func)
        log_func(f"[PROXY] Provider: Webshare Rotating Residential")
        return provider

    elif provider_type == 'proxyxoay':
        from modules.proxy_providers.proxyxoay_provider import ProxyXoayProvider
        px_config = pp_config.get('proxyxoay', {})
        provider = ProxyXoayProvider(config={'proxyxoay': px_config}, log_func=log_func)
        log_func(f"[PROXY] Provider: ProxyXoay.shop")
        return provider

    else:
        log_func(f"[PROXY] Provider: None (direct connection)")
        return NoneProvider(config=config, log_func=log_func)


def get_provider_types() -> list:
    """Tra ve danh sach cac provider types ho tro."""
    return [
        {"value": "none", "label": "Khong dung proxy"},
        {"value": "ipv6", "label": "IPv6 Rotation"},
        {"value": "ipv6_pool", "label": "IPv6 Pool API"},
        {"value": "webshare", "label": "Webshare Rotating Residential"},
        {"value": "proxyxoay", "label": "ProxyXoay.shop"},
    ]
