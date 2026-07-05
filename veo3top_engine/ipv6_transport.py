"""
IPv6 transport (cách B) — dùng CHUNG pool IPv6 giống các tool khác (server/flowkit).
Thay cho WARP: mỗi request generate/poll đi qua 1 IPv6 riêng (pool ~vô hạn) -> hết 429/unusual.

ĐÃ KIỂM CHỨNG (test cùng token+account ultra): WARP=['unusual','unusual','unusual'] chặn sạch,
IPv6 xoay source=['ok','ok','ok','unusual','ok'] -> 4/5 = 200. IPv6 pool fix được rate-limit.

Thiết kế BỀN cho ĐA TIẾN TRÌNH (mỗi mã = 1 subprocess dùng chung 1 interface):
- CHỈ 1 default route ::/0 ổn định (qua MikroTik). KHÔNG đổi route khi rotate
  (tránh các subprocess giành nhau default route -> đứt mạng nhau).
- rotate = pool cấp IP mới -> add address (idempotent) -> đổi SOURCE BIND của SOCKS.
  Bind source là per-socket, không đụng global state -> nhiều mã chạy song song OK.
- Lấy IP + điều phối tránh trùng qua pool API (http://192.168.88.146:8765).
netsh add address/route cần Admin (các tool khác cũng chạy Admin). Nếu IP pool đã bound sẵn
trên interface (thường có sẵn ~60 IP) thì bind source chạy được cả khi không Admin.
"""
import os, sys, subprocess, threading, time

_FLOWKIT = r"D:\VE3_SUITE\server\flowkit"
if _FLOWKIT not in sys.path:
    sys.path.insert(0, _FLOWKIT)
import ipv6_proxy as ip6   # IPv6SocksProxy (bind source IPv6) — code đã kiểm chứng

try:
    import requests
except Exception:
    requests = None

POOL_URL = os.environ.get("VEO3TOP_POOL_URL", "http://192.168.88.146:8765")
IFACE = os.environ.get("VEO3TOP_IPV6_IFACE", "Ethernet")

_ROUTE_LOCK = threading.Lock()
_ROUTE_READY = False


def _sh(cmd, timeout=15):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                              creationflags=0x08000000)  # CREATE_NO_WINDOW: ẩn cửa sổ cmd (netsh/ping)
    except Exception:
        return None


def _has_default_route():
    # Bảng route hiển thị INTERFACE INDEX (vd 13), không phải tên "Ethernet".
    # Route mặc định hợp lệ = có dòng ::/0 kèm next-hop MikroTik (2001:ee0:b004..) hoặc fe80.
    r = _sh('netsh interface ipv6 show route')
    if not r or not r.stdout:
        return False
    for line in r.stdout.splitlines():
        if '::/0' not in line:
            continue
        for p in line.split():
            if p != '::/0' and '::' in p and (p.startswith('2001:ee0:b004') or p.startswith('fe80')):
                return True
    return False


def _router_gateway():
    """Tìm gateway MikroTik còn sống (neighbor cờ Router, ưu tiên global 30xx::1 Reachable)."""
    r = _sh(f'netsh interface ipv6 show neighbors "{IFACE}"')
    reach_ll = None
    if r and r.stdout:
        for line in r.stdout.splitlines():
            if 'Router' not in line:
                continue
            parts = line.split()
            if not parts:
                continue
            addr = parts[0]
            if addr.startswith('2001:ee0:b004') and 'Reachable' in line:
                return addr           # global gw còn sống -> tốt nhất
            if addr.startswith('fe80') and reach_ll is None:
                reach_ll = addr
    return reach_ll


def ensure_route(gateway_hint="", log=print):
    """Đảm bảo có 1 default route ::/0 ổn định. Chạy 1 lần/tiến trình (idempotent)."""
    global _ROUTE_READY
    with _ROUTE_LOCK:
        if _ROUTE_READY or _has_default_route():
            _ROUTE_READY = True
            return True
        gw = _router_gateway() or gateway_hint
        if not gw:
            log("[ipv6] khong tim thay MikroTik gateway")
            return False
        _sh(f'netsh interface ipv6 add route ::/0 "{IFACE}" {gw} store=active')
        ok = _has_default_route()
        _ROUTE_READY = ok
        log(f"[ipv6] default route ::/0 via {gw} -> {'OK' if ok else 'FAIL (can Admin?)'}")
        return ok


def _addr_bound(ip):
    r = _sh(f'netsh interface ipv6 show address "{IFACE}"')
    return bool(r and r.stdout and ip in r.stdout)


