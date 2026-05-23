#!/usr/bin/env python3
"""
IPv6 Rotator - Tự động đổi IPv6 khi bị 403
==========================================

Sử dụng danh sách IPv6 từ file config/ipv6_list.txt
Khi Chrome bị 403, tự động đổi sang IPv6 khác trong danh sách.

Usage:
    from modules.ipv6_rotator import IPv6Rotator, get_ipv6_rotator

    # Cách 1: Dùng singleton
    rotator = get_ipv6_rotator(settings)
    if rotator and rotator.enabled:
        new_ip = rotator.rotate()

    # Cách 2: Tạo instance riêng
    rotator = IPv6Rotator(settings)
    rotator.rotate()
"""

import sys
import os

# Fix Windows encoding issues
if sys.platform == "win32":
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass
    if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
        try:
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except:
            pass


import subprocess
import random
import re
import time
import sys
import os
from pathlib import Path
from typing import Optional, Dict, Any, List


def _is_admin() -> bool:
    """Check if running with admin privileges (Windows)."""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except:
        return False


def _run_netsh_admin(commands: List[str], log_func=print) -> bool:
    """
    Run netsh commands with admin privileges using PowerShell.

    Args:
        commands: List of netsh commands to run
        log_func: Function to log messages

    Returns:
        True if successful
    """
    try:
        # Create a batch script with all commands
        script_path = Path(__file__).parent.parent / "config" / "ipv6_change.bat"

        with open(script_path, 'w', encoding='utf-8') as f:
            f.write("@echo off\n")
            for cmd in commands:
                f.write(f"{cmd}\n")
            f.write("exit /b 0\n")

        # Run batch file with admin privileges using PowerShell
        ps_cmd = f'Start-Process -FilePath "{script_path}" -Verb RunAs -Wait -WindowStyle Hidden'

        result = subprocess.run(
            ['powershell', '-Command', ps_cmd],
            capture_output=True,
            text=True,
            timeout=30
        )

        # Clean up
        try:
            script_path.unlink()
        except:
            pass

        return True

    except subprocess.TimeoutExpired:
        log_func("[IPv6] Admin command timeout")
        return False
    except Exception as e:
        log_func(f"[IPv6] Admin command error: {e}")
        return False


def _get_gateway_for_ipv6(ipv6_address: str) -> str:
    """
    Tính gateway từ IPv6 address.
    Gateway = ::1 trong cùng /64 subnet.

    Ví dụ:
        2001:ee0:b004:1f00::2 → 2001:ee0:b004:1f00::1
        2001:ee0:b004:1fee::238 → 2001:ee0:b004:1fee::1
        2001:ee0:b004:308d:98f4:ed82:428f:e4c5 → 2001:ee0:b004:308d::1
    """
    # v1.0.584: Dung ipaddress.IPv6Network cho compressed format (khong leading zeros)
    import ipaddress
    try:
        addr = ipaddress.IPv6Address(ipv6_address)
        network = ipaddress.IPv6Network(f"{addr}/64", strict=False)
        gateway = network.network_address + 1
        return str(gateway)  # compressed: "2001:ee0:b004:3075::1" (KHONG co 0ee0)
    except Exception:
        # Fallback: logic cu cho format don gian (co ::)
        parts = ipv6_address.split('::')
        if len(parts) >= 1:
            prefix = parts[0]
            if len(prefix.split(':')) <= 4:
                return f"{prefix}::1"
        return ""


def _get_onlink_prefix(gateway: str) -> str:
    """Tinh /64 prefix tu gateway address. VD: 2001:ee0:b004:3075::1 → 2001:ee0:b004:3075::/64"""
    import ipaddress
    try:
        addr = ipaddress.IPv6Address(gateway)
        network = ipaddress.IPv6Network(f"{addr}/64", strict=False)
        return str(network)
    except Exception:
        return ""


