#!/usr/bin/env python3
"""
MikroTik REST API Client - Quan ly IPv6 addresses qua Router API.
==================================================================

VNPT Fiber Xtra 2: Prefix /56 → 256 subnet /64 (3000 → 30ff)
Subnet rotate: 3052 → 30ff (174 subnets kha dung)

MikroTik RouterOS 7+ REST API:
    GET    /rest/ipv6/address        → List all IPv6 addresses
    PUT    /rest/ipv6/address        → Add new address
    DELETE /rest/ipv6/address/{id}   → Remove address

Flow:
    Pool can IP → add_ipv6(subnet_hex) → MikroTik add vao interface
    IP bi 403   → remove_ipv6(address_id) → MikroTik xoa khoi interface
    Lay IP moi  → pick subnet chua dung → add_ipv6() → IP moi san sang
"""

import requests
import urllib3
import time
from typing import Optional, List, Dict, Tuple

# Tat SSL warning (MikroTik dung self-signed cert)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class MikroTikAPI:
    """
    Client giao tiep voi MikroTik RouterOS REST API.

    Args:
        host: IP cua router (mac dinh 192.168.88.1)
        username: Tai khoan admin
        password: Mat khau
        interface: Ten interface IPv6 (mac dinh ether1)
        prefix: IPv6 prefix /56 (vd: 2001:ee0:4f89:3000)
        subnet_start: Subnet bat dau rotate (hex, vd: 0x52 = 3052)
        subnet_end: Subnet ket thuc rotate (hex, vd: 0xff = 30ff)
    """

    def __init__(
        self,
        host: str = "192.168.88.1",
        username: str = "admin",
        password: str = "",
        interface: str = "ether1",
        prefix: str = "",
        subnet_start: int = 0x66,  # 102 decimal, tranh 100 IP YouTube (subnet 01-65)
        subnet_end: int = 0xFF,
        log_func=print,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.interface = interface
        self.prefix = prefix.rstrip(":")  # "2001:ee0:4f89:30" (bo ":" cuoi)
        self.subnet_start = subnet_start
        self.subnet_end = subnet_end
        self.log = log_func

        # v1.0.663: RESERVED SUBNETS - Pool KHONG BAO GIO duoc dong vao
        # Subnet 01-65 hex (1-101 decimal) = 100 IP YouTube
        # Day la HANG RAO CUNG - bat ke subnet_start config the nao
        self._reserved_start = 0x01
        self._reserved_end = 0x65  # 100 IP YouTube

        self.session = requests.Session()
        self.session.auth = (username, password)
        self.session.verify = False  # MikroTik self-signed cert
        self.session.headers.update({"Content-Type": "application/json"})

        # v1.0.554: Auto-detect HTTPS vs HTTP
        self.base_url = self._detect_base_url()

    def _detect_base_url(self) -> str:
        """Thu HTTPS truoc, neu fail thu HTTP."""
        for scheme in ["https", "http"]:
            try:
                url = f"{scheme}://{self.host}/rest/system/identity"
                resp = self.session.get(url, timeout=5)
                if resp.status_code in (200, 401):
                    self.log(f"[MikroTik] Using {scheme.upper()}://{self.host}")
                    return f"{scheme}://{self.host}/rest"
            except Exception:
                pass
        # Fallback HTTPS
        return f"https://{self.host}/rest"

    # =========================================================================
    # CORE API METHODS
    # =========================================================================

    def list_ipv6_addresses(self) -> List[Dict]:
        """
        Lay danh sach tat ca IPv6 addresses tren router.

        Returns:
            List of dicts: [{".id": "*1", "address": "2001:...:1/64", "interface": "ether1", ...}]
        """
        try:
            resp = self.session.get(f"{self.base_url}/ipv6/address", timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            self.log(f"[MikroTik] List IPv6 failed: {e}")
            return []

    def _is_reserved_subnet(self, subnet: int) -> bool:
        """
        v1.0.663: Check subnet co nam trong reserved range khong.
        Reserved = 100 IP YouTube (subnet 01-65) - KHONG BAO GIO duoc dong vao.
        """
        return self._reserved_start <= subnet <= self._reserved_end

    def _check_address_reserved(self, address: str) -> bool:
        """
        v1.0.663: Check 1 IPv6 address co thuoc reserved subnet khong.
        Returns True neu RESERVED (KHONG duoc dong vao).
        """
        subnet = self._extract_subnet(address)
        if subnet is not None and self._is_reserved_subnet(subnet):
            self.log(f"[MikroTik] ⛔ BLOCKED: subnet {subnet:02x} nam trong reserved range "
                     f"({self._reserved_start:02x}-{self._reserved_end:02x}) - KHONG DONG VAO!")
            return True
        return False

    def add_ipv6_address(self, address: str, interface: str = None) -> Optional[str]:
        """
        Them IPv6 address vao interface.

        Args:
            address: IPv6 address voi prefix length (vd: "2001:ee0:4f89:3052::1/64")
            interface: Interface name (mac dinh dung self.interface)

        Returns:
            Address ID (vd: "*A") neu thanh cong, None neu that bai
        """
        # v1.0.663: Guard - khong add vao reserved subnet
        if self._check_address_reserved(address):
            return None

        iface = interface or self.interface
        data = {
            "address": address,
            "interface": iface,
            "advertise": "false",  # Khong quang ba ra mang
        }

        try:
            resp = self.session.put(f"{self.base_url}/ipv6/address", json=data, timeout=10)
            if resp.status_code in (200, 201):
                result = resp.json()
                addr_id = result.get(".id", "")
                self.log(f"[MikroTik] Added: {address} on {iface} → ID={addr_id}")
                return addr_id
            else:
                self.log(f"[MikroTik] Add failed ({resp.status_code}): {resp.text}")
                return None
        except Exception as e:
            self.log(f"[MikroTik] Add IPv6 error: {e}")
            return None

    def remove_ipv6_address(self, address_id: str) -> bool:
        """
        Xoa IPv6 address theo ID.

        Args:
            address_id: MikroTik address ID (vd: "*A", "*1B")

        Returns:
            True neu thanh cong
        """
        try:
            resp = self.session.delete(
                f"{self.base_url}/ipv6/address/{address_id}", timeout=10
            )
            if resp.status_code in (200, 204):
                self.log(f"[MikroTik] Removed: ID={address_id}")
                return True
            else:
                self.log(f"[MikroTik] Remove failed ({resp.status_code}): {resp.text}")
                return False
        except Exception as e:
            self.log(f"[MikroTik] Remove IPv6 error: {e}")
            return False

    def remove_ipv6_by_address(self, address: str) -> bool:
        """
        Xoa IPv6 theo address string (tim ID roi xoa).

        Args:
            address: IPv6 address (vd: "2001:ee0:4f89:3052::1/64")
        """
        all_addrs = self.list_ipv6_addresses()
        for entry in all_addrs:
            if entry.get("address", "").startswith(address.split("/")[0]):
                return self.remove_ipv6_address(entry[".id"])
        self.log(f"[MikroTik] Address not found: {address}")
        return False

    # =========================================================================
    # SUBNET HELPERS
    # =========================================================================

    def build_ipv6_address(self, subnet_hex: int, host_id: int = 1, full_random: bool = False) -> str:
        """
        Tao IPv6 address tu subnet hex.

        Args:
            subnet_hex: Subnet number (vd: 0x52 → 3052)
            host_id: Host part (mac dinh ::1, chi dung khi full_random=False)
            full_random: True = tao 64-bit Privacy Extension host (RFC 4941)

        Returns:
            Full IPv6 address voi /128

        Note: Dung /128 de chi assign 1 IP duy nhat (khong phai ca subnet)

        Examples:
            full_random=False: 2001:ee0:b004:3052::1/128
            full_random=True:  2001:ee0:b004:3052:8f2e:41bc:d7a0:3e15/128
        """
        subnet_str = f"{subnet_hex:02x}"
        full_prefix = f"{self.prefix}{subnet_str}"

        if full_random:
            host_str = self._generate_privacy_host()
            return f"{full_prefix}:{host_str}/128"
        else:
            return f"{full_prefix}::{host_id:x}/128"

    @staticmethod
    def _generate_privacy_host() -> str:
        """
        Tao 64-bit Interface ID giong Privacy Extension (RFC 4941).

        Dac diem cua Privacy Extension address:
        - 64-bit random Interface ID
        - Bit U (bit 6 cua byte dau) = 0 (khong phai tu MAC)
        - Bit G (bit 7 cua byte dau) = 0 (unicast)
        - Trong giong IP that cua Windows/Linux/macOS

        Returns:
            Host string 4 groups: "8f2e:41bc:d7a0:3e15"
        """
        import random as _rnd

        # Tao 8 bytes random
        host_bytes = [_rnd.randint(0, 255) for _ in range(8)]

        # RFC 4941: Clear bit U (bit 6) va bit G (bit 7) cua byte dau
        # Bit 6 = 0: "not globally unique" (Privacy Extension)
        # Bit 7 = 0: "unicast"
        host_bytes[0] &= 0b11111100  # Clear bit 1 (G) va bit 0... wait

        # IPv6 Interface ID: byte 0 bit 6 = Universal/Local flag
        # RFC 4941: set bit 6 = 0 (locally assigned, khong phai tu MAC)
        host_bytes[0] &= ~0x02  # Clear bit 6 (U flag) → local

        # Tranh host = 0 (network address) hoac ffff:ffff:ffff:ffff (broadcast-like)
        if all(b == 0 for b in host_bytes):
            host_bytes[7] = _rnd.randint(1, 254)
        if all(b == 0xFF for b in host_bytes):
            host_bytes[7] = _rnd.randint(0, 254)

        # Format thanh 4 groups
        g1 = (host_bytes[0] << 8) | host_bytes[1]
        g2 = (host_bytes[2] << 8) | host_bytes[3]
        g3 = (host_bytes[4] << 8) | host_bytes[5]
        g4 = (host_bytes[6] << 8) | host_bytes[7]

        return f"{g1:x}:{g2:x}:{g3:x}:{g4:x}"

    def get_available_subnets(self) -> List[int]:
        """
        Tra ve danh sach subnet hex kha dung (chua dung).

        Returns:
            List subnet hex numbers (vd: [0x52, 0x53, ..., 0xff])
        """
        return list(range(self.subnet_start, self.subnet_end + 1))

    def get_used_subnets(self) -> List[int]:
        """
        Lay cac subnet dang duoc su dung tren router.

        Returns:
            List subnet hex numbers dang active
        """
        all_addrs = self.list_ipv6_addresses()
        used = []

        for entry in all_addrs:
            addr = entry.get("address", "")
            # Parse subnet tu address
            subnet = self._extract_subnet(addr)
            if subnet is not None and self.subnet_start <= subnet <= self.subnet_end:
                used.append(subnet)

        return used

    def _extract_subnet(self, address: str) -> Optional[int]:
        """
        Extract subnet hex tu IPv6 address.

        "2001:ee0:4f89:3052::1/128" → 0x52
        """
        try:
            # Bo /prefix
            addr_part = address.split("/")[0]
            # Split groups
            groups = addr_part.split(":")
            # Tim group chua subnet (group thu 4, 0-indexed = 3)
            if len(groups) >= 4:
                subnet_group = groups[3]  # "3052"
                # Lay 2 ky tu cuoi
                if len(subnet_group) >= 2:
                    subnet_hex = subnet_group[-2:]
                    return int(subnet_hex, 16)
        except (ValueError, IndexError):
            pass
        return None

    # =========================================================================
    # CONNECTIVITY TEST
    # =========================================================================

    def test_connection(self) -> bool:
        """Test ket noi toi MikroTik REST API."""
        try:
            resp = self.session.get(
                f"{self.base_url}/system/identity", timeout=5
            )
            if resp.status_code == 200:
                identity = resp.json()
                name = identity.get("name", "unknown")
                self.log(f"[MikroTik] Connected: {name} ({self.host})")
                return True
            elif resp.status_code == 401:
                self.log(f"[MikroTik] Auth failed! Check username/password")
                return False
            else:
                self.log(f"[MikroTik] HTTP {resp.status_code}")
                return False
        except requests.exceptions.ConnectionError:
            self.log(f"[MikroTik] Cannot connect to {self.host}")
            return False
        except Exception as e:
            self.log(f"[MikroTik] Connection error: {e}")
            return False

    def test_ipv6_connectivity(self, address: str) -> bool:
        """
        Test IPv6 connectivity tu router (ping Google DNS qua REST API).

        Args:
            address: IPv6 address (khong co /prefix) - dung lam src-address
        """
        try:
            # Dung MikroTik REST API /tool/ping
            data = {
                "address": "2001:4860:4860::8888",
                "src-address": address,
                "count": "2",
            }
            self.log(f"[MikroTik] Ping test: {address} → Google DNS...")
            resp = self.session.post(
                f"{self.base_url}/tool/ping",
                json=data,
                timeout=15,
            )

            if resp.status_code == 200:
                results = resp.json()
                # MikroTik tra ve list cac ping results
                if isinstance(results, list):
                    for r in results:
                        # Tim result co "time" (thanh cong) hoac "sent"/"received"
                        if r.get("time"):
                            self.log(f"[MikroTik] IPv6 OK: {address} ({r.get('time')})")
                            return True
                        # Check sent/received summary
                        received = r.get("received", 0)
                        if isinstance(received, str):
                            received = int(received) if received.isdigit() else 0
                        if received > 0:
                            self.log(f"[MikroTik] IPv6 OK: {address} (received={received})")
                            return True

                self.log(f"[MikroTik] IPv6 FAIL: {address} - no reply")
                return False
            else:
                self.log(f"[MikroTik] Ping API error ({resp.status_code}): {resp.text[:200]}")
                return False
        except Exception as e:
            self.log(f"[MikroTik] IPv6 test error: {e}")
            return False