def ensure_address(ip, gateway="", log=print):
    """Bảo đảm IP có trên interface (để bind source). Idempotent. Cần Admin nếu chưa có."""
    if _addr_bound(ip):
        return True
    _sh(f'netsh interface ipv6 add address "{IFACE}" {ip}')
    # NDP: cho MikroTik học địa chỉ này (định tuyến chiều về)
    gw = gateway or (':'.join(ip.split(':')[:4]) + '::1')
    _sh(f'ping -6 -n 1 -w 2000 -S {ip} {gw}', timeout=6)
    return _addr_bound(ip)


# --- IP bound sẵn: mỗi account 1 IP KHÁC NHAU (egress đa dạng, bind KHÔNG cần admin — đã kiểm chứng) ---
_BOUND_CACHE = None
_ASSIGN_LOCK = threading.Lock()
_ASSIGNED_BOUND = set()
_ASSIGNED_SUBNETS = set()

def _subnet_of(ip):
    parts = ip.split(':')
    return parts[3] if len(parts) > 3 else ""

def _bound_pool_ips():
    """Liệt kê IPv6 pool ĐÃ BOUND trên interface (bỏ gateway ::1). Cache 1 lần."""
    global _BOUND_CACHE
    if _BOUND_CACHE is None:
        r = _sh(f'netsh interface ipv6 show address "{IFACE}"')
        ips = []
        if r and r.stdout:
            for line in r.stdout.splitlines():
                for p in line.split():
                    if p.startswith('2001:ee0:b004') and p.count(':') >= 5 and not p.endswith('::1'):
                        ips.append(p)
        _BOUND_CACHE = ips
    return _BOUND_CACHE

def _pick_bound_ip():
    """1 IP bound sẵn, ưu tiên SUBNET chưa dùng (mỗi account 1 subnet /64 khác -> quota reCAPTCHA riêng).
    VEO3TOP_IPV6_FROM_END=1 (nhà máy ẢNH) -> pick từ CUỐI list -> KHÔNG đụng subnet nhà máy VIDEO (pick từ đầu)."""
    with _ASSIGN_LOCK:
        ips = _bound_pool_ips()
        if os.environ.get("VEO3TOP_IPV6_FROM_END") == "1":
            ips = list(reversed(ips))
        # ưu tiên subnet mới (tránh ::1/::10/::100 gateway-like — dùng host ngẫu nhiên)
        for ip in ips:
            sub = _subnet_of(ip)
            if sub not in _ASSIGNED_SUBNETS and ip not in _ASSIGNED_BOUND and not ip.endswith(("::10", "::100")):
                _ASSIGNED_SUBNETS.add(sub); _ASSIGNED_BOUND.add(ip); return ip
        # hết subnet mới -> IP chưa dùng bất kỳ
        for ip in ips:
            if ip not in _ASSIGNED_BOUND:
                _ASSIGNED_BOUND.add(ip); return ip
    return None

def _release_bound_ip(ip):
    with _ASSIGN_LOCK:
        _ASSIGNED_BOUND.discard(ip)
        _ASSIGNED_SUBNETS.discard(_subnet_of(ip))