class IPv6Rotator:
    """Quản lý việc đổi IPv6 khi bị block."""

    def __init__(self, settings: Dict[str, Any] = None):
        """
        Khởi tạo IPv6 Rotator.

        Args:
            settings: Dict cấu hình từ settings.yaml (optional)
        """
        settings = settings or {}
        ipv6_cfg = settings.get('ipv6_rotation', {})

        self.enabled = ipv6_cfg.get('enabled', False)
        self.interface_name = ipv6_cfg.get('interface_name', 'Ethernet')
        self.max_403 = ipv6_cfg.get('max_403_before_rotate', 3)
        self.gateway = ipv6_cfg.get('gateway', '')
        self.disable_ipv4 = ipv6_cfg.get('disable_ipv4', False)  # False = giữ IPv4 cho RDP
        self.use_local_proxy = ipv6_cfg.get('use_local_proxy', False)  # False = Chrome chạy trực tiếp, không qua proxy
        self.local_proxy_port = ipv6_cfg.get('local_proxy_port', 1088)

        # IPv6 Pool Client (MikroTik dynamic pool)
        self._pool_client = None
        self._pool_mode = False  # True = dung pool API thay vi file
        mikrotik_cfg = settings.get('mikrotik', {})
        pool_api_url = mikrotik_cfg.get('pool_api_url', '')
        if pool_api_url:
            try:
                from modules.ipv6_pool_client import IPv6PoolClient
                self._pool_client = IPv6PoolClient(
                    api_url=pool_api_url,
                    timeout=mikrotik_cfg.get('pool_api_timeout', 5),
                    log_func=print,
                )
                # Test ket noi
                if self._pool_client.ping():
                    self._pool_mode = True
                    self.enabled = True
                    print(f"[IPv6] Pool API connected: {pool_api_url}")
                else:
                    print(f"[IPv6] Pool API not available: {pool_api_url}, fallback to file")
                    self._pool_client = None
            except Exception as e:
                print(f"[IPv6] Pool API init error: {e}, fallback to file")
                self._pool_client = None

        # Load IPv6 list from file (fallback khi khong co pool)
        self.ipv6_list: List[str] = []
        self.current_index = 0
        if not self._pool_mode:
            self._load_ipv6_list()

        # State
        self.consecutive_403 = 0
        self.current_ipv6 = None
        self.current_gateway = None  # Track gateway đang dùng để xóa đúng route
        self.last_rotated = None
        self._ipv4_disabled = False  # Track trạng thái IPv4
        self._local_proxy = None  # Local SOCKS5 proxy
        self.ipv6_gateways = {}  # Map IP -> custom gateway (loaded from file)
        self._worker_name = mikrotik_cfg.get('worker_name', 'unknown')

        # Log function (có thể override)
        self.log = print

    def _load_ipv6_list(self):
        """
        Load danh sách IPv6 từ file config/ipv6.txt (hoặc ipv6_list.txt)

        Format hỗ trợ:
        1. Chỉ IP: 2001:ee0:b004:3f01::2  (gateway tự tính = prefix::1)
        2. IP,GATEWAY: 2001:ee0:b004:3f01::2,2001:ee0:b004:3f01::1
        """
        try:
            # Tìm file ipv6.txt hoặc ipv6_list.txt (backward compatible)
            base_dir = Path(__file__).parent.parent
            ipv6_file = base_dir / "config" / "ipv6.txt"
            if not ipv6_file.exists():
                ipv6_file = base_dir / "config" / "ipv6_list.txt"

            if ipv6_file.exists():
                with open(ipv6_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()

                self.ipv6_list = []
                self.ipv6_gateways = {}  # Map IP -> custom gateway

                for line in lines:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    # Check format: IP,GATEWAY or just IP
                    if ',' in line:
                        parts = line.split(',')
                        ip = parts[0].strip()
                        gateway = parts[1].strip() if len(parts) > 1 else None
                        self.ipv6_list.append(ip)
                        if gateway:
                            self.ipv6_gateways[ip] = gateway
                    else:
                        self.ipv6_list.append(line)

                if self.ipv6_list:
                    self.enabled = True  # Auto-enable nếu có danh sách
                    print(f"[IPv6] Loaded {len(self.ipv6_list)} IPv6 addresses from {ipv6_file.name}")
                    if self.ipv6_gateways:
                        print(f"[IPv6] Custom gateways: {len(self.ipv6_gateways)} entries")
                else:
                    print(f"[IPv6] No IPv6 addresses in {ipv6_file.name}")
            else:
                print(f"[IPv6] File not found: {ipv6_file}")

        except Exception as e:
            print(f"[IPv6] Error loading IPv6 list: {e}")

    def _remove_dead_ipv6(self, dead_ip: str):
        """
        Xóa IPv6 chết khỏi danh sách memory (KHÔNG sửa file config).

        Args:
            dead_ip: IPv6 không hoạt động cần xóa
        """
        try:
            # Chỉ xóa khỏi memory, KHÔNG sửa file ipv6_list.txt
            if dead_ip in self.ipv6_list:
                self.ipv6_list.remove(dead_ip)
                self.log(f"[IPv6] [DEL] Removed dead IP from memory: {dead_ip}")
                self.log(f"[IPv6] Remaining this session: {len(self.ipv6_list)} IPs")

            # Điều chỉnh current_index nếu cần
            if self.current_index >= len(self.ipv6_list):
                self.current_index = 0

        except Exception as e:
            self.log(f"[IPv6] Error removing dead IP: {e}")

    def set_logger(self, log_func):
        """Set custom log function."""
        self.log = log_func
        if self._pool_client:
            self._pool_client.log = log_func

    def increment_403(self) -> bool:
        """
        Tăng counter 403 và kiểm tra có cần rotate không.

        Returns:
            True nếu đã đạt max và cần rotate
        """
        self.consecutive_403 += 1
        self.log(f"[IPv6] 403 count: {self.consecutive_403}/{self.max_403}")

        if self.consecutive_403 >= self.max_403:
            return True
        return False

    def reset_403(self):
        """Reset counter 403 khi thành công."""
        if self.consecutive_403 > 0:
            self.log(f"[IPv6] Reset 403 counter (was {self.consecutive_403})")
        self.consecutive_403 = 0

    def init_with_working_ipv6(self) -> Optional[str]:
        """
        Khởi tạo với một IPv6 hoạt động.
        Thử TẤT CẢ IP trong danh sách cho đến khi tìm được IP có connectivity.
        IP không hoạt động sẽ bị XÓA khỏi danh sách và file.

        Returns:
            IPv6 hoạt động hoặc None
        """
        # Pool mode: lay IP tu pool API
        if self._pool_mode and self._pool_client:
            return self._init_via_pool()

        if not self.ipv6_list:
            self.log("[IPv6] No IPv6 list!")
            return None

        total = len(self.ipv6_list)
        self.log(f"[IPv6] Finding working IPv6 (total: {total} IPs)...")

        # Copy list để iterate vì sẽ xóa phần tử trong quá trình loop
        ipv6_list_copy = self.ipv6_list.copy()
        dead_ips = []  # Thu thập IP chết để xóa sau

        for i, ipv6 in enumerate(ipv6_list_copy):
            self.log(f"[IPv6] Trying {i+1}/{total}: {ipv6}")

            if self.set_ipv6(ipv6):
                # Test connectivity
                if self.test_ipv6_connectivity():
                    self.log(f"[IPv6] [v] Found working IP: {ipv6}")
                    # Xóa tất cả dead IPs đã thu thập
                    for dead_ip in dead_ips:
                        self._remove_dead_ipv6(dead_ip)
                    # Cập nhật current_index sau khi xóa
                    try:
                        self.current_index = self.ipv6_list.index(ipv6)
                    except ValueError:
                        self.current_index = 0
                    return ipv6
                else:
                    self.log(f"[IPv6] [x] No connectivity: {ipv6} → REMOVING")
                    dead_ips.append(ipv6)
            else:
                self.log(f"[IPv6] [x] Failed to set: {ipv6} → REMOVING")
                dead_ips.append(ipv6)

        # Xóa tất cả dead IPs nếu không tìm thấy IP nào hoạt động
        for dead_ip in dead_ips:
            self._remove_dead_ipv6(dead_ip)

        self.log("[IPv6] [x] No working IPv6 found in entire list!")
        return None

    def _init_via_pool(self, max_retries: int = 5) -> Optional[str]:
        """Lay IPv6 tu Pool API va set len may."""
        self.log("[IPv6] Pool: finding working IP...")

        # v1.0.605: Thu register IP hien tai truoc (tranh lay IP moi khi da co)
        current_ip = self.get_current_ipv6()
        if current_ip and hasattr(self._pool_client, 'register_ip'):
            self.log(f"[IPv6] Pool: register IP hien tai: {current_ip}")
            reg_result = self._pool_client.register_ip(current_ip, worker=self._worker_name)
            if reg_result:
                pool_ip = reg_result["ip"] if isinstance(reg_result, dict) else reg_result
                pool_gw = reg_result.get("gateway", "") if isinstance(reg_result, dict) else ""
                # Set IP tu pool (co the khac worker IP nhung cung subnet)
                if self.set_ipv6(pool_ip, gateway_override=pool_gw):
                    if self.test_ipv6_connectivity():
                        self.log(f"[IPv6] [v] Pool registered OK: {pool_ip}")
                        if self.use_local_proxy:
                            self._start_local_proxy(pool_ip)
                        return pool_ip
                self.log(f"[IPv6] Pool: register IP khong connectivity, lay IP moi...")

        # v1.0.621: Release IP cu truoc khi lay IP moi (tranh chiem slot pool)
        if current_ip and hasattr(self._pool_client, 'release_ip'):
            self.log(f"[IPv6] Pool: release IP cu: {current_ip}")
            self._pool_client.release_ip(current_ip, worker=self._worker_name)

        for attempt in range(max_retries):
            result = self._pool_client.get_ip(worker=self._worker_name)
            if not result:
                self.log("[IPv6] Pool: no IPs available!")
                break

            # v1.0.578: Pool API tra ve dict {"ip": "...", "gateway": "..."}
            ip = result["ip"] if isinstance(result, dict) else result
            pool_gw = result.get("gateway", "") if isinstance(result, dict) else ""

            self.log(f"[IPv6] Pool trying {attempt + 1}/{max_retries}: {ip} gw={pool_gw}")
            if self.set_ipv6(ip, gateway_override=pool_gw):
                if self.test_ipv6_connectivity():
                    self.log(f"[IPv6] [v] Pool found working IP: {ip}")
                    if self.use_local_proxy:
                        self._start_local_proxy(ip)
                    return ip
                else:
                    self.log(f"[IPv6] [x] Pool IP no connectivity: {ip} → burn")
                    self._pool_client.burn_ip(ip, reason="no_connectivity", worker=self._worker_name)
            else:
                self.log(f"[IPv6] [x] Pool IP set failed: {ip} → burn")
                self._pool_client.burn_ip(ip, reason="set_failed", worker=self._worker_name)

        self.log("[IPv6] [x] Pool: no working IP found!")
        return None

    def get_current_ipv6(self) -> Optional[str]:
        """
        Lấy IPv6 hiện tại của interface (Windows).

        Returns:
            IPv6 address hoặc None
        """
        try:
            # netsh interface ipv6 show addresses "Ethernet"
            cmd = f'netsh interface ipv6 show addresses "{self.interface_name}"'
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=20
            )

            if result.returncode == 0:
                # Parse output để tìm global IPv6 (không phải fe80:: link-local)
                lines = result.stdout.split('\n')
                for line in lines:
                    # Tìm dòng chứa "Address" và IPv6
                    if 'Address' in line:
                        # Match IPv6 pattern
                        match = re.search(r'(2[0-9a-fA-F]{3}:[0-9a-fA-F:]+)', line)
                        if match:
                            return match.group(1)
            return None
        except Exception as e:
            self.log(f"[IPv6] Error getting current IP: {e}")
            return None

    def get_next_ipv6(self) -> Optional[str]:
        """
        Lấy IPv6 tiếp theo trong danh sách.

        Returns:
            IPv6 address hoặc None nếu hết danh sách
        """
        if not self.ipv6_list:
            return None

        # Lấy IPv6 hiện tại để tránh trùng
        current = self.get_current_ipv6()

        # Thử tìm IPv6 khác trong danh sách
        for _ in range(len(self.ipv6_list)):
            self.current_index = (self.current_index + 1) % len(self.ipv6_list)
            next_ip = self.ipv6_list[self.current_index]

            # Kiểm tra không trùng với IP hiện tại
            if current and next_ip.lower() == current.lower():
                continue

            return next_ip

        # Nếu tất cả đều trùng (không nên xảy ra), random 1 cái
        return random.choice(self.ipv6_list)

    def test_ipv6_connectivity(self, ipv6: str = None, timeout: int = 5) -> bool:
        """
        Test xem IPv6 có kết nối được internet không.
        v1.0.375: Dùng curl HTTP thay vì ping (nhiều mạng chặn ICMP).

        Args:
            ipv6: IPv6 để test (None = test IP hiện tại)
            timeout: Timeout (giây)

        Returns:
            True nếu kết nối thành công
        """
        try:
            # Thử curl -6 trước (chính xác hơn ping)
            cmd = f'curl -6 --connect-timeout {timeout} -s -o nul -w "%{{http_code}}" https://www.google.com'
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout + 3
            )
            if result.returncode == 0 and result.stdout.strip().startswith(('2', '3')):
                return True
        except:
            pass

        try:
            # Fallback: ping Google DNS IPv6
            cmd = f'ping -n 1 -w {timeout * 1000} 2001:4860:4860::8888'
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout + 2
            )
            return result.returncode == 0 and 'Reply from' in result.stdout
        except:
            return False

    def set_ipv6(self, new_ipv6: str, gateway_override: str = "") -> bool:
        """
        Đặt IPv6 mới cho interface (Windows).

        Steps:
        1. Tắt IPv4 để ép dùng IPv6 (nếu bật disable_ipv4)
        2. Xóa tất cả IPv6 cũ (trong danh sách) khỏi interface
        3. Thêm IPv6 mới
        4. Đợi network adapter cập nhật

        Tự động yêu cầu quyền Admin nếu cần.

        Args:
            new_ipv6: IPv6 address mới

        Returns:
            True nếu thành công
        """
        try:
            # === SKIP NẾU ĐÃ DÙNG IPv6 NÀY ===
            # Tránh thao tác thừa khi restart Chrome
            if self.current_ipv6 and self.current_ipv6.lower() == new_ipv6.lower():
                self.log(f"[IPv6] [v] Already using: {new_ipv6}")
                return True

            self.log(f"[IPv6] [SYNC] Changing to: {new_ipv6}")

            # Collect all netsh commands
            commands = []

            # v1.0.586: Dam bao Windows Firewall cho phep ICMPv6 (NDP)
            # Neu khong co rule nay → MikroTik khong tim duoc VM → gateway UNREACHABLE
            if not getattr(self, '_icmpv6_rule_added', False):
                commands.append('netsh advfirewall firewall add rule name="ICMPv6-NDP-In" dir=in action=allow protocol=icmpv6')
                commands.append('netsh advfirewall firewall add rule name="ICMPv6-NDP-Out" dir=out action=allow protocol=icmpv6')
                self._icmpv6_rule_added = True

            # Bước 0: Tắt IPv4 để Chrome phải dùng IPv6 (nếu bật)
            if self.disable_ipv4 and not self._ipv4_disabled:
                self.log("[IPv6] [PLUG] Disabling IPv4 to force IPv6...")
                commands.append(f'netsh interface ipv4 set interface "{self.interface_name}" admin=disabled')

            # Bước 1: XÓA IP HIỆN TẠI đang dùng (quan trọng!)
            current_ip = self.get_current_ipv6()
            if current_ip and current_ip.lower() != new_ipv6.lower():
                self.log(f"[IPv6] Removing current IP: {current_ip}")
                commands.append(f'netsh interface ipv6 delete address "{self.interface_name}" {current_ip}')

            # Bước 2: Xóa tất cả IPv6 cũ trong danh sách khỏi interface
            for old_ip in self.ipv6_list:
                if old_ip.lower() != new_ipv6.lower() and old_ip.lower() != (current_ip or '').lower():
                    commands.append(f'netsh interface ipv6 delete address "{self.interface_name}" {old_ip}')

            # Bước 3: Thêm IPv6 mới
            commands.append(f'netsh interface ipv6 add address "{self.interface_name}" {new_ipv6}')

            # Bước 3: Set gateway
            # v1.0.578: Uu tien: 1) pool API gateway, 2) per-IP gateway, 3) config gateway, 4) auto-compute
            if gateway_override:
                # Gateway tu Pool API - rieng cho moi subnet
                new_gateway = gateway_override
                self.log(f"[IPv6] Using pool gateway: {new_gateway}")
            elif new_ipv6 in self.ipv6_gateways:
                new_gateway = self.ipv6_gateways[new_ipv6]
                self.log(f"[IPv6] Using custom gateway: {new_gateway}")
            elif self.gateway:
                # Gateway co dinh tu settings.yaml
                new_gateway = self.gateway
                self.log(f"[IPv6] Using config gateway: {new_gateway}")
            else:
                new_gateway = _get_gateway_for_ipv6(new_ipv6)
                self.log(f"[IPv6] Auto gateway: {new_gateway}")

            if new_gateway:
                # v1.0.643: Check neu IPv6Provider da quan ly route ::/0
                # Neu da co default route (server mode, nhieu worker) → KHONG xoa/them ::/0
                # Chi xoa/them on-link route cho subnet moi
                _provider_has_route = False
                try:
                    from modules.proxy_providers.ipv6_provider import IPv6Provider
                    _provider_has_route = bool(IPv6Provider._default_route_gateway)
                except ImportError:
                    pass

                if _provider_has_route:
                    # v1.0.643: Server mode - chi xoa on-link route cu, them on-link moi
                    # KHONG dong den route ::/0 (IPv6Provider quan ly)
                    self.log(f"[IPv6] [v1.0.643] Skip ::/0 route (IPv6Provider quan ly)")
                    if self.current_gateway:
                        old_onlink = _get_onlink_prefix(self.current_gateway)
                        if old_onlink:
                            commands.append(f'netsh interface ipv6 delete route {old_onlink} "{self.interface_name}"')
                else:
                    # VM mode (1 worker) hoac khong co IPv6Provider → xu ly binh thuong
                    if self.current_gateway:
                        self.log(f"[IPv6] Xoa route cu: gateway {self.current_gateway}")
                        commands.append(f'netsh interface ipv6 delete route ::/0 "{self.interface_name}" {self.current_gateway}')
                        old_onlink = _get_onlink_prefix(self.current_gateway)
                        if old_onlink:
                            commands.append(f'netsh interface ipv6 delete route {old_onlink} "{self.interface_name}"')
                    else:
                        commands.append(f'netsh interface ipv6 delete route ::/0 "{self.interface_name}"')

                # v1.0.585: QUAN TRONG - Them on-link route cho /64 subnet
                # Neu khong co route nay, Windows khong biet gateway nam cung mang
                # → gateway UNREACHABLE → IPv6 khong hoat dong!
                import ipaddress as _ipaddr
                try:
                    _gw_addr = _ipaddr.IPv6Address(new_gateway)
                    _gw_net = _ipaddr.IPv6Network(f"{_gw_addr}/64", strict=False)
                    _onlink_prefix = str(_gw_net)  # "2001:ee0:b004:3075::/64"
                    commands.append(f'netsh interface ipv6 add route {_onlink_prefix} "{self.interface_name}"')
                    self.log(f"[IPv6] Them on-link route: {_onlink_prefix}")
                except Exception:
                    pass

                if not _provider_has_route:
                    commands.append(f'netsh interface ipv6 add route ::/0 "{self.interface_name}" {new_gateway}')

            # Bước 4: Set Windows prefer IPv6 over IPv4 (quan trọng!)
            # Đây là cách ép Windows dùng IPv6 cho outgoing connections
            commands.append('netsh interface ipv6 set prefixpolicy ::1/128 50 0')
            commands.append('netsh interface ipv6 set prefixpolicy ::/0 40 1')
            commands.append('netsh interface ipv6 set prefixpolicy 2002::/16 30 2')
            commands.append('netsh interface ipv6 set prefixpolicy ::ffff:0:0/96 10 4')

            # Check admin và chạy commands
            if _is_admin():
                # Đã có quyền admin - chạy trực tiếp
                self.log("[IPv6] Running with admin privileges...")

                # v1.0.604: CLEANUP - xoa TAT CA IPv6 addresses co prefix pool tren interface
                # Tranh IP cu tu session truoc van nam tren interface → routing loan
                try:
                    addr_check = subprocess.run(
                        f'netsh interface ipv6 show address "{self.interface_name}"',
                        shell=True, capture_output=True, text=True, timeout=15
                    )
                    if addr_check.returncode == 0:
                        # Tim prefix cua pool (vd: "2001:ee0:b004:30")
                        pool_prefix = ""
                        if hasattr(self, '_pool_client') and self._pool_client:
                            # Lay prefix tu config
                            _mk_cfg = self._settings.get('mikrotik', {}) if hasattr(self, '_settings') else {}
                            pool_prefix = _mk_cfg.get('prefix', '')
                        if not pool_prefix and new_ipv6:
                            # Auto-detect prefix tu IP moi (lay 4 octets dau)
                            _parts = new_ipv6.split(":")
                            if len(_parts) >= 4:
                                pool_prefix = ":".join(_parts[:3]) + ":"  # "2001:ee0:b004:"

                        if pool_prefix:
                            pool_prefix_clean = pool_prefix.rstrip(":")
                            for line in (addr_check.stdout or "").split('\n'):
                                line = line.strip()
                                # Tim dong co "Address" va prefix pool
                                if pool_prefix_clean in line and 'Address' in line:
                                    parts = line.split()
                                    for p in parts:
                                        if pool_prefix_clean in p and p != new_ipv6:
                                            # Xoa IP cu nay
                                            del_cmd = f'netsh interface ipv6 delete address "{self.interface_name}" {p}'
                                            try:
                                                subprocess.run(del_cmd, shell=True, capture_output=True, text=True, timeout=15)
                                                self.log(f"[IPv6] [CLEANUP] Xoa IP cu: {p}")
                                            except subprocess.TimeoutExpired:
                                                self.log(f"[IPv6] [WARN] cleanup IP timeout: {p}")
                except Exception as addr_cleanup_err:
                    self.log(f"[IPv6] [WARN] Address cleanup error: {addr_cleanup_err}")

                # v1.0.643: CLEANUP - xoa routes ::/0 cu, giu _default_route_gateway + active gateways
                try:
                    # Collect gateway can giu
                    keep_set = set()
                    if new_gateway:
                        keep_set.add(new_gateway)
                    try:
                        from modules.proxy_providers.ipv6_provider import IPv6Provider
                        keep_set.update(IPv6Provider._active_gateways.values())
                        # v1.0.643: Luon giu default route gateway
                        if IPv6Provider._default_route_gateway:
                            keep_set.add(IPv6Provider._default_route_gateway)
                    except ImportError:
                        pass

                    route_check = subprocess.run(
                        'netsh interface ipv6 show route',
                        shell=True, capture_output=True, text=True, timeout=15
                    )
                    if route_check.returncode == 0:
                        route_lines = (route_check.stdout or "").split('\n')
                        for rl in route_lines:
                            if '::/0' in rl and self.interface_name.lower() in rl.lower():
                                parts = rl.strip().split()
                                for p in parts:
                                    if ':' in p and '::' in p and p != '::/0':
                                        if p in keep_set:
                                            continue  # v1.0.643: Giu route cua worker khac + default route
                                        del_cmd = f'netsh interface ipv6 delete route ::/0 "{self.interface_name}" {p}'
                                        try:
                                            subprocess.run(del_cmd, shell=True, capture_output=True, text=True, timeout=15)
                                            self.log(f"[IPv6] [CLEANUP] Xoa route cu: ::/0 via {p}")
                                        except subprocess.TimeoutExpired:
                                            self.log(f"[IPv6] [WARN] cleanup timeout: {del_cmd}")
                except Exception as cleanup_err:
                    self.log(f"[IPv6] [WARN] Route cleanup error: {cleanup_err}")

                # v1.0.598: Tang timeout 5s → 20s de netsh khong bi timeout
                for cmd in commands:
                    try:
                        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20)
                        # v1.0.581: Log errors de debug connectivity
                        if r.returncode != 0:
                            stderr = (r.stderr or "").strip()
                            # delete address/route fail khi chua co → OK, bo qua
                            if "delete" in cmd and ("not found" in stderr.lower() or "object" in stderr.lower()):
                                pass
                            else:
                                self.log(f"[IPv6] [WARN] cmd failed (rc={r.returncode}): {cmd}")
                                if stderr:
                                    self.log(f"[IPv6] [WARN] stderr: {stderr[:200]}")
                    except subprocess.TimeoutExpired:
                        self.log(f"[IPv6] [WARN] cmd timeout: {cmd}")
                        # v1.0.598: Retry 1 lan voi timeout dai hon
                        try:
                            r2 = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                            if r2.returncode == 0:
                                self.log(f"[IPv6] [v] cmd OK sau retry")
                        except Exception:
                            self.log(f"[IPv6] [!] cmd FAIL ca sau retry: {cmd}")

                # v1.0.584: Verify route + addresses (gateway ping moved to AFTER adapter wait)
                try:
                    # Show all IPv6 routes (netsh show route khong ho tro filter)
                    verify = subprocess.run(
                        'netsh interface ipv6 show route',
                        shell=True, capture_output=True, text=True, timeout=15
                    )
                    route_out = (verify.stdout or "").strip()
                    # Tim default route ::/0
                    default_lines = [l for l in route_out.split('\n') if '::/0' in l]
                    if default_lines:
                        # v1.0.598: Canh bao neu co nhieu hon 1 default route
                        if len(default_lines) > 1:
                            self.log(f"[IPv6] [!] CO {len(default_lines)} default routes (nen chi co 1)!")
                        self.log(f"[IPv6] [v] Default route found:")
                        for dl in default_lines[:5]:
                            self.log(f"[IPv6]     {dl.strip()}")
                    else:
                        self.log(f"[IPv6] [!] DEFAULT ROUTE ::/0 MISSING in route table!")
                        route_lines = [l.strip() for l in route_out.split('\n') if l.strip()]
                        for rl in route_lines[-5:]:
                            self.log(f"[IPv6] [!] route: {rl[:200]}")
                except Exception as e:
                    self.log(f"[IPv6] [WARN] Route verify error: {e}")

                # Bước 5: v1.0.375 - Set DNS IPv6
                # v1.0.679: Dung gateway lam DNS primary (MikroTik allow-remote-requests=true)
                # VNPT drop/throttle UDP IPv6 toi Google DNS → timeout 60s
                # Gateway relay qua ISP DNS → instant. Google DNS lam fallback.
                dns_primary = new_gateway if new_gateway else '2001:4860:4860::8888'
                dns_commands = [
                    f'netsh interface ipv6 set dnsservers "{self.interface_name}" static {dns_primary} primary',
                    f'netsh interface ipv6 add dnsservers "{self.interface_name}" 2001:4860:4860::8888 index=2',
                ]
                for dns_cmd in dns_commands:
                    try:
                        subprocess.run(dns_cmd, shell=True, capture_output=True, timeout=15)
                    except Exception as dns_err:
                        self.log(f"[IPv6] [WARN] DNS command timeout (non-fatal): {dns_err}")
            else:
                # Cần yêu cầu quyền admin
                self.log("[IPv6] Requesting admin privileges...")
                if not _run_netsh_admin(commands, self.log):
                    self.log("[IPv6] [x] Failed to get admin privileges")
                    return False
                # DNS riêng (non-fatal)
                # v1.0.679: Gateway lam DNS primary
                dns_primary = new_gateway if new_gateway else '2001:4860:4860::8888'
                dns_commands = [
                    f'netsh interface ipv6 set dnsservers "{self.interface_name}" static {dns_primary} primary',
                    f'netsh interface ipv6 add dnsservers "{self.interface_name}" 2001:4860:4860::8888 index=2',
                ]
                _run_netsh_admin(dns_commands, self.log)  # Best-effort

            # Đợi adapter cập nhật + NDP discovery (quan trọng!)
            # v1.0.584: Tang 5s → 8s de NDP kip hoan thanh truoc khi test connectivity
            self.log("[IPv6] Doi adapter cap nhat + NDP discovery (8s)...")
            time.sleep(8)

            # Track IPv4 disabled status
            if self.disable_ipv4:
                self._ipv4_disabled = True

            # v1.0.584: Gateway ping SAU khi doi adapter (truoc day chay TRUOC → luon fail)
            if new_gateway and _is_admin():
                try:
                    # Ping gateway voi timeout 5s (du cho NDP da hoan thanh)
                    ping_gw = subprocess.run(
                        f'ping -6 -n 1 -w 5000 {new_gateway}',
                        shell=True, capture_output=True, text=True, timeout=8
                    )
                    if ping_gw.returncode == 0 and 'Reply from' in (ping_gw.stdout or ''):
                        self.log(f"[IPv6] [v] Gateway reachable: {new_gateway}")
                    else:
                        self.log(f"[IPv6] [!] Gateway UNREACHABLE: {new_gateway}")
                        ping_out = (ping_gw.stdout or '').strip()
                        if ping_out:
                            self.log(f"[IPv6] [!] {ping_out[:200]}")
                except Exception:
                    pass

                # Show addresses (debug)
                try:
                    addr_verify = subprocess.run(
                        f'netsh interface ipv6 show addresses "{self.interface_name}"',
                        shell=True, capture_output=True, text=True, timeout=15
                    )
                    addr_out = (addr_verify.stdout or "").strip()
                    addr_lines = [l.strip() for l in addr_out.split('\n') if l.strip() and ('Address' in l or '2001:' in l or 'DAD' in l)]
                    if addr_lines:
                        self.log(f"[IPv6] Addresses on {self.interface_name}:")
                        for al in addr_lines[:6]:
                            self.log(f"[IPv6]     {al[:150]}")
                except Exception:
                    pass

            # Verify
            current = self.get_current_ipv6()
            if current:
                self.log(f"[IPv6] [v] Dang dung: {current}")
                self.log(f"[IPv6] [v] Gateway: {new_gateway}")
                if self.disable_ipv4:
                    self.log("[IPv6] [v] IPv4 disabled - Chrome se dung IPv6")
                self.current_ipv6 = current
                self.current_gateway = new_gateway  # Track gateway de lan sau xoa dung
                return True
            else:
                self.log("[IPv6] [x] Khong verify duoc IP moi")
                return False

        except Exception as e:
            self.log(f"[IPv6] Error setting IP: {e}")
            return False

    def enable_ipv4(self) -> bool:
        """Bật lại IPv4 (khi không cần ép IPv6 nữa)."""
        if not self._ipv4_disabled:
            return True

        try:
            self.log("[IPv6] [PLUG] Re-enabling IPv4...")
            cmd = f'netsh interface ipv4 set interface "{self.interface_name}" admin=enabled'

            if _is_admin():
                subprocess.run(cmd, shell=True, capture_output=True, timeout=5)
            else:
                _run_netsh_admin([cmd], self.log)

            self._ipv4_disabled = False
            self.log("[IPv6] [v] IPv4 re-enabled")
            return True
        except Exception as e:
            self.log(f"[IPv6] Error enabling IPv4: {e}")
            return False

    def rotate(self, max_retries: int = 5) -> Optional[str]:
        """
        Thực hiện rotate IPv6.

        1. Lấy IPv6 tiếp theo từ danh sách
        2. Set IPv6 mới
        3. Test connectivity - nếu fail thì XÓA IP chết và thử IP tiếp theo
        4. Start/update local proxy (nếu bật)
        5. Reset 403 counter

        Args:
            max_retries: Số lần thử tối đa nếu IP không hoạt động

        Returns:
            IPv6 mới nếu thành công, None nếu thất bại
        """
        if not self.enabled:
            self.log("[IPv6] Rotation is disabled")
            return None

        # =====================================================
        # MODE 1: Pool API (MikroTik dynamic pool)
        # =====================================================
        if self._pool_mode and self._pool_client:
            return self._rotate_via_pool(max_retries)

        # =====================================================
        # MODE 2: Static file list (ipv6.txt) - cach cu
        # =====================================================
        if not self.ipv6_list:
            self.log("[IPv6] No IPv6 list available!")
            return None

        tried_ips = set()
        dead_ips = []  # Thu thập IP chết để xóa
        current = self.get_current_ipv6()

        # Thử tất cả IP trong danh sách
        max_attempts = len(self.ipv6_list) + max_retries

        for attempt in range(max_attempts):
            try:
                new_ipv6 = self.get_next_ipv6()

                if not new_ipv6 or new_ipv6 in tried_ips:
                    self.log("[IPv6] No more untried IPs available")
                    break

                tried_ips.add(new_ipv6)
                self.log(f"[IPv6] Rotating: {current} → {new_ipv6} (attempt {attempt + 1})")

                if self.set_ipv6(new_ipv6):
                    # Test connectivity
                    if self.test_ipv6_connectivity():
                        self.log(f"[IPv6] [v] Connectivity OK: {new_ipv6}")

                        # Xóa tất cả dead IPs đã thu thập
                        for dead_ip in dead_ips:
                            self._remove_dead_ipv6(dead_ip)

                        # Start/update local proxy nếu bật
                        if self.use_local_proxy:
                            self._start_local_proxy(new_ipv6)

                        self.reset_403()
                        self.last_rotated = time.time()
                        return new_ipv6
                    else:
                        self.log(f"[IPv6] [x] No connectivity: {new_ipv6} → REMOVING")
                        dead_ips.append(new_ipv6)
                        continue
                else:
                    self.log(f"[IPv6] [x] Failed to set: {new_ipv6} → REMOVING")
                    dead_ips.append(new_ipv6)
                    continue

            except Exception as e:
                self.log(f"[IPv6] Rotation error: {e}")
                continue

        # Xóa tất cả dead IPs nếu không tìm thấy IP nào hoạt động
        for dead_ip in dead_ips:
            self._remove_dead_ipv6(dead_ip)

        self.log("[IPv6] [x] All rotation attempts failed")
        return None

    def _rotate_via_pool(self, max_retries: int = 5) -> Optional[str]:
        """
        Rotate IPv6 qua Pool API.
        Lay IP tu pool → set vao may → test → OK thi dung, fail thi burn va thu IP khac.
        """
        current = self.get_current_ipv6() or self.current_ipv6

        for attempt in range(max_retries):
            try:
                # Lay IP moi tu pool
                if current and attempt == 0:
                    # Lan dau: rotate (burn cu + lay moi)
                    result = self._pool_client.rotate_ip(
                        current, reason="403", worker=self._worker_name
                    )
                else:
                    # Lan sau: chi lay moi (IP cu da burn roi)
                    result = self._pool_client.get_ip(worker=self._worker_name)

                if not result:
                    self.log("[IPv6] Pool: No more IPs available!")
                    break

                # v1.0.578: Pool API tra ve dict {"ip": "...", "gateway": "..."}
                new_ipv6 = result["ip"] if isinstance(result, dict) else result
                pool_gw = result.get("gateway", "") if isinstance(result, dict) else ""

                self.log(f"[IPv6] Pool rotate: {current} → {new_ipv6} gw={pool_gw} (attempt {attempt + 1})")

                # Set IPv6 len may
                if self.set_ipv6(new_ipv6, gateway_override=pool_gw):
                    if self.test_ipv6_connectivity():
                        self.log(f"[IPv6] [v] Pool OK: {new_ipv6}")

                        if self.use_local_proxy:
                            self._start_local_proxy(new_ipv6)

                        self.reset_403()
                        self.last_rotated = time.time()
                        return new_ipv6
                    else:
                        # IP khong ket noi duoc → burn va thu cai khac
                        self.log(f"[IPv6] [x] Pool IP no connectivity: {new_ipv6} → burn")
                        self._pool_client.burn_ip(new_ipv6, reason="no_connectivity", worker=self._worker_name)
                        current = None  # Da burn, lan sau chi get_ip
                        continue
                else:
                    self.log(f"[IPv6] [x] Pool IP set failed: {new_ipv6} → burn")
                    self._pool_client.burn_ip(new_ipv6, reason="set_failed", worker=self._worker_name)
                    current = None
                    continue

            except Exception as e:
                self.log(f"[IPv6] Pool rotation error: {e}")
                continue

        self.log("[IPv6] [x] Pool rotation failed - all attempts exhausted")
        return None

    def _start_local_proxy(self, ipv6_address: str):
        """Start or update local SOCKS5 proxy với IPv6 mới."""
        try:
            from modules.ipv6_proxy import start_ipv6_proxy, get_ipv6_proxy

            # LUÔN set IPv6 trên interface trước khi start proxy
            # (cần có IP trên interface thì proxy mới bind được)
            self.log(f"[IPv6] Ensuring interface has: {ipv6_address}")
            if not self.set_ipv6(ipv6_address):
                self.log("[IPv6] [WARN] Failed to set IPv6, proxy may not work correctly", "WARN")

            if self._local_proxy is None:
                # Start proxy lần đầu
                self._local_proxy = start_ipv6_proxy(
                    ipv6_address=ipv6_address,
                    port=self.local_proxy_port,
                    log_func=self.log
                )
            else:
                # Update IPv6 cho proxy đang chạy
                self._local_proxy.set_ipv6(ipv6_address)

        except Exception as e:
            self.log(f"[IPv6] Local proxy error: {e}")

    def get_proxy_url(self) -> Optional[str]:
        """Get proxy URL cho Chrome (socks5://localhost:port)."""
        if self.use_local_proxy and self._local_proxy and self._local_proxy._running:
            return f"socks5://127.0.0.1:{self.local_proxy_port}"
        return None

    def should_rotate(self) -> bool:
        """
        Kiểm tra có nên rotate không.

        Returns:
            True nếu cần rotate (403 >= max)
        """
        return self.enabled and self.consecutive_403 >= self.max_403


