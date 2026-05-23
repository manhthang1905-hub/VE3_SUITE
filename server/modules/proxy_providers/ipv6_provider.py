#!/usr/bin/env python3
"""
IPv6Provider - Proxy provider dung IPv6 rotation.
==================================================

Wrap modules/ipv6_rotator.py + modules/ipv6_proxy.py thanh ProxyProvider interface.
Logic giu nguyen y het, chi thay doi cach goi.

VM Mode: Chrome 1 (worker_id=0) quan ly proxy, Chrome 2+ reuse.
Server Mode: Moi worker co proxy rieng.

v1.0.643: Single-route architecture
- CHI 1 route ::/0 duy nhat (worker dau tien them, worker sau KHONG them)
- Moi worker chi add IP address + on-link route /64
- SOCKS5 proxy bind() source IP rieng → Google thay IP khac nhau
- MikroTik forward traffic tu bat ky source subnet (cung bridge)
- NDP keepalive thread ping gateway moi 20s → giu NDP cache alive
- Scale duoc 10+ Chrome instances on dinh

v1.0.677-678: Fix route ::/0 khong update khi rotate
- BUG: Pool burn subnet cu → xoa gateway tren MikroTik
  → Route ::/0 van tro toi gateway cu (da xoa) → TAT CA worker timeout
- v1.0.677: Detect gateway thay doi → update route (QUA RONG - doi ca initial setup)
- v1.0.678: Chi reset route khi rotate_to() VA old_gw == default_route
  → Initial setup: worker 1 KHONG doi route cua worker 0
  → Rotation: chi doi khi gateway bi burn la default route
"""

import time
import subprocess
import ipaddress
import threading
from typing import Optional, Callable
from modules.proxy_providers.base_provider import ProxyProvider

IPV6_PROVIDER_BUILD = "2026-04-23-gwfix2"


class NDPKeepalive:
    """
    v1.0.643: Ping gateway dinh ky de giu NDP cache alive.

    Khi NDP cache expire (~30-60s), MikroTik khong tim duoc MAC cua Windows
    → IPv6 mat mang. Thread nay ping gateway moi 20s de ngua van de nay.

    Moi worker co 1 NDPKeepalive rieng (ping tu source IP cua worker).
    """

    def __init__(self, gateway: str, source_ip: str, interface: str,
                 interval: int = 20, log_func: Callable = print):
        self.gateway = gateway
        self.source_ip = source_ip
        self.interface = interface
        self.interval = interval
        self.log = log_func
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        """Start keepalive thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._keepalive_loop,
            name=f"NDP-Keepalive-{self.source_ip[:20]}",
            daemon=True
        )
        self._thread.start()
        self.log(f"[NDP-Keepalive] Started: ping {self.gateway} every {self.interval}s (src: {self.source_ip})")

    def _keepalive_loop(self):
        """Ping gateway dinh ky."""
        fail_count = 0
        while not self._stop_event.is_set():
            try:
                # ping -6 -S source_ip gateway
                # -S: bind source IP cu the → NDP cho dung IP nay
                cmd = f'ping -6 -n 1 -w 3000 -S {self.source_ip} {self.gateway}'
                result = subprocess.run(
                    cmd, shell=True, capture_output=True, text=True, timeout=8
                )
                if result.returncode == 0 and 'Reply from' in (result.stdout or ''):
                    if fail_count >= 2:
                        self.log(f"[NDP-Keepalive] Gateway {self.gateway} recovered after {fail_count} fails")
                    fail_count = 0
                else:
                    fail_count += 1
                    # v1.0.645: Chi log tu fail #2 tro len (fail #1 la binh thuong, giam spam)
                    if (fail_count >= 2 and fail_count <= 5) or fail_count % 10 == 0:
                        self.log(f"[NDP-Keepalive] [!] Gateway {self.gateway} no reply (fail #{fail_count})")
            except Exception:
                fail_count += 1
            self._stop_event.wait(self.interval)

    def stop(self):
        """Stop keepalive thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self.log(f"[NDP-Keepalive] Stopped for {self.source_ip}")


