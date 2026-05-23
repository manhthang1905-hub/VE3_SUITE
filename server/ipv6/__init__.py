#!/usr/bin/env python3
"""
IPv6 Dynamic Pool - Quan ly IPv6 addresses qua MikroTik REST API.
==================================================================

VNPT Fiber Xtra 2: Prefix /56 → 256 subnet /64
Subnet rotate: 3052 → 30ff (174 subnets kha dung)

Usage:
    from ipv6 import create_pool

    pool = create_pool(config)
    pool.init()

    # Lay IP
    ip = pool.get_ip()

    # Bi 403 → doi IP
    new_ip = pool.rotate_ip(ip, reason="403")

    # Tra IP (van OK)
    pool.release_ip(ip)

Config format (settings.yaml):
    mikrotik:
        host: 192.168.88.1
        username: admin
        password: "matkhau"
        interface: ether1
        prefix: "2001:ee0:4f89:30"
        subnet_start: 82        # 0x52
        subnet_end: 255         # 0xFF
        pool_min: 3
        pool_max: 10
"""

from ipv6.mikrotik_api import MikroTikAPI
from ipv6.ipv6_pool import IPv6Pool


def create_pool(config: dict = None, log_func=print) -> IPv6Pool:
    """
    Factory function - tao IPv6Pool tu config.

    Args:
        config: Dict cau hinh (tu settings.yaml hoac GUI)
        log_func: Ham log
    """
    config = config or {}
    mk_cfg = config.get("mikrotik", {})

    api = MikroTikAPI(
        host=mk_cfg.get("host", "192.168.88.1"),
        username=mk_cfg.get("username", "admin"),
        password=mk_cfg.get("password", ""),
        interface=mk_cfg.get("interface", "ether1"),
        prefix=mk_cfg.get("prefix", ""),
        subnet_start=mk_cfg.get("subnet_start", 0x65),
        subnet_end=mk_cfg.get("subnet_end", 0xFF),
        log_func=log_func,
    )

    pool = IPv6Pool(
        mikrotik=api,
        min_pool_size=mk_cfg.get("pool_min", 3),
        max_pool_size=mk_cfg.get("pool_max", 20),
        cooldown_seconds=mk_cfg.get("cooldown_seconds", 3600),
        log_func=log_func,
    )

    return pool