# Singleton instance
_rotator_instance: Optional[IPv6Rotator] = None


def get_ipv6_rotator(settings: Dict[str, Any] = None) -> Optional[IPv6Rotator]:
    """
    Lấy IPv6Rotator instance (singleton).

    Args:
        settings: Dict cấu hình (chỉ cần lần đầu).
                  v1.0.575: Nếu None, tự đọc settings.yaml

    Returns:
        IPv6Rotator instance hoặc None nếu disabled
    """
    global _rotator_instance

    if _rotator_instance is None:
        # v1.0.575: Tu dong doc settings.yaml neu khong truyen settings
        if not settings:
            try:
                import yaml
                settings_path = Path(__file__).parent.parent / "config" / "settings.yaml"
                if settings_path.exists():
                    with open(settings_path, 'r', encoding='utf-8') as f:
                        settings = yaml.safe_load(f) or {}
            except Exception:
                settings = {}
        _rotator_instance = IPv6Rotator(settings)

    return _rotator_instance


def init_ipv6_rotator(settings: Dict[str, Any] = None) -> IPv6Rotator:
    """
    Khởi tạo IPv6Rotator singleton.

    Args:
        settings: Dict cấu hình

    Returns:
        IPv6Rotator instance
    """
    global _rotator_instance
    _rotator_instance = IPv6Rotator(settings)
    return _rotator_instance