class IPv6Provider(ProxyProvider):
    """Proxy provider dung IPv6 SOCKS5 local binding."""

    # v1.0.640: Class-level tracking - tat ca gateway dang active cua cac worker
    # Key: worker_id, Value: gateway string
    _active_gateways: dict = {}

    # v1.0.643: Single-route - chi 1 route ::/0 duy nhat
    # Gateway cua route ::/0 hien tai (shared across all workers)
    _default_route_gateway: str = ""
    # Lock de tranh race condition khi nhieu worker setup cung luc
    _route_lock: threading.Lock = threading.Lock()

    def __init__(self, config: dict = None, log_func: Callable = print):
        super().__init__(config, log_func)
        self._rotator = None  # IPv6Rotator instance
        self._proxy = None    # IPv6SocksProxy instance
        self._activated = False
        self._current_ipv6 = None      # v1.0.609: IPv6 hien tai (dedicated mode)
        self._current_gateway = None   # v1.0.609: Gateway hien tai (dedicated mode)
        self._interface_name = None    # v1.0.609: Interface name tu settings
        self._ndp_keepalive = None     # v1.0.643: NDP keepalive thread
        self._build_logged = False

    @staticmethod
    def _sanitize_iface_name(name: str) -> str:
        """Normalize interface name de tranh netsh command bi quote kep."""
        s = str(name or "Ethernet").strip()
        # Xu ly ca truong hop ""Ethernet""
        s = s.replace('"', '').replace("'", "").strip()
        return s or "Ethernet"

    def setup(self, worker_id: int = 0, port: int = 1088) -> bool:
        """
        Khoi tao IPv6 cho VM mode (API).

        v1.0.617: DIRECT mode - khong SOCKS5 proxy (tranh cham).
        - IPv6 da add vao interface boi ipv6_rotator.set_ipv6()
        - Firewall block IPv4 outbound cho Chrome → bat buoc dung IPv6
        - Khong proxy → Chrome ket noi truc tiep → nhanh hon

        - worker_id=0: Tim IPv6 hoat dong + block IPv4 cho Chrome
        - worker_id>0: Reuse IPv6 da set boi worker 0
        """
        self.worker_id = worker_id
        self.port = port
        self._direct_mode = True  # VM mode = direct, khong SOCKS5

        try:
            from modules.ipv6_rotator import get_ipv6_rotator
            self._rotator = get_ipv6_rotator()

            if not self._rotator or not self._rotator.enabled:
                self.log(f"[PROXY-IPv6] IPv6 khong kha dung (enabled={getattr(self._rotator, 'enabled', False)})")
                return False

            # v1.0.574: Pool mode khong can ipv6_list (lay tu API)
            _is_pool = getattr(self._rotator, '_pool_mode', False)
            if not _is_pool and not self._rotator.ipv6_list:
                self.log(f"[PROXY-IPv6] Khong co IPv6 list va khong co pool!")
                return False

            self._rotator.set_logger(self.log)

            if worker_id == 0:
                # Chrome 1: Tim IPv6 hoat dong
                working_ipv6 = self._rotator.init_with_working_ipv6()
                if not working_ipv6:
                    self.log("[PROXY-IPv6] Khong tim duoc IPv6 hoat dong!")
                    return False

                # v1.0.620: Firewall da chuyen sang drission_flow_api.py + google_login.py
                # Khong can block o day nua (tranh duplicate + timeout)

                self._activated = True
                self._ready = True
                self.log(f"[PROXY-IPv6] [v] Worker {worker_id}: IPv6={working_ipv6} (DIRECT + IPv4 blocked)")
                return True
            else:
                # Worker khac: IPv6 + firewall da san sang (worker 0 da set)
                self._ready = True
                self.log(f"[PROXY-IPv6] [v] Worker {worker_id}: Reuse IPv6 (DIRECT)")
                return True

        except ImportError as e:
            self.log(f"[PROXY-IPv6] Import error: {e}")
            return False
        except Exception as e:
            self.log(f"[PROXY-IPv6] Setup error: {e}")
            return False

    def _get_interface_name(self) -> str:
        """Lay interface name tu settings.yaml."""
        if self._interface_name:
            return self._sanitize_iface_name(self._interface_name)
        try:
            import yaml
            from pathlib import Path
            settings_path = Path(__file__).parent.parent.parent / "config" / "settings.yaml"
            if settings_path.exists():
                with open(settings_path, 'r', encoding='utf-8') as f:
                    cfg = yaml.safe_load(f) or {}
                self._interface_name = cfg.get('ipv6_rotation', {}).get('interface_name', 'Ethernet')
            else:
                self._interface_name = 'Ethernet'
        except Exception:
            self._interface_name = 'Ethernet'
        self._interface_name = self._sanitize_iface_name(self._interface_name)
        return self._interface_name

    def _get_gateway_for_ipv6(self, ipv6: str) -> str:
        """Auto-compute gateway = network_address + 1 (giong ipv6_rotator)."""
        try:
            net = ipaddress.IPv6Network(f"{ipv6}/64", strict=False)
            gw = net.network_address + 1
            return str(gw)
        except Exception:
            return ""

    def _get_onlink_prefix(self, gateway: str) -> str:
        """Lay /64 prefix tu gateway (cho on-link route)."""
        try:
            net = ipaddress.IPv6Network(f"{gateway}/64", strict=False)
            return str(net)
        except Exception:
            return ""

    def _run_cmd(self, cmd: str, timeout: int = 20) -> bool:
        """Run netsh command, log failure. Return True if OK."""
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                stderr = (r.stderr or "").strip()
                # "already exists" / "not found" khi delete → OK
                if 'already' in stderr.lower() or 'object already exists' in stderr.lower():
                    return True
                if 'delete' in cmd and ('not found' in stderr.lower() or 'object' in stderr.lower()):
                    return True
                self.log(f"[PROXY-IPv6] [WARN] FAIL: {cmd[:80]}")
                if stderr:
                    self.log(f"[PROXY-IPv6] [WARN] stderr: {stderr[:150]}")
                return False
            return True
        except subprocess.TimeoutExpired:
            self.log(f"[PROXY-IPv6] [WARN] Timeout ({timeout}s): {cmd[:60]}...")
            return False
        except Exception as e:
            self.log(f"[PROXY-IPv6] [WARN] Error: {e}")
            return False

    # ================================================================
    # v1.0.613: Firewall block IPv4 cho Chrome (VM direct mode)
    # ================================================================
    _FW_RULE_PREFIX = "VE3_Block_IPv4_Chrome"

    def _get_chrome_paths(self) -> list:
        """Lay duong dan Chrome Portable executables."""
        from pathlib import Path
        tool_dir = Path(__file__).parent.parent.parent
        paths = []
        for folder in ["GoogleChromePortable", "GoogleChromePortable - Copy"]:
            exe = tool_dir / folder / "GoogleChromePortable.exe"
            if exe.exists():
                paths.append(str(exe))
            # Cung check chrome.exe trong App
            app_exe = tool_dir / folder / "App" / "Chrome-bin" / "chrome.exe"
            if app_exe.exists():
                paths.append(str(app_exe))
        return paths

    def _block_ipv4_for_chrome(self):
        """
        v1.0.615: Block outbound IPv4 cho Chrome executables.
        Chrome khong the dung IPv4 → bat buoc IPv6 truc tiep.
        RDP/system van dung IPv4 binh thuong.

        NOTE: netsh khong cho protocol=any + remoteip cung luc
        → tach thanh 2 rules: TCP va UDP rieng.
        """
        from pathlib import Path

        # Xoa rules cu truoc (tranh duplicate)
        self._unblock_ipv4_for_chrome()

        chrome_paths = self._get_chrome_paths()
        if not chrome_paths:
            self.log("[PROXY-IPv6] [WARN] Khong tim thay Chrome Portable, skip firewall")
            return

        self.log(f"[PROXY-IPv6] Firewall: Tim thay {len(chrome_paths)} Chrome exe(s)")

        rule_idx = 0
        for exe_path in chrome_paths:
            folder_name = Path(exe_path).parent.parent.name
            ok = True
            # TCP rule
            rule_tcp = f"{self._FW_RULE_PREFIX}_{rule_idx}"
            cmd_tcp = (
                f'netsh advfirewall firewall add rule '
                f'name="{rule_tcp}" dir=out action=block '
                f'program="{exe_path}" '
                f'protocol=tcp remoteip=0.0.0.0-255.255.255.255'
            )
            if not self._run_cmd(cmd_tcp):
                ok = False
            rule_idx += 1

            # UDP rule
            rule_udp = f"{self._FW_RULE_PREFIX}_{rule_idx}"
            cmd_udp = (
                f'netsh advfirewall firewall add rule '
                f'name="{rule_udp}" dir=out action=block '
                f'program="{exe_path}" '
                f'protocol=udp remoteip=0.0.0.0-255.255.255.255'
            )
            if not self._run_cmd(cmd_udp):
                ok = False
            rule_idx += 1

            if ok:
                self.log(f"[PROXY-IPv6] [v] Firewall: Block IPv4 (TCP+UDP) cho {folder_name}")
            else:
                self.log(f"[PROXY-IPv6] [WARN] Firewall: Khong block duoc IPv4 cho {exe_path}")

    def _unblock_ipv4_for_chrome(self):
        """v1.0.614: Go tat ca firewall rules block IPv4 cho Chrome."""
        for i in range(20):  # Max 20 rules (moi Chrome = 2 rules TCP+UDP)
            rule_name = f"{self._FW_RULE_PREFIX}_{i}"
            self._run_cmd(
                f'netsh advfirewall firewall delete rule name="{rule_name}"',
                timeout=5
            )

    def cleanup_old_addresses(self, keep_ips: list = None):
        """
        v1.0.611: Xoa TAT CA IPv6 addresses cu tren interface (tru keep_ips).
        Goi TRUOC khi add Pool IPs de tranh routing conflict tu 41+ static IPs.
        """
        keep_ips = [ip.lower() for ip in (keep_ips or [])]
        iface = self._get_interface_name()
        try:
            addr_check = subprocess.run(
                f'netsh interface ipv6 show address "{iface}"',
                shell=True, capture_output=True, text=True, timeout=15
            )
            if addr_check.returncode != 0:
                return

            # Tim prefix chung cua pool IPs (vd "2001:ee0:b004:")
            pool_prefix = ""
            if keep_ips:
                parts = keep_ips[0].split(":")
                if len(parts) >= 3:
                    pool_prefix = ":".join(parts[:3]) + ":"

            if not pool_prefix:
                return

            removed = 0
            for line in (addr_check.stdout or "").split('\n'):
                line_stripped = line.strip()
                if pool_prefix in line_stripped.lower() and 'Address' in line:
                    parts = line_stripped.split()
                    for p in parts:
                        if pool_prefix in p.lower() and p.lower() not in keep_ips:
                            try:
                                subprocess.run(
                                    f'netsh interface ipv6 delete address "{iface}" {p}',
                                    shell=True, capture_output=True, text=True, timeout=15
                                )
                                removed += 1
                            except Exception:
                                pass
            if removed:
                self.log(f"[PROXY-IPv6] [CLEANUP] Xoa {removed} IPv6 cu tren interface")
        except Exception:
            pass

    def _cleanup_old_routes(self, iface: str, keep_gateway: str = ""):
        """
        v1.0.643: Cleanup routes - giu route ::/0 cua _default_route_gateway.
        Chi xoa routes ::/0 cua gateway KHONG ai dung.
        """
        try:
            route_check = subprocess.run(
                'netsh interface ipv6 show route',
                shell=True, capture_output=True, text=True, timeout=15
            )
            if route_check.returncode != 0:
                return

            # Collect tat ca gateway can giu
            keep_set = set(IPv6Provider._active_gateways.values())
            if keep_gateway:
                keep_set.add(keep_gateway)
            # v1.0.643: Luon giu default route gateway
            if IPv6Provider._default_route_gateway:
                keep_set.add(IPv6Provider._default_route_gateway)

            for line in (route_check.stdout or "").split('\n'):
                if '::/0' in line and iface.lower() in line.lower():
                    parts = line.strip().split()
                    for p in parts:
                        if ':' in p and '::' in p and p != '::/0':
                            if p in keep_set:
                                continue
                            del_cmd = f'netsh interface ipv6 delete route ::/0 "{iface}" {p}'
                            try:
                                subprocess.run(del_cmd, shell=True, capture_output=True, text=True, timeout=15)
                                self.log(f"[PROXY-IPv6] [CLEANUP] Xoa route cu: ::/0 via {p}")
                            except Exception:
                                pass
        except Exception:
            pass

    def _add_to_interface(self, ipv6_address: str, gateway: str = "") -> bool:
        """
        v1.0.609/610: Add IPv6 address to Windows interface via netsh.
        Giong logic cua ipv6_rotator.set_ipv6() nhung cho server mode (nhieu worker).

        Steps:
        1. Cleanup old default routes (tranh routing conflict)
        2. Add IPv6 to interface
        3. Firewall ICMPv6 NDP
        4. On-link route + default route
        5. IPv6 prefix policy (prefer IPv6)
        6. Wait 8s NDP + ping gateway
        7. DNS (non-fatal, chay rieng)
        8. Verify
        """
        iface = self._sanitize_iface_name(self._get_interface_name())

        if not self._build_logged:
            self.log(f"[PROXY-IPv6] Build: {IPV6_PROVIDER_BUILD} iface={iface}")
            self._build_logged = True

        if not gateway:
            gateway = self._get_gateway_for_ipv6(ipv6_address)
        if not gateway:
            self.log(f"[PROXY-IPv6] Khong the tinh gateway cho {ipv6_address}")
            return False

        onlink_prefix = self._get_onlink_prefix(gateway)

        self.log(f"[PROXY-IPv6] Adding {ipv6_address} to interface '{iface}' (gw: {gateway})")

        # Step 1: Cleanup old default routes (v1.0.610)
        self._cleanup_old_routes(iface, keep_gateway=gateway)

        # Step 2: Firewall - mo IPv6 (ICMPv6 NDP + TCP/UDP outbound)
        # v1.0.611: Mo ca TCP/UDP outbound cho IPv6, khong chi ICMPv6
        self._run_cmd('netsh advfirewall firewall add rule name="ICMPv6-NDP-In" dir=in action=allow protocol=icmpv6')
        self._run_cmd('netsh advfirewall firewall add rule name="ICMPv6-NDP-Out" dir=out action=allow protocol=icmpv6')
        self._run_cmd('netsh advfirewall firewall add rule name="IPv6-TCP-Out" dir=out action=allow protocol=tcp remoteip=any localip=any')
        self._run_cmd('netsh advfirewall firewall add rule name="IPv6-UDP-Out" dir=out action=allow protocol=udp remoteip=any localip=any')

        # Step 3: Add IPv6 address
        ok_addr = self._run_cmd(f'netsh interface ipv6 add address "{iface}" {ipv6_address}')
        if not ok_addr:
            self.log(f"[PROXY-IPv6] [!] Add address failed - may already exist, continuing...")

        # Step 4: On-link route + default route
        if onlink_prefix:
            self._run_cmd(f'netsh interface ipv6 add route {onlink_prefix} "{iface}"')

        # v1.0.643: SINGLE-ROUTE - chi 1 route ::/0 duy nhat
        # v1.0.678: Chi update route khi rotate_to() da reset _default_route_gateway
        #   Initial setup: worker 1 KHONG doi route cua worker 0 (gateway cu van hop le)
        #   Rotation: rotate_to() reset _default_route_gateway → _add_to_interface them route moi
        with IPv6Provider._route_lock:
            if not IPv6Provider._default_route_gateway:
                # Chua co route ::/0 → them moi (worker dau tien HOAC sau rotation reset)
                self._run_cmd(f'netsh interface ipv6 add route ::/0 "{iface}" {gateway}')
                IPv6Provider._default_route_gateway = gateway
                self.log(f"[PROXY-IPv6] [v] Added DEFAULT route ::/0 via {gateway}")
            else:
                # Da co route ::/0 → dung chung (initial setup worker khac)
                self.log(f"[PROXY-IPv6] [v] Reuse DEFAULT route ::/0 via {IPv6Provider._default_route_gateway} (skip add)")

        # Step 5: IPv6 prefix policy - uu tien IPv6 (giong ipv6_rotator)
        self._run_cmd('netsh interface ipv6 set prefixpolicy ::1/128 50 0')
        self._run_cmd('netsh interface ipv6 set prefixpolicy ::/0 40 1')
        self._run_cmd('netsh interface ipv6 set prefixpolicy 2002::/16 30 2')
        self._run_cmd('netsh interface ipv6 set prefixpolicy ::ffff:0:0/96 10 4')

        # Step 6: Wait for NDP neighbor discovery (8s giong rotator)
        self.log(f"[PROXY-IPv6] Doi NDP discovery (8s)...")
        time.sleep(8)

        # Ping gateway to trigger NDP + verify reachable
        # v1.0.644: Ping DEFAULT gateway (khong phai gateway rieng cua worker)
        # Worker o subnet khac khong the ping gateway rieng qua on-link
        ping_gw = gateway
        try:
            ping_cmd = f'ping -6 -n 2 -w 3000 -S {ipv6_address} {ping_gw}'
            ping_result = subprocess.run(
                ping_cmd, shell=True, capture_output=True, text=True, timeout=15
            )
            if ping_result.returncode == 0 and 'Reply from' in (ping_result.stdout or ''):
                self.log(f"[PROXY-IPv6] [v] Gateway {ping_gw} reachable from {ipv6_address}!")
            else:
                # Fallback: ping khong chi dinh source
                ping_result2 = subprocess.run(
                    f'ping -6 -n 2 -w 3000 {ping_gw}',
                    shell=True, capture_output=True, text=True, timeout=15
                )
                if ping_result2.returncode == 0 and 'Reply from' in (ping_result2.stdout or ''):
                    self.log(f"[PROXY-IPv6] [v] Gateway {ping_gw} reachable (no source bind)")
                else:
                    self.log(f"[PROXY-IPv6] [!] Gateway {ping_gw} KHONG reply (NDP chua xong?)")
        except Exception:
            self.log(f"[PROXY-IPv6] [!] Gateway ping timeout")

        # Step 7: DNS (non-fatal - chay rieng, timeout rieng)
        # v1.0.679: Dung MikroTik gateway lam DNS primary (allow-remote-requests=true)
        # VNPT drop/throttle UDP IPv6 toi Google DNS → DNS timeout 60s → mat mang
        # Gateway ::1 relay qua ISP DNS noi bo → instant
        # Google DNS lam fallback (index=2) phong khi gateway khong co DNS relay
        for dns_cmd in [
            f'netsh interface ipv6 set dnsservers "{iface}" static {gateway} primary',
            f'netsh interface ipv6 add dnsservers "{iface}" 2001:4860:4860::8888 index=2',
        ]:
            try:
                subprocess.run(dns_cmd, shell=True, capture_output=True, timeout=15)
            except Exception:
                self.log(f"[PROXY-IPv6] [WARN] DNS command timeout (non-fatal)")

        # Step 8: Verify IP on interface
        try:
            verify = subprocess.run(
                f'netsh interface ipv6 show address "{iface}"',
                shell=True, capture_output=True, text=True, timeout=10
            )
            if ipv6_address in (verify.stdout or ''):
                self.log(f"[PROXY-IPv6] [v] VERIFIED: {ipv6_address} co tren interface")
            else:
                self.log(f"[PROXY-IPv6] [!] WARN: {ipv6_address} KHONG thay tren interface!")
        except Exception:
            pass

        self._current_ipv6 = ipv6_address
        self._current_gateway = gateway
        # v1.0.640: Register gateway → worker khac se KHONG xoa route nay
        if hasattr(self, 'worker_id') and gateway:
            IPv6Provider._active_gateways[self.worker_id] = gateway
            self.log(f"[PROXY-IPv6] [v] Registered gateway {gateway} for worker {self.worker_id}")

        # v1.0.643: Start NDP keepalive thread cho worker nay
        self._start_ndp_keepalive(ipv6_address, gateway, iface)

        self.log(f"[PROXY-IPv6] [v] Setup complete for {ipv6_address}")
        return True

    def _remove_from_interface(self, ipv6_address: str):
        """v1.0.609: Remove IPv6 address tu Windows interface."""
        if not ipv6_address:
            return
        iface = self._get_interface_name()
        try:
            subprocess.run(
                f'netsh interface ipv6 delete address "{iface}" {ipv6_address}',
                shell=True, capture_output=True, timeout=10
            )
            self.log(f"[PROXY-IPv6] Removed {ipv6_address} from interface")
        except Exception:
            pass

    def _start_ndp_keepalive(self, ipv6_address: str, gateway: str, iface: str):
        """
        v1.0.643: Start NDP keepalive thread cho worker nay.
        v1.0.644: Ping DEFAULT gateway (khong phai gateway rieng cua worker).
        Worker o subnet khac (VD 3072::) khong the ping gateway cua subnet khac (VD 30e7::1)
        qua on-link. Nhung traffic van chay tot vi SOCKS5 bind source IP
        va MikroTik forward tu bat ky source subnet.
        → Keepalive ping default gateway de giu NDP cache cho route ::/0.
        """
        # Stop thread cu neu co
        if self._ndp_keepalive:
            self._ndp_keepalive.stop()

        # v1.0.644: Dung default gateway (gateway cua route ::/0) thay vi gateway rieng
        # Gateway rieng (VD 3072::1) co the unreachable vi on-link route fail
        # Default gateway (VD 30e7::1) LUON reachable vi co route ::/0
        keepalive_gw = gateway
        self._ndp_keepalive = NDPKeepalive(
            gateway=keepalive_gw,
            source_ip=ipv6_address,
            interface=iface,
            interval=20,
            log_func=self.log
        )
        self._ndp_keepalive.start()

    def setup_dedicated(self, worker_id: int, port: int, ipv6_address: str,
                        gateway: str = "") -> bool:
        """
        Setup proxy rieng cho 1 worker (Server mode).
        Moi worker co SOCKS5 proxy rieng, IPv6 rieng.

        v1.0.609: Them gateway param + add IPv6 to Windows interface truoc khi tao SOCKS5 proxy.
        Pool IP phai co tren interface de SOCKS5 proxy co the bind va route traffic.
        """
        self.worker_id = worker_id
        self.port = port

        try:
            # v1.0.609: Add IPv6 to Windows interface (giong VM mode dung ipv6_rotator.set_ipv6)
            if gateway or ipv6_address:
                if not self._add_to_interface(ipv6_address, gateway):
                    self.log(f"[PROXY-IPv6] Warning: Could not add {ipv6_address} to interface, proxy may not work")

            from modules.ipv6_proxy import IPv6SocksProxy
            self._proxy = IPv6SocksProxy(
                listen_port=port,
                ipv6_address=ipv6_address,
                log_func=self.log
            )
            if self._proxy.start():
                # v1.0.627: Test internet connectivity thuc te (khong chi ping gateway)
                if not self._test_internet_via_proxy(port, ipv6_address):
                    self.log(f"[PROXY-IPv6] [!] IPv6 {ipv6_address} KHONG co internet! Worker {worker_id} se KHONG hoat dong.", "ERROR")
                    self._activated = True
                    self._ready = False  # Mark NOT ready
                    return False
                self._activated = True
                self._ready = True
                self.log(f"[PROXY-IPv6] [v] Dedicated worker {worker_id}: IPv6={ipv6_address}, port={port}")
                return True
            return False
        except Exception as e:
            self.log(f"[PROXY-IPv6] Dedicated setup error: {e}")
            return False

    def _test_internet_via_proxy(self, proxy_port: int, ipv6_address: str, timeout: int = 10) -> bool:
        """
        v1.0.627: Test ket noi internet THUC TE qua SOCKS5 proxy.
        Gateway ping OK khong co nghia internet hoat dong.
        """
        import subprocess
        try:
            # Test 1: curl qua SOCKS5 proxy
            cmd = (f'curl -x socks5h://127.0.0.1:{proxy_port} '
                   f'--connect-timeout {timeout} -s -o nul -w "%{{http_code}}" '
                   f'https://www.google.com')
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout + 5)
            if result.returncode == 0 and result.stdout.strip().startswith(('2', '3')):
                self.log(f"[PROXY-IPv6] [v] Internet OK qua {ipv6_address} (proxy port {proxy_port})")
                return True
        except Exception as e:
            self.log(f"[PROXY-IPv6] [!] curl test error: {e}")

        try:
            # Test 2: Fallback - ping Google DNS IPv6
            cmd = f'ping -6 -n 1 -w {timeout * 1000} 2001:4860:4860::8888'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout + 3)
            if result.returncode == 0 and 'Reply from' in (result.stdout or ''):
                self.log(f"[PROXY-IPv6] [v] Google DNS reachable qua IPv6")
                return True
        except Exception:
            pass

        self.log(f"[PROXY-IPv6] [!] KHONG co internet qua {ipv6_address}!")
        return False

    def rotate(self, reason: str = "403") -> bool:
        """Doi sang IPv6 tiep theo trong danh sach."""
        if not self._rotator:
            self.log("[PROXY-IPv6] Rotator chua khoi tao!")
            return False

        # Chi worker 0 rotate (VM mode)
        if self.worker_id != 0 and not self._proxy:
            self.log(f"[PROXY-IPv6] Worker {self.worker_id}: Skip rotate (worker 0 se lam)")
            return True

        new_ip = self._rotator.rotate()
        if new_ip:
            # v1.0.612: Direct mode (VM) - rotator.rotate() da set_ipv6() len interface
            # Server mode (dedicated) - van can update SOCKS5 proxy
            if self._proxy:
                self._proxy.set_ipv6(new_ip)
            self.log(f"[PROXY-IPv6] [v] Rotated ({reason}): → {new_ip}")
            return True

        self.log(f"[PROXY-IPv6] [x] Rotate failed ({reason})")
        return False

    def rotate_to(self, new_ipv6: str, gateway: str = "") -> bool:
        """
        Doi sang 1 IPv6 cu the (Server mode dung).

        v1.0.609: Them gateway param. Remove old IP, add new IP to interface.
        v1.0.640: Update _active_gateways khi rotate.
        v1.0.643: Giu nguyen route ::/0, chi doi IP address + on-link route.
        v1.0.678: Reset _default_route_gateway khi gateway cu cua worker nay
                   chinh la default route → _add_to_interface() se them route moi.
                   Worker khac rotate (gateway != default) → KHONG doi route.
        """
        # v1.0.643: Stop NDP keepalive cu truoc khi rotate
        if self._ndp_keepalive:
            self._ndp_keepalive.stop()
            self._ndp_keepalive = None

        # v1.0.640: Xoa gateway cu khoi tracking TRUOC khi cleanup routes
        old_gw = self._current_gateway
        if hasattr(self, 'worker_id') and self.worker_id in IPv6Provider._active_gateways:
            del IPv6Provider._active_gateways[self.worker_id]

        # v1.0.678: Neu gateway cu cua worker nay LA default route
        # → Pool se burn subnet nay → gateway cu bi xoa tren MikroTik
        # → PHAI reset de _add_to_interface() them route moi
        # Neu gateway cu KHONG phai default → default van hop le, KHONG doi
        with IPv6Provider._route_lock:
            if old_gw and old_gw == IPv6Provider._default_route_gateway:
                iface = self._get_interface_name()
                self._run_cmd(f'netsh interface ipv6 delete route ::/0 "{iface}" {old_gw}')
                IPv6Provider._default_route_gateway = ""
                self.log(f"[PROXY-IPv6] [v] Reset DEFAULT route ::/0 (old gw {old_gw} burned by Pool)")

        # Remove old IP from interface
        if self._current_ipv6 and self._current_ipv6 != new_ipv6:
            self._remove_from_interface(self._current_ipv6)

        if self._proxy:
            self._proxy.stop()

        try:
            # v1.0.609: Add new IP to interface
            # v1.0.678: _add_to_interface se them ::/0 moi neu da reset o tren
            if not self._add_to_interface(new_ipv6, gateway):
                self.log(f"[PROXY-IPv6] Warning: Could not add {new_ipv6} to interface")

            from modules.ipv6_proxy import IPv6SocksProxy
            self._proxy = IPv6SocksProxy(
                listen_port=self.port,
                ipv6_address=new_ipv6,
                log_func=self.log
            )
            self._proxy.start()
            self.log(f"[PROXY-IPv6] [v] Rotated to: {new_ipv6}")
            return True
        except Exception as e:
            self.log(f"[PROXY-IPv6] Rotate to {new_ipv6} failed: {e}")
            return False

    def get_chrome_arg(self) -> str:
        """Tra ve proxy arg cho Chrome.

        v1.0.617: VM direct mode → "" (firewall block IPv4)
        Server dedicated mode → "socks5://..." (SOCKS5 proxy)
        """
        if getattr(self, '_direct_mode', False):
            return ""
        return f"socks5://127.0.0.1:{self.port}"

    def get_current_ip(self) -> str:
        """Tra ve IPv6 hien tai."""
        if self._rotator and self._rotator.current_ipv6:
            return self._rotator.current_ipv6
        if self._proxy and self._proxy.ipv6_address:
            return self._proxy.ipv6_address
        return "unknown"

    def stop(self):
        """Dung proxy/cleanup."""
        # v1.0.643: Stop NDP keepalive thread truoc
        if self._ndp_keepalive:
            self._ndp_keepalive.stop()
            self._ndp_keepalive = None

        if self._proxy:
            self._proxy.stop()
            self._proxy = None

        # v1.0.640: Unregister gateway truoc khi remove IP
        wid = self.worker_id if hasattr(self, 'worker_id') else -1
        if wid >= 0 and wid in IPv6Provider._active_gateways:
            del IPv6Provider._active_gateways[wid]

        # v1.0.643: Xoa route ::/0 CHI khi day la worker cuoi cung (khong con ai active)
        with IPv6Provider._route_lock:
            if not IPv6Provider._active_gateways and IPv6Provider._default_route_gateway:
                # Worker cuoi cung → xoa route ::/0
                iface = self._get_interface_name()
                gw = IPv6Provider._default_route_gateway
                self._run_cmd(f'netsh interface ipv6 delete route ::/0 "{iface}" {gw}')
                self.log(f"[PROXY-IPv6] [v] Removed DEFAULT route ::/0 via {gw} (last worker)")
                IPv6Provider._default_route_gateway = ""

        # v1.0.609: Remove IP from interface khi stop (dedicated/server mode)
        if self._current_ipv6:
            self._remove_from_interface(self._current_ipv6)
            self._current_ipv6 = None
        self._current_gateway = None
        self._ready = False
        self.log("[PROXY-IPv6] Stopped")

    def get_type(self) -> str:
        return "ipv6"

    def test_connectivity(self) -> bool:
        """Test IPv6 connectivity qua curl."""
        if self._rotator:
            return self._rotator.test_ipv6_connectivity()
        return False

    def get_rotator(self):
        """Tra ve IPv6Rotator instance (backward compat)."""
        return self._rotator