class IPv6Transport:
    """1 SOCKS proxy bind source IPv6 cho 1 tiến trình (1 mã). Rotate = đổi source bind."""

    def __init__(self, worker, port=1088, pool_url=POOL_URL, log=print):
        self.worker = worker
        self.port = int(port)
        self.pool_url = pool_url.rstrip("/")
        self.log = log
        self.proxy = None
        self.ip = None
        self.gw = None
        self._lock = threading.Lock()

    # --- pool API ---
    def _pool_get(self):
        try:
            r = requests.get(f"{self.pool_url}/api/get_ip?worker={self.worker}", timeout=8).json()
            if r.get("success"):
                return r["ip"], r.get("gateway", "")
        except Exception as e:
            self.log(f"[ipv6] pool get_ip error: {e}")
        return None, None

    def _pool_rotate(self):
        try:
            r = requests.post(f"{self.pool_url}/api/rotate_ip",
                              json={"ip": self.ip, "reason": "429", "worker": self.worker},
                              timeout=12).json()
            if r.get("success"):
                return (r.get("new_ip") or r.get("ip")), r.get("gateway", "")
        except Exception as e:
            self.log(f"[ipv6] pool rotate error: {e}")
        return None, None

    def ping(self):
        try:
            return bool(requests.get(f"{self.pool_url}/api/ping", timeout=5).json().get("ok"))
        except Exception:
            return False

    def proxy_url(self):
        # socks5h: proxy tự resolve hostname -> lấy AAAA -> bind source IPv6
        return f"socks5h://127.0.0.1:{self.port}"

    def start(self):
        # XIN POOL API LÀ CHÍNH (tool luôn chạy admin): pool ĐIỀU PHỐI in_use/burned -> ảnh & video & tool khác
        # KHÔNG đụng IP nhau. netsh add address cần admin (đã có). Fallback IP bound sẵn nếu pool lỗi.
        gw = ""; ip = None
        self._from_bound = False
        if requests is not None and self.ping():
            ip, gw = self._pool_get()
            if ip:
                ensure_route(gw, self.log)
                ensure_address(ip, gw, self.log)
                if not _addr_bound(ip):
                    # netsh add fail (không admin?) -> TRẢ IP về pool (tránh leak in_use), dùng bound sẵn
                    try:
                        requests.post(f"{self.pool_url}/api/release_ip",
                                      json={"ip": ip, "worker": self.worker}, timeout=6)
                    except Exception:
                        pass
                    ip = None
        if ip:
            self.log(f"[ipv6] {self.worker}: pool cấp {ip}")
        else:
            # fallback: IP bound sẵn (không cần admin) — dự phòng khi pool lỗi
            ip = _pick_bound_ip()
            self._from_bound = True
            if not ip:
                self.log(f"[ipv6] {self.worker}: hết IP (pool lỗi + hết bound sẵn)")
                return None
            self.log(f"[ipv6] {self.worker}: fallback IP bound sẵn {ip}")
        self.proxy = ip6.IPv6SocksProxy(listen_port=self.port, ipv6_address=ip, log_func=lambda *a: None)
        self.proxy.worker_name = self.worker
        self.proxy.pool_url = self.pool_url
        self.proxy.iface = IFACE
        if not self.proxy.start():
            self.log("[ipv6] SOCKS proxy start lỗi")
            return None
        self.ip, self.gw = ip, gw
        self.log(f"[ipv6] transport sẵn sàng: {ip} qua {self.proxy_url()}")
        return self.proxy_url()

    MIN_ROTATE_INTERVAL = 3.0   # gộp rotate: nhiều scene cùng mã gọi trong <3s -> dùng chung IP (tránh đốt pool)

    def rotate(self):
        """Đổi source IPv6 (429/unusual liên tục). KHÔNG đụng default route. Trả IP mới hoặc None.
        Gộp: nếu vừa rotate <MIN_ROTATE_INTERVAL giây -> trả IP hiện tại (3 scene concurrent không rotate 3 lần)."""
        with self._lock:
            now = time.time()
            if now - getattr(self, "_last_rotate_ts", 0) < self.MIN_ROTATE_INTERVAL:
                return self.ip
            self._last_rotate_ts = now
            old = self.ip
            if getattr(self, "_from_bound", False):
                # đang dùng IP bound sẵn -> đổi sang IP bound KHÁC (release cái cũ)
                newip = _pick_bound_ip()
                if not newip:
                    self.log("[ipv6] rotate: hết IP bound"); return self.ip
                _release_bound_ip(old)
                if self.proxy: self.proxy.set_ipv6(newip)
                self.ip = newip
                self.log(f"[ipv6] rotate (bound) {old} -> {newip}")
                return newip
            # FIX (2026-07-05): pool có ~136 IP unique NHƯNG _pool_rotate 1 worker chỉ xoay 2 IP -> IP cháy -> unusual.
            # Lấy IP TƯƠI UNIQUE bằng WORKER MỚI mỗi lần (đã đo: worker khác = IP khác 6/6) -> né burn per-IP.
            self._rot_n = getattr(self, "_rot_n", 0) + 1
            ip = gw = None
            try:
                r = requests.get(f"{self.pool_url}/api/get_ip?worker={self.worker}_r{self._rot_n}", timeout=8).json()
                if r.get("success"):
                    ip, gw = r.get("ip"), r.get("gateway", "")
            except Exception as e:
                self.log(f"[ipv6] rotate fresh-ip err: {e}")
            if not ip or ip == old:
                ip, gw = self._pool_rotate()
            if not ip or ip == old:
                ip, gw = self._pool_get()
            if not ip:
                self.log("[ipv6] rotate: hết IP")
                return None
            # trả IP cũ về pool (điều phối: 136 IP xoay vòng, không cạn)
            if old and old != ip:
                try: requests.post(f"{self.pool_url}/api/release_ip", json={"ip": old, "worker": self.worker}, timeout=5)
                except Exception: pass
            ensure_address(ip, gw, self.log)
            if self.proxy:
                self.proxy.set_ipv6(ip)   # chỉ đổi source bind — an toàn đa tiến trình
            self.ip, self.gw = ip, gw
            self.log(f"[ipv6] rotate {old} -> {ip}")
            return ip

    def stop(self):
        try:
            if self.proxy:
                self.proxy.stop()
        except Exception:
            pass
        if getattr(self, "_from_bound", False) and self.ip:
            _release_bound_ip(self.ip)
        elif self.ip and requests is not None:
            # TRẢ IP về pool (điều phối in_use) -> IP được tái sử dụng / cooldown đúng
            try:
                requests.post(f"{self.pool_url}/api/release_ip",
                              json={"ip": self.ip, "worker": self.worker}, timeout=8)
            except Exception:
                pass
