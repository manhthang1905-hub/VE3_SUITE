#!/usr/bin/env python3
"""
IPv6 Pool Manager - Quan ly pool IPv6 dong qua MikroTik.
=========================================================

v1.0.555: Existing IPs mode - dung IP da co san tren router.
Router da co 255 IP (subnet 01-ff), pool chi QUAN LY, KHONG add/remove.

Pool states:
    available  → IP san sang dung (co tren router)
    in_use     → Dang duoc worker su dung
    burned     → Bi 403 (khong dung lai, nhung VAN GIU tren router)
    cooldown   → Tam nghi, se kha dung lai sau cooldown_time

Pool file: ipv6/pool.json (luu trang thai giua cac lan chay)
"""

import json
import time
import random
import threading
from pathlib import Path
from typing import Optional, List, Dict, Tuple
try:
    from ipv6.mikrotik_api import MikroTikAPI
except ModuleNotFoundError:
    from mikrotik_api import MikroTikAPI


class IPv6Pool:
    """
    Quan ly pool IPv6 addresses tu IP da co san tren router.

    Mode: EXISTING IPs - router da co tat ca IP, pool chi track trang thai.
    KHONG add/remove IP tren router.

    Flow:
        1. init() → scan router, lay IP trong allowed range → pool
        2. get_ip() → lay 1 IP available → mark in_use → tra ve
        3. release_ip(ip) → IP van OK → mark available (dung lai duoc)
        4. burn_ip(ip) → IP bi 403 → mark burned (GIU tren router)
        5. refill() → pool it IP → lay them IP tu router (da co san)
    """

    def __init__(
        self,
        mikrotik: MikroTikAPI,
        pool_file: str = None,
        min_pool_size: int = 3,
        max_pool_size: int = 20,
        cooldown_seconds: int = 3600,
        log_func=print,
    ):
        self.api = mikrotik
        self.pool_file = Path(pool_file) if pool_file else Path(__file__).parent / "pool.json"
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        self.cooldown_seconds = cooldown_seconds
        self.log = log_func
        self._lock = threading.Lock()

        # Pool data
        self.pool: List[Dict] = []
        # Tracking: addresses da tung dung (ke ca burned) - de khong dung lai
        self._burned_addresses: set = set()
        # v1.0.652: Track burned subnets voi timestamp de tu phuc hoi
        # {subnet_int: burned_at_timestamp}
        self._burned_subnets: Dict[int, float] = {}
        self._recover_cooldown = min(cooldown_seconds, 600)  # Max 10 phut

    # =========================================================================
    # INIT / LOAD / SAVE
    # =========================================================================

    def init(self) -> bool:
        """
        Khoi tao pool:
        1. Load pool.json (neu co)
        2. Scan router, lay IP trong allowed range vao pool
        3. Load TAT CA IP trong range vao pool (day la kho IPv6)
        """
        self.log("[POOL] Initializing IPv6 pool (existing IPs mode)...")

        # Load saved pool
        self._load_pool()

        # Scan router va dong bo
        self._sync_with_router()

        # v1.0.563: Load TAT CA IP trong allowed range vao pool (khong gioi han)
        # Pool la KHO IPv6 → can thay het tat ca IP de quan ly
        self._load_all_from_router()

        # v1.0.602: Auto-adjust max_pool_size theo so IP thuc te tren router
        if len(self.pool) > self.max_pool_size:
            self.max_pool_size = len(self.pool) + 10  # +10 du phong

        available = [p for p in self.pool if p["status"] == "available"]
        in_use = [p for p in self.pool if p["status"] == "in_use"]
        burned = len(self._burned_addresses)
        total_range = self.api.subnet_end - self.api.subnet_start + 1
        self.log(f"[POOL] Ready: {len(available)} available, {len(in_use)} in_use, {burned} burned (range: {total_range})")
        return len(available) > 0

    def _load_pool(self):
        """Load pool tu file."""
        if self.pool_file.exists():
            try:
                with open(self.pool_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.pool = data.get("pool", [])
                self._burned_addresses = set(data.get("burned_addresses", []))
                # v1.0.652: Load burned subnets timestamps
                _bs = data.get("burned_subnets", {})
                self._burned_subnets = {int(k): float(v) for k, v in _bs.items()}
                self.log(f"[POOL] Loaded {len(self.pool)} entries, {len(self._burned_addresses)} burned, {len(self._burned_subnets)} burned subnets")
            except Exception as e:
                self.log(f"[POOL] Load error: {e}")
                self.pool = []
                self._burned_addresses = set()
                self._burned_subnets = {}
        else:
            self.log("[POOL] No saved pool, starting fresh")

    def _save_pool(self):
        """Save pool ra file."""
        try:
            data = {
                "pool": self.pool,
                "burned_addresses": list(self._burned_addresses),
                "burned_subnets": {str(k): v for k, v in self._burned_subnets.items()},
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(self.pool_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self.log(f"[POOL] Save error: {e}")

    # =========================================================================
    # POOL OPERATIONS
    # =========================================================================

    def _get_gateway_for_ip(self, ip: str) -> str:
        """
        v1.0.580: Tinh gateway ::1 cho 1 IPv6 address.
        Gateway = /64 network address + 1, dung compressed format (khong co leading zeros).

        Args:
            ip: IPv6 address (khong co /prefix)

        Returns:
            Gateway address (vd: "2001:ee0:b004:3065::1")
        """
        import ipaddress
        try:
            addr = ipaddress.IPv6Address(ip)
            # Lay /64 network, +1 = gateway
            network = ipaddress.IPv6Network(f"{addr}/64", strict=False)
            gateway = network.network_address + 1
            return str(gateway)  # compressed format: "2001:ee0:b004:3098::1"
        except Exception:
            return ""

    def get_ip(self) -> Optional[Tuple[str, str]]:
        """
        Lay 1 IPv6 address tu pool.

        v1.0.608: FIFO queue - lay tu dau, chuyen ve cuoi.
        IP vua dung xong luon o cuoi → duoc cap lai sau cung → tranh trung.

        Returns:
            Tuple (ip, gateway) hoac None neu het.
            ip: IPv6 address (khong co /prefix)
            gateway: Gateway ::1 cua subnet
        """
        with self._lock:
            # v1.0.652: Tu phuc hoi burned subnets truoc khi tim IP
            self._auto_recover_subnets()

            # Tim IP available (tu dau list - FIFO)
            for i, entry in enumerate(self.pool):
                if entry["status"] == "available":
                    entry["status"] = "in_use"
                    entry["used_at"] = time.time()
                    entry["use_count"] = entry.get("use_count", 0) + 1
                    # v1.0.608: FIFO - chuyen entry ve cuoi list
                    self.pool.append(self.pool.pop(i))
                    self._save_pool()
                    ip = entry["address"]
                    gateway = self._get_gateway_for_ip(ip)
                    self.log(f"[POOL] GET: {ip} gw={gateway} (#{entry['use_count']})")
                    return ip, gateway

            # Het IP available → thu refill
            self.log("[POOL] No available IPs, refilling...")
            added = self._refill(count=self.min_pool_size)
            if added:
                for i, entry in enumerate(self.pool):
                    if entry["status"] == "available":
                        entry["status"] = "in_use"
                        entry["used_at"] = time.time()
                        entry["use_count"] = entry.get("use_count", 0) + 1
                        self.pool.append(self.pool.pop(i))
                        self._save_pool()
                        ip = entry["address"]
                        gateway = self._get_gateway_for_ip(ip)
                        self.log(f"[POOL] GET: {ip} gw={gateway} (#{entry['use_count']})")
                        return ip, gateway

            self.log("[POOL] [!] POOL EMPTY - No more IPs!")
            return None

    def register_ip(self, address: str, worker: str = "unknown") -> Optional[Tuple[str, str]]:
        """
        v1.0.603: VM dang ky IP dang dung voi pool.

        Khi VM khoi dong va da co IPv6 tu lan truoc, goi register de:
        - Pool biet IP nay dang duoc dung (tranh cap cho VM khac)
        - Match theo SUBNET (vi worker IP co the khac sau reset)

        Flow:
            1. Tim entry trong pool co cung subnet
            2. Neu tim thay → danh dau in_use, tra ve IP + gateway
            3. Neu khong tim thay → tra None (VM nen goi get_ip)

        Args:
            address: IPv6 address VM dang dung
            worker: Ten worker (vd: "KA4-T3_chrome0")

        Returns:
            Tuple (pool_ip, gateway) hoac None
        """
        import ipaddress
        with self._lock:
            # Extract subnet tu address VM dang dung
            # v1.0.621: Fix format mismatch - pool luu "65" nhung extract ra "3065"
            try:
                addr = ipaddress.IPv6Address(address)
                parts = addr.exploded.split(":")
                subnet_full = parts[3]  # "3065" (4 chars, co leading zeros)
                # Pool luu subnet 2 ky tu cuoi: "3065" → "65", "30bc" → "bc"
                subnet_hex_short = subnet_full[-2:]  # LUON lay 2 ky tu cuoi
                self.log(f"[POOL] REGISTER: extract subnet={subnet_full} → short={subnet_hex_short}")
            except Exception:
                self.log(f"[POOL] REGISTER: Invalid address: {address}")
                return None

            # Tim entry co cung subnet
            for entry in self.pool:
                if entry.get("subnet_hex") == subnet_hex_short and entry["status"] == "available":
                    entry["status"] = "in_use"
                    entry["used_at"] = time.time()
                    entry["use_count"] = entry.get("use_count", 0) + 1
                    self._save_pool()
                    ip = entry["address"]
                    gateway = self._get_gateway_for_ip(ip)
                    self.log(f"[POOL] REGISTER: {worker} → subnet {subnet_hex_short} → {ip} (da danh dau in_use)")
                    return ip, gateway

            # Subnet da in_use (VM khac hoac chinh VM nay truoc do)
            for entry in self.pool:
                if entry.get("subnet_hex") == subnet_hex_short and entry["status"] == "in_use":
                    ip = entry["address"]
                    gateway = self._get_gateway_for_ip(ip)
                    self.log(f"[POOL] REGISTER: {worker} → subnet {subnet_hex_short} da in_use, tra ve {ip}")
                    return ip, gateway

            self.log(f"[POOL] REGISTER: subnet {subnet_hex_short} khong tim thay trong pool")
            return None

    def release_ip(self, address: str):
        """
        Tra IP lai pool (van dung duoc, khong bi 403).
        v1.0.608: FIFO - chuyen ve cuoi list de IP khac duoc cap truoc.

        Args:
            address: IPv6 address
        """
        with self._lock:
            # v1.0.621: Match exact address hoac subnet (sau RESET address co the khac)
            import ipaddress as _ipa
            for i, entry in enumerate(self.pool):
                if entry["address"] == address and entry["status"] == "in_use":
                    entry["status"] = "available"
                    entry["released_at"] = time.time()
                    self.pool.append(self.pool.pop(i))
                    self._save_pool()
                    self.log(f"[POOL] RELEASE: {address} → available (cuoi queue)")
                    return

            # Fallback: match theo subnet (VM co IP cu, pool co IP moi cho cung subnet)
            try:
                parts = _ipa.IPv6Address(address).exploded.split(":")
                vm_subnet = parts[3][-2:]  # "3065" → "65"
                for i, entry in enumerate(self.pool):
                    if entry.get("subnet_hex") == vm_subnet and entry["status"] == "in_use":
                        entry["status"] = "available"
                        entry["released_at"] = time.time()
                        self.pool.append(self.pool.pop(i))
                        self._save_pool()
                        self.log(f"[POOL] RELEASE: subnet {vm_subnet} ({address}) → available")
                        return
            except Exception:
                pass

            self.log(f"[POOL] Release: {address} not found in pool")

    def burn_ip(self, address: str, reason: str = "403"):
        """
        v1.0.599: Burn IP = xoa gateway cu tren router + tao gateway moi voi subnet random.
        Giong rotate_all nhung chi cho 1 IP.

        Flow:
            1. Tim subnet cua IP bi burn
            2. Xoa gateway cu (::1/64) tren router
            3. Chon subnet MOI (random, chua dung)
            4. Tao gateway moi tren router
            5. Them IP moi vao pool (available)
            6. Xoa IP cu khoi pool

        Args:
            address: IPv6 address
            reason: Ly do burn (vd: "403", "blocked")
        """
        with self._lock:
            burned_entry = None
            burned_idx = None
            for i, entry in enumerate(self.pool):
                if entry["address"] == address:
                    burned_entry = entry
                    burned_idx = i
                    break

            if not burned_entry:
                self.log(f"[POOL] Burn: {address} not found")
                return

            old_subnet = burned_entry.get("subnet")
            old_subnet_hex = burned_entry.get("subnet_hex", "")

            # v1.0.663: Guard - KHONG xoa reserved subnet
            if old_subnet is not None and self.api._is_reserved_subnet(old_subnet):
                self.log(f"[POOL] BURN: subnet {old_subnet_hex} la RESERVED → KHONG xoa gateway!")
                # Chi xoa khoi pool, KHONG dong vao router
                self.pool.pop(burned_idx)
                self._save_pool()
                return

            # 1. Xoa gateway cu tren router
            if old_subnet is not None:
                router_addrs = self.api.list_ipv6_addresses()
                for rentry in router_addrs:
                    addr_full = rentry.get("address", "")
                    addr_clean = addr_full.split("/")[0]
                    interface = rentry.get("interface", "")
                    addr_id = rentry.get(".id", "")

                    if interface != self.api.interface:
                        continue

                    r_subnet = self.api._extract_subnet(addr_full)
                    if r_subnet == old_subnet and (addr_clean.endswith("::1") or addr_clean.endswith(":0001")):
                        if self.api.remove_ipv6_address(addr_id):
                            self.log(f"[POOL] BURN: Xoa gateway subnet {old_subnet_hex} tren router")
                        else:
                            self.log(f"[POOL] BURN: [!] Khong xoa duoc gateway subnet {old_subnet_hex}")
                        break

            # 2. Xoa IP cu khoi pool
            self.pool.pop(burned_idx)
            self._burned_addresses.add(address)

            # v1.0.652: Tu phuc hoi burned subnets truoc khi tim subnet moi
            self._auto_recover_subnets()

            # 3. Chon subnet MOI (random, chua co trong pool va chua burned)
            pool_subnets = {e.get("subnet") for e in self.pool if e.get("subnet") is not None}
            # Uu tien tranh subnet cu (vi bi 403)
            pool_subnets_strict = pool_subnets | {old_subnet}

            full_range = list(range(self.api.subnet_start, self.api.subnet_end + 1))
            random.shuffle(full_range)

            new_subnet = None
            # Lan 1: Tim subnet KHONG trung pool VA khong phai subnet cu
            for s in full_range:
                if s not in pool_subnets_strict:
                    new_subnet = s
                    break

            # Lan 2: Neu het subnet moi, dung lai subnet cu (gateway da xoa, worker IP moi random)
            if new_subnet is None and old_subnet is not None:
                new_subnet = old_subnet
                self.log(f"[POOL] BURN: Het subnet moi → dung lai subnet {old_subnet:02x} (IP se random moi)")

            if new_subnet is None:
                self.log(f"[POOL] BURN: {address} ({reason}) → khong tim duoc subnet!")
                self._save_pool()
                return

            # 4. Tao gateway moi tren router
            new_subnet_hex = f"{new_subnet:02x}"
            gw_addr = f"{self.api.prefix}{new_subnet_hex}::1/64"
            gw_ok = self.api.add_ipv6_address(gw_addr)

            if not gw_ok:
                self.log(f"[POOL] BURN: [!] Khong tao duoc gateway cho subnet {new_subnet_hex}")
                self._save_pool()
                return

            # 5. Generate random worker IP + them vao pool
            worker_ip_full = self.api.build_ipv6_address(new_subnet, full_random=True)
            worker_ip = worker_ip_full.split("/")[0]
            gateway = self._get_gateway_for_ip(worker_ip)

            new_entry = {
                "address": worker_ip,
                "full_address": worker_ip_full,
                "subnet": new_subnet,
                "subnet_hex": new_subnet_hex,
                "gateway": gateway,
                "status": "available",
                "added_at": time.time(),
            }
            self.pool.append(new_entry)
            self._save_pool()

            # v1.0.652: Track burned subnet + timestamp de tu phuc hoi
            if old_subnet is not None:
                self._burned_subnets[old_subnet] = time.time()

            self.log(f"[POOL] BURN: {address} ({reason}) → xoa subnet {old_subnet_hex}, "
                     f"tao moi subnet {new_subnet_hex} ({worker_ip})")

    def _auto_recover_subnets(self):
        """
        v1.0.652: Tu phuc hoi burned subnets sau cooldown.
        Subnet bi 403 → doi _recover_cooldown → tao gateway moi + IP moi → available.
        Goi TU TRONG _lock (caller da acquire lock).
        """
        if not self._burned_subnets:
            return

        now = time.time()
        pool_subnets = {e.get("subnet") for e in self.pool if e.get("subnet") is not None}
        recovered = []

        for subnet, burned_at in list(self._burned_subnets.items()):
            elapsed = now - burned_at
            if elapsed < self._recover_cooldown:
                continue
            # Subnet da qua cooldown → phuc hoi
            if subnet in pool_subnets:
                # Subnet da co trong pool (co the da duoc reuse) → chi xoa tracking
                recovered.append(subnet)
                continue

            # Tao gateway moi tren router + them IP vao pool
            subnet_hex = f"{subnet:02x}"
            gw_addr = f"{self.api.prefix}{subnet_hex}::1/64"
            try:
                gw_ok = self.api.add_ipv6_address(gw_addr)
                if not gw_ok:
                    # Co the da ton tai → van OK
                    pass

                worker_ip_full = self.api.build_ipv6_address(subnet, full_random=True)
                worker_ip = worker_ip_full.split("/")[0]
                gateway = self._get_gateway_for_ip(worker_ip)

                new_entry = {
                    "address": worker_ip,
                    "full_address": worker_ip_full,
                    "subnet": subnet,
                    "subnet_hex": subnet_hex,
                    "gateway": gateway,
                    "status": "available",
                    "added_at": now,
                }
                self.pool.append(new_entry)
                recovered.append(subnet)
                self.log(f"[POOL] RECOVER: subnet {subnet_hex} phuc hoi sau {int(elapsed)}s ({worker_ip})")
            except Exception as e:
                self.log(f"[POOL] RECOVER: subnet {subnet_hex} FAIL: {e}")

        if recovered:
            for s in recovered:
                self._burned_subnets.pop(s, None)
            self._save_pool()
            self.log(f"[POOL] RECOVER: {len(recovered)} subnets phuc hoi, "
                     f"con {len(self._burned_subnets)} dang cooldown")

    def rotate_ip(self, current_address: str, reason: str = "403") -> Optional[Tuple[str, str]]:
        """
        Doi IP: burn IP cu → lay IP moi.

        Args:
            current_address: IP dang dung
            reason: Ly do doi

        Returns:
            Tuple (new_ip, gateway) hoac None
        """
        self.log(f"[POOL] ROTATE: {current_address} ({reason})")
        self.burn_ip(current_address, reason)
        result = self.get_ip()
        if result:
            new_ip, gateway = result
            self.log(f"[POOL] ROTATE: {current_address} → {new_ip} gw={gateway}")
            return new_ip, gateway
        else:
            self.log(f"[POOL] ROTATE FAILED: No new IP available!")
            return None

    # =========================================================================
    # POOL MANAGEMENT
    # =========================================================================

    def _load_all_from_router(self):
        """
        v1.0.583: Load subnets tu gateway addresses tren router.
        Moi gateway ::1/64 = 1 subnet kha dung.
        Worker IP duoc generate random LOCAL, KHONG nam tren router (tranh DAD conflict).
        """
        router_addrs = self.api.list_ipv6_addresses()
        pool_subnets = {e.get("subnet_hex", "") for e in self.pool}
        added = 0

        for entry in router_addrs:
            addr_full = entry.get("address", "")
            addr_clean = addr_full.split("/")[0]
            interface = entry.get("interface", "")

            if interface != self.api.interface:
                continue

            subnet = self.api._extract_subnet(addr_full)
            if subnet is None:
                continue
            if subnet < self.api.subnet_start or subnet > self.api.subnet_end:
                continue

            # v1.0.583: Chi load GATEWAY addresses (::1/64) - moi gateway = 1 subnet
            if not (addr_clean.endswith("::1") or addr_clean.endswith(":0001")):
                continue

            subnet_hex = f"{subnet:02x}"
            if subnet_hex in pool_subnets:
                continue

            # Generate random worker IP LOCAL (chi VM dung, router KHONG co)
            worker_ip_full = self.api.build_ipv6_address(subnet, full_random=True)
            worker_ip = worker_ip_full.split("/")[0]  # Bo /128

            new_entry = {
                "address": worker_ip,
                "full_address": worker_ip_full,
                "subnet": subnet,
                "subnet_hex": subnet_hex,
                "router_id": "",
                "interface": interface,
                "status": "available",
                "use_count": 0,
                "created_at": time.time(),
                "used_at": None,
            }
            self.pool.append(new_entry)
            pool_subnets.add(subnet_hex)
            added += 1

        if added > 0:
            self._save_pool()
            self.log(f"[POOL] Loaded {added} IPs from router (total pool: {len(self.pool)})")

    def _refill_if_needed(self):
        """Them IP tu router neu pool duoi min_pool_size."""
        available = [p for p in self.pool if p["status"] == "available"]
        if len(available) < self.min_pool_size:
            need = self.min_pool_size - len(available)
            self.log(f"[POOL] Refill: {len(available)} available < {self.min_pool_size} min, adding {need}")
            self._refill(count=need)

    def _refill(self, count: int = 1) -> int:
        """
        v1.0.583: Tim subnets co gateway tren router ma chua co trong pool.
        Generate random worker IP cho moi subnet (LOCAL, khong tren router).

        Returns:
            So IP da them
        """
        router_addrs = self.api.list_ipv6_addresses()
        pool_subnets = {e.get("subnet_hex", "") for e in self.pool}
        candidates = []

        for entry in router_addrs:
            addr_full = entry.get("address", "")
            addr_clean = addr_full.split("/")[0]
            interface = entry.get("interface", "")

            if interface != self.api.interface:
                continue

            subnet = self.api._extract_subnet(addr_full)
            if subnet is None:
                continue
            if subnet < self.api.subnet_start or subnet > self.api.subnet_end:
                continue

            # v1.0.583: Chi gateways (::1)
            if not (addr_clean.endswith("::1") or addr_clean.endswith(":0001")):
                continue

            subnet_hex = f"{subnet:02x}"
            if subnet_hex in pool_subnets:
                continue

            # Generate random worker IP LOCAL
            worker_ip_full = self.api.build_ipv6_address(subnet, full_random=True)
            worker_ip = worker_ip_full.split("/")[0]

            candidates.append({
                "address": worker_ip,
                "full_address": worker_ip_full,
                "subnet": subnet,
                "subnet_hex": subnet_hex,
                "router_id": "",
                "interface": interface,
            })

        if not candidates:
            # v1.0.602: Khong co gateway co san → tao gateway MOI cho subnets chua dung
            pool_subnets_int = {e.get("subnet") for e in self.pool if e.get("subnet") is not None}
            full_range = list(range(self.api.subnet_start, self.api.subnet_end + 1))
            random.shuffle(full_range)

            for s in full_range:
                if len(candidates) >= count:
                    break
                if s in pool_subnets_int:
                    continue
                # v1.0.663: Skip reserved subnet
                if self.api._is_reserved_subnet(s):
                    continue

                # Tao gateway moi tren router
                s_hex = f"{s:02x}"
                gw_addr = f"{self.api.prefix}{s_hex}::1/64"
                if self.api.add_ipv6_address(gw_addr):
                    worker_ip_full = self.api.build_ipv6_address(s, full_random=True)
                    worker_ip = worker_ip_full.split("/")[0]
                    candidates.append({
                        "address": worker_ip,
                        "full_address": worker_ip_full,
                        "subnet": s,
                        "subnet_hex": s_hex,
                        "router_id": "",
                        "interface": self.api.interface,
                    })
                    self.log(f"[POOL] TAO MOI gateway subnet {s_hex} tren router")
                else:
                    self.log(f"[POOL] [!] Khong tao duoc gateway subnet {s_hex}")

            if not candidates:
                self.log("[POOL] No more subnets available!")
                return 0

        random.shuffle(candidates)

        added = 0
        for cand in candidates:
            if added >= count:
                break

            active_count = len([p for p in self.pool if p["status"] in ("available", "in_use")])
            if active_count >= self.max_pool_size:
                self.log(f"[POOL] Max pool size reached ({self.max_pool_size})")
                break

            entry = {
                "address": cand["address"],
                "full_address": cand["full_address"],
                "subnet": cand["subnet"],
                "subnet_hex": cand["subnet_hex"],
                "router_id": "",
                "status": "available",
                "created_at": time.time(),
                "use_count": 0,
            }
            self.pool.append(entry)
            added += 1
            self.log(f"[POOL] Added subnet: {cand['subnet_hex']} → {cand['address']}")

        if added:
            self._save_pool()
        return added

    def rotate_all(self) -> int:
        """
        v1.0.566: Doi TAT CA IP - xoa IP cu, tao IP moi voi SUBNET + host RANDOM.

        /56 prefix = 256 subnet /64 (00-ff). Google block theo /64 nen
        phai doi SUBNET (3065 → 30a3) chu khong chi doi host (::1 → ::abc).

        Flow:
            1. Xoa tat ca IP trong allowed range tren router
            2. Clear pool + burned_addresses
            3. Chon NGAU NHIEN subnet moi tu full range (00-ff), tranh trung subnet cu
            4. Add IP moi: subnet random + host random
            5. Load vao pool

        Returns:
            So IP moi da them
        """
        with self._lock:
            self.log("[POOL] === ROTATE ALL: Doi tat ca IP (subnet + host) ===")

            # 1. Lay tat ca IP trong allowed range tren router
            router_addrs = self.api.list_ipv6_addresses()
            old_ids = []  # router .id de xoa
            old_subnets = set()  # subnet cu de tranh chon lai

            for entry in router_addrs:
                addr_full = entry.get("address", "")
                interface = entry.get("interface", "")
                addr_id = entry.get(".id", "")

                if interface != self.api.interface:
                    continue

                subnet = self.api._extract_subnet(addr_full)
                if subnet is None:
                    continue
                if subnet < self.api.subnet_start or subnet > self.api.subnet_end:
                    continue

                # v1.0.663: KHONG dong vao reserved subnet
                if self.api._is_reserved_subnet(subnet):
                    continue

                old_ids.append(addr_id)
                old_subnets.add(subnet)

            self.log(f"[POOL] Tim thay {len(old_ids)} IP cu tren router (subnets: {len(old_subnets)})")

            # 2. Xoa tat ca IP cu tren router (delay giua cac lenh de khong qua tai router)
            # v1.0.665: Delay 0.1s giua moi operation de tranh router CPU spike
            # → giam anh huong toi IPv6 traffic khac (RDP, YouTube)
            removed = 0
            for addr_id in old_ids:
                if self.api.remove_ipv6_address(addr_id):
                    removed += 1
                time.sleep(0.1)  # Cho router xu ly xong truoc khi tiep
            self.log(f"[POOL] Da xoa {removed}/{len(old_ids)} IP cu tren router")

            # 3. Clear pool va burned list
            self.pool = []
            self._burned_addresses = set()
            self._save_pool()
            self.log("[POOL] Da clear pool va burned list")

            # 4. Tao IP moi cho TOAN BO subnet trong range (65-ff = 155)
            full_range = list(range(self.api.subnet_start, self.api.subnet_end + 1))
            random.shuffle(full_range)
            selected = full_range

            self.log(f"[POOL] Tao moi {len(selected)} IP cho toan bo range "
                     f"({self.api.subnet_start:02x}-{self.api.subnet_end:02x})")

            # 5. Add GATEWAY cho moi subnet (chi gateway, KHONG add worker IP)
            # v1.0.583: Worker IP chi dung tren VM, KHONG add vao MikroTik
            # Neu add worker IP vao router → VM cung add → DAD conflict → "Duplicate" → fail
            added = 0
            for subnet in selected:
                subnet_str = f"{subnet:02x}"
                gw_addr = f"{self.api.prefix}{subnet_str}::1/64"
                gw_result = self.api.add_ipv6_address(gw_addr)
                if gw_result:
                    added += 1
                else:
                    self.log(f"[POOL] [!] Cannot create gateway for subnet {subnet:02x}, skip")
                time.sleep(0.1)  # v1.0.665: Delay de khong qua tai router

            self.log(f"[POOL] Da them {added}/{len(selected)} gateway tren router")

            # 6. Load tat ca IP moi vao pool
            self._load_all_from_router()

            available = [p for p in self.pool if p["status"] == "available"]
            self.log(f"[POOL] === ROTATE ALL DONE: {len(available)} IP san sang ===")
            return added

    def _sync_with_router(self):
        """
        v1.0.583: Dong bo pool voi trang thai thuc tren router.
        Check GATEWAY (khong phai worker IP) vi worker IP chi tren VM.
        - Subnet con gateway tren router → giu
        - Subnet mat gateway → mark burned
        """
        router_addrs = self.api.list_ipv6_addresses()

        # Build set of subnets that have gateways on router
        router_subnets = set()
        for entry in router_addrs:
            addr = entry.get("address", "").split("/")[0]
            interface = entry.get("interface", "")
            if interface != self.api.interface:
                continue
            if addr.endswith("::1") or addr.endswith(":0001"):
                subnet = self.api._extract_subnet(entry.get("address", ""))
                if subnet is not None:
                    router_subnets.add(f"{subnet:02x}")

        for entry in self.pool:
            if entry["status"] in ("available", "in_use"):
                subnet_hex = entry.get("subnet_hex", "")
                if subnet_hex and subnet_hex not in router_subnets:
                    self.log(f"[POOL] Sync: subnet {subnet_hex} gateway missing → burned")
                    entry["status"] = "burned"
                    entry["burn_reason"] = "sync_missing"
                    self._burned_addresses.add(entry["address"])

        # Cleanup
        before = len(self.pool)
        self.pool = [p for p in self.pool if p["status"] != "burned"]
        if before != len(self.pool):
            self.log(f"[POOL] Sync cleanup: removed {before - len(self.pool)} burned entries")

        self._save_pool()

    # =========================================================================
    # STATUS / INFO
    # =========================================================================

    def get_status(self) -> Dict:
        """Tra ve trang thai pool."""
        with self._lock:
            stats = {"available": 0, "in_use": 0, "burned": 0, "total": len(self.pool)}
            for entry in self.pool:
                status = entry.get("status", "unknown")
                if status in stats:
                    stats[status] += 1

            # Tinh IP con lai (trong allowed range, chua burned)
            total_range = self.api.subnet_end - self.api.subnet_start + 1
            stats["burned_total"] = len(self._burned_addresses)
            stats["range_total"] = total_range
            stats["range_remaining"] = total_range - len(self._burned_addresses)
            # v1.0.652: Burned subnets dang cooldown
            stats["subnets_cooling"] = len(self._burned_subnets)
            stats["recover_cooldown"] = self._recover_cooldown
            return stats

    def get_pool_entries(self) -> List[Dict]:
        """Tra ve ban sao cua pool."""
        with self._lock:
            return list(self.pool)

    def cleanup_burned(self):
        """Xoa cac entry burned khoi pool (giu burned_addresses)."""
        with self._lock:
            before = len(self.pool)
            self.pool = [p for p in self.pool if p["status"] != "burned"]
            after = len(self.pool)
            if before != after:
                self._save_pool()
                self.log(f"[POOL] Cleanup: removed {before - after} burned entries")

    def reset_pool(self):
        """
        Reset pool state - KHONG xoa IP khoi router.
        Chi xoa pool tracking data.
        """
        with self._lock:
            self.log("[POOL] RESET: Clearing pool tracking data...")
            self.pool = []
            self._burned_addresses = set()
            self._burned_subnets = {}
            self._save_pool()
            self.log("[POOL] RESET complete (IPs unchanged on router)")
