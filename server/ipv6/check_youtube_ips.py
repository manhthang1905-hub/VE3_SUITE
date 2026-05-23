#!/usr/bin/env python3
"""
Kiem tra 100 IPv6 YouTube (subnet 01-65) tren MikroTik router.
- Gateway ::1/64 con tren router khong?
- Co bi Pool ghi de khong?

Usage:
    python ipv6/check_youtube_ips.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ipv6.mikrotik_api import MikroTikAPI

# ===== CONFIG - SUA THEO MAY BAN =====
# Doc tu settings.yaml hoac nhap truc tiep
ROUTER_HOST = "192.168.88.1"
ROUTER_USER = "admin"
ROUTER_PASS = ""  # Nhap password router
INTERFACE = "ether1"
PREFIX = "2001:ee0:4f89:30"  # Prefix /56

# YouTube subnet range
YT_START = 0x01  # subnet 3001
YT_END = 0x65    # subnet 3065 (100 subnets)
# ======================================

def main():
    # Thu doc config tu settings.yaml
    try:
        import yaml
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "config", "settings.yaml")
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f) or {}
            mk = cfg.get('mikrotik', {})
            host = mk.get('host', ROUTER_HOST)
            user = mk.get('username', ROUTER_USER)
            pwd = mk.get('password', ROUTER_PASS)
            iface = mk.get('interface', INTERFACE)
            prefix = mk.get('prefix', PREFIX)
        else:
            host, user, pwd, iface, prefix = ROUTER_HOST, ROUTER_USER, ROUTER_PASS, INTERFACE, PREFIX
    except Exception:
        host, user, pwd, iface, prefix = ROUTER_HOST, ROUTER_USER, ROUTER_PASS, INTERFACE, PREFIX

    print(f"Router: {host}")
    print(f"Interface: {iface}")
    print(f"Prefix: {prefix}")
    print(f"YouTube range: subnet {YT_START:02x}-{YT_END:02x} ({YT_END - YT_START + 1} subnets)")
    print("=" * 70)

    api = MikroTikAPI(
        host=host, username=user, password=pwd,
        interface=iface, prefix=prefix,
        log_func=lambda x: None  # Im lang
    )

    # Lay tat ca IPv6 tren router
    all_addrs = api.list_ipv6_addresses()
    if not all_addrs:
        print("[!] Khong lay duoc danh sach IPv6 tu router!")
        print("    Kiem tra: host, username, password, ket noi mang")
        return

    print(f"Tong IPv6 tren router: {len(all_addrs)}")
    print()

    # Phan loai theo subnet YouTube
    yt_gateways = {}   # subnet -> gateway entry (::1/64)
    yt_workers = {}     # subnet -> list of worker entries
    pool_gateways = {}  # subnet -> gateway entry (66-ff)

    for entry in all_addrs:
        addr = entry.get("address", "")
        interface = entry.get("interface", "")
        addr_id = entry.get(".id", "")
        addr_clean = addr.split("/")[0]

        if interface != iface:
            continue

        subnet = api._extract_subnet(addr)
        if subnet is None:
            continue

        if YT_START <= subnet <= YT_END:
            # YouTube subnet
            if addr_clean.endswith("::1") or addr_clean.endswith(":0001"):
                yt_gateways[subnet] = {"addr": addr, "id": addr_id}
            else:
                if subnet not in yt_workers:
                    yt_workers[subnet] = []
                yt_workers[subnet].append({"addr": addr, "id": addr_id})
        elif subnet >= 0x66:
            # Pool subnet
            if addr_clean.endswith("::1") or addr_clean.endswith(":0001"):
                pool_gateways[subnet] = {"addr": addr, "id": addr_id}

    # === BAO CAO ===
    print("=" * 70)
    print("BAO CAO 100 IPv6 YOUTUBE (subnet 01-65)")
    print("=" * 70)

    missing = []
    ok_count = 0
    has_worker = 0

    for s in range(YT_START, YT_END + 1):
        s_hex = f"{s:02x}"
        expected_gw = f"{prefix}{s_hex}::1/64"

        if s in yt_gateways:
            gw = yt_gateways[s]
            workers = yt_workers.get(s, [])
            status = "OK"
            ok_count += 1
            if workers:
                has_worker += 1
                worker_ips = ", ".join(w["addr"] for w in workers)
                print(f"  [{status}] subnet {s_hex}: gateway {gw['addr']} + {len(workers)} worker(s): {worker_ips}")
            else:
                print(f"  [{status}] subnet {s_hex}: gateway {gw['addr']}")
        else:
            status = "MISSING"
            missing.append(s)
            workers = yt_workers.get(s, [])
            if workers:
                worker_ips = ", ".join(w["addr"] for w in workers)
                print(f"  [!!!] subnet {s_hex}: KHONG CO GATEWAY! Co {len(workers)} worker: {worker_ips}")
            else:
                print(f"  [!!!] subnet {s_hex}: KHONG CO GATEWAY! ({expected_gw})")

    print()
    print("=" * 70)
    print("TONG KET")
    print("=" * 70)
    print(f"  Gateway OK:      {ok_count}/{YT_END - YT_START + 1}")
    print(f"  Gateway MISSING: {len(missing)}/{YT_END - YT_START + 1}")
    print(f"  Co worker IP:    {has_worker}")
    print(f"  Pool gateways:   {len(pool_gateways)} (subnet 66-ff)")

    if missing:
        print()
        print("=" * 70)
        print("SUBNET BI MAT GATEWAY - CAN KHOI PHUC!")
        print("=" * 70)
        print(f"  Missing subnets: {', '.join(f'{s:02x}' for s in missing)}")
        print()
        print("LENH KHOI PHUC (chay tren may co ket noi MikroTik):")
        print()
        for s in missing:
            s_hex = f"{s:02x}"
            print(f'  # Subnet {s_hex}:')
            print(f'  curl -k -u {user}:{pwd} -X PUT "{api.base_url}/ipv6/address" '
                  f'-H "Content-Type: application/json" '
                  f'-d \'{{"address":"{prefix}{s_hex}::1/64","interface":"{iface}","advertise":"false"}}\'')
            print()

        # Tao script Python de khoi phuc
        print()
        print("HOAC chay script Python nay de khoi phuc tu dong:")
        print("-" * 50)
        print(f"    python ipv6/check_youtube_ips.py --restore")
    else:
        print()
        print("[v] TAT CA 100 GATEWAY YOUTUBE VAN CON TREN ROUTER!")

    # === AUTO RESTORE ===
    if "--restore" in sys.argv and missing:
        print()
        print("=" * 70)
        print("DANG KHOI PHUC GATEWAY...")
        print("=" * 70)
        restored = 0
        for s in missing:
            s_hex = f"{s:02x}"
            gw_addr = f"{prefix}{s_hex}::1/64"
            result = api.add_ipv6_address(gw_addr)
            if result:
                print(f"  [v] Restored: {gw_addr}")
                restored += 1
            else:
                print(f"  [x] FAILED: {gw_addr}")
            import time
            time.sleep(0.1)
        print(f"\nKhoi phuc: {restored}/{len(missing)} gateways")


if __name__ == "__main__":
    main()
