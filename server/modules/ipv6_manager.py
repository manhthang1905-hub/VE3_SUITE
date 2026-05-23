#!/usr/bin/env python3
"""
IPv6 Manager - Quản lý IPv6 rotation cho VM Manager
====================================================

Xử lý việc đổi IPv6 khi Chrome bị 403 nhiều lần.
Được gọi bởi VM Manager khi phát hiện 403 errors liên tục.

Logic:
    - Khi VM Manager detect 5 lần 403 liên tiếp → gọi rotate_ipv6()
    - Module này sẽ:
        1. Xóa IPv6 cũ
        2. Set IPv6 mới từ danh sách hoặc random từ subnet
        3. Log kết quả
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
import yaml
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict

TOOL_DIR = Path(__file__).parent.parent
CONFIG_FILE = TOOL_DIR / "config" / "settings.yaml"


class IPv6Manager:
    """Quản lý IPv6 rotation."""

    def __init__(self):
        self.config = self._load_config()
        self.current_ipv6_index = 0
        self.rotation_count = 0
        self.last_rotation_time: Optional[datetime] = None

    def _load_config(self) -> Dict:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        return {}

    @property
    def enabled(self) -> bool:
        return self.config.get('ipv6_rotation', {}).get('enabled', False)

    @property
    def interface_name(self) -> str:
        return self.config.get('ipv6_rotation', {}).get('interface_name', 'Ethernet')

    @property
    def subnet_prefix(self) -> str:
        return self.config.get('ipv6_rotation', {}).get('subnet_prefix', '')

    @property
    def prefix_length(self) -> int:
        return self.config.get('ipv6_rotation', {}).get('prefix_length', 56)

    @property
    def ipv6_list(self) -> List[str]:
        """Danh sách IPv6 tĩnh (nếu có)."""
        return self.config.get('ipv6_rotation', {}).get('ips', [])

    def generate_random_ipv6(self) -> Optional[str]:
        """Tạo IPv6 ngẫu nhiên từ subnet prefix."""
        if not self.subnet_prefix:
            return None

        # Ví dụ: prefix = "2001:db8:1234:5600", length = /56
        # Random phần còn lại: ::XXXX:XXXX:XXXX:XXXX
        suffix = ':'.join([f'{random.randint(0, 65535):04x}' for _ in range(4)])
        return f"{self.subnet_prefix}:{suffix}"

    def get_next_ipv6(self) -> Optional[str]:
        """
        Lấy IPv6 tiếp theo.
        Ưu tiên: danh sách tĩnh > random từ subnet
        """
        if self.ipv6_list:
            # Dùng danh sách tĩnh, xoay vòng
            self.current_ipv6_index = self.current_ipv6_index % len(self.ipv6_list)
            ipv6 = self.ipv6_list[self.current_ipv6_index]
            self.current_ipv6_index += 1
            return ipv6
        elif self.subnet_prefix:
            # Random từ subnet
            return self.generate_random_ipv6()
        return None

    def get_current_ipv6(self) -> List[str]:
        """Lấy danh sách IPv6 hiện tại trên interface."""
        try:
            result = subprocess.run(
                ['netsh', 'interface', 'ipv6', 'show', 'addresses', self.interface_name],
                capture_output=True,
                text=True
            )

            ipv6_addresses = []
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith('Address'):
                    # Format: "Address         2001:db8::1"
                    parts = line.split()
                    if len(parts) >= 2:
                        ipv6_addresses.append(parts[1])
            return ipv6_addresses
        except Exception as e:
            print(f"[IPv6] Error getting current IPv6: {e}")
            return []

    def remove_ipv6(self, ipv6_address: str) -> bool:
        """Xóa một địa chỉ IPv6 khỏi interface."""
        try:
            result = subprocess.run(
                ['netsh', 'interface', 'ipv6', 'delete', 'address',
                 self.interface_name, ipv6_address],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except Exception as e:
            print(f"[IPv6] Error removing IPv6 {ipv6_address}: {e}")
            return False

    def add_ipv6(self, ipv6_address: str) -> bool:
        """Thêm một địa chỉ IPv6 vào interface."""
        try:
            result = subprocess.run(
                ['netsh', 'interface', 'ipv6', 'add', 'address',
                 self.interface_name, ipv6_address],
                capture_output=True,
                text=True
            )
            return result.returncode == 0
        except Exception as e:
            print(f"[IPv6] Error adding IPv6 {ipv6_address}: {e}")
            return False

    def rotate_ipv6(self) -> Dict:
        """
        Thực hiện rotation IPv6.

        Returns:
            Dict với keys: success, old_ipv6, new_ipv6, message
        """
        result = {
            "success": False,
            "old_ipv6": None,
            "new_ipv6": None,
            "message": ""
        }

        if not self.enabled:
            result["message"] = "IPv6 rotation is disabled"
            return result

        # 1. Lấy IPv6 mới
        new_ipv6 = self.get_next_ipv6()
        if not new_ipv6:
            result["message"] = "No IPv6 available (no list or subnet configured)"
            return result

        # 2. Lấy IPv6 hiện tại
        current_ipv6_list = self.get_current_ipv6()

        # 3. Xóa IPv6 cũ (chỉ xóa những cái từ subnet của chúng ta)
        old_ipv6 = None
        for ipv6 in current_ipv6_list:
            if self.subnet_prefix and ipv6.startswith(self.subnet_prefix[:16]):
                old_ipv6 = ipv6
                self.remove_ipv6(ipv6)
                break

        result["old_ipv6"] = old_ipv6

        # 4. Thêm IPv6 mới
        if self.add_ipv6(new_ipv6):
            result["success"] = True
            result["new_ipv6"] = new_ipv6
            result["message"] = f"Rotated: {old_ipv6 or 'none'} → {new_ipv6}"

            self.rotation_count += 1
            self.last_rotation_time = datetime.now()
        else:
            result["message"] = f"Failed to add new IPv6: {new_ipv6}"

        return result

    def get_status(self) -> Dict:
        """Lấy trạng thái IPv6 rotation."""
        return {
            "enabled": self.enabled,
            "interface": self.interface_name,
            "current_ipv6": self.get_current_ipv6(),
            "available_count": len(self.ipv6_list) if self.ipv6_list else "unlimited (subnet)",
            "rotation_count": self.rotation_count,
            "last_rotation": self.last_rotation_time.isoformat() if self.last_rotation_time else None,
        }


# Singleton instance
_ipv6_manager: Optional[IPv6Manager] = None


def get_ipv6_manager() -> IPv6Manager:
    global _ipv6_manager
    if _ipv6_manager is None:
        _ipv6_manager = IPv6Manager()
    return _ipv6_manager


if __name__ == "__main__":
    # Test
    manager = get_ipv6_manager()
    print("IPv6 Status:", manager.get_status())

    if manager.enabled:
        print("\nRotating IPv6...")
        result = manager.rotate_ipv6()
        print("Result:", result)
