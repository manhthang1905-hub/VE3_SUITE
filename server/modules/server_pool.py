"""
Server Pool - Quan ly nhieu server, load balancing cho VM.

VM co nhieu prompts can tao anh → phan bo qua nhieu server dong thoi.
Moi server co 1 Chrome = 1 anh/lan → N server = N anh song song.

Strategy: Least-queue (chon server it viec nhat)

v1.0.528: Queue-based redesign
- Task da submit vao server = CHAC CHAN se duoc lam (server co queue)
- VM chi can cho den luot, KHONG disable server khi task fail
- Chi disable khi KHONG KET NOI DUOC (server chet/unreachable)
- Tach: connect_fail (server chet) vs task_fail (Google loi - server van OK)
"""
import time
import threading
import requests
from typing import List, Dict, Optional, Any, Callable


class ServerInfo:
    """Thong tin 1 server."""
    def __init__(self, url: str, name: str = "", enabled: bool = True):
        self.url = url.rstrip('/')
        self.name = name or url
        self.enabled = enabled
        self.queue_size = 0
        self.chrome_ready = False
        # Local pending: so prompt VM nay dang gui cho server (chua xong)
        self.local_pending = 0
        # Connection failures (server unreachable) - CO THE disable
        self.connect_fail_count = 0
        self.last_connect_fail_time = 0.0
        # Task failures (Google API error) - KHONG disable server
        self.task_fail_count = 0
        self.last_check = 0.0
        self.total_completed = 0
        self.total_failed = 0
        # v1.0.541: Response time tracking - server nhanh hon duoc uu tien
        self.last_response_time = 0.0  # seconds - thoi gian phan hoi /api/status
        self.recent_timeout_count = 0  # so lan timeout gan day (reset khi success)

    @property
    def fail_count(self):
        """Backward compat - tra ve connect_fail_count."""
        return self.connect_fail_count

    def __repr__(self):
        return f"Server({self.name}, q={self.queue_size}, pending={self.local_pending}, conn_fail={self.connect_fail_count})"


class ServerPool:
    """
    Quan ly pool cac server, load balancing least-queue.

    v1.0.528: Queue-based - task da submit = se duoc lam.
    Chi disable server khi KHONG KET NOI DUOC.

    Usage:
        pool = ServerPool(config)
        pool.refresh_all()

        server = pool.pick_best_server()
        # submit task to server.url
        pool.mark_success(server)
    """

    REFRESH_INTERVAL = 5     # seconds - check server status moi 5s
    CONNECT_COOLDOWN = 60    # seconds - server unreachable → cho 60s roi thu lai
    MAX_CONNECT_FAIL = 5     # Sau 5 lan khong ket noi → disable tam
    WAIT_FOR_SERVER = 30     # seconds - cho khi khong co server nao

    def __init__(self, config: Dict, log_callback: Callable = None):
        self._lock = threading.Lock()
        self._log_fn = log_callback or (lambda msg, level="info": print(f"[ServerPool] {msg}"))
        self.servers: List[ServerInfo] = []

        # Parse config
        self._parse_config(config)

    def _log(self, msg: str, level: str = "info"):
        self._log_fn(msg, level)

    def _parse_config(self, config: Dict):
        """Parse server list tu config. Ho tro ca format cu (single URL) va moi (list)."""
        server_list = config.get('local_server_list', [])

        if server_list:
            # Format moi: list of {url, name, enabled}
            for s in server_list:
                if isinstance(s, str):
                    self.servers.append(ServerInfo(url=s))
                elif isinstance(s, dict):
                    self.servers.append(ServerInfo(
                        url=s.get('url', ''),
                        name=s.get('name', ''),
                        enabled=s.get('enabled', True),
                    ))
        else:
            # Format cu: single URL
            url = config.get('local_server_url', '')
            if url:
                self.servers.append(ServerInfo(url=url, name='default'))

        self._log(f"Loaded {len(self.servers)} servers: {[s.name for s in self.servers]}")

    def available_count(self) -> int:
        """So server kha dung (enabled + ket noi duoc)."""
        with self._lock:
            return sum(1 for s in self.servers if self._is_available(s))

    def _is_available(self, server: ServerInfo) -> bool:
        """Check server co kha dung khong (goi trong lock)."""
        if not server.enabled:
            return False
        if server.connect_fail_count >= self.MAX_CONNECT_FAIL:
            # Check cooldown - chi 60s thay vi 300s
            if time.time() - server.last_connect_fail_time < self.CONNECT_COOLDOWN:
                return False
            # Het cooldown → reset, cho thu lai
            server.connect_fail_count = 0
        # v1.0.542: Server PHAI refresh thanh cong + Chrome ready moi nhan task
        # Chua refresh (last_check=0) = KHONG available (khong biet server co chay khong)
        if server.last_check == 0:
            return False
        if not server.chrome_ready:
            return False
        return True

    def refresh_status(self, server: ServerInfo) -> bool:
        """
        Check status 1 server. Return True neu OK.
        v1.0.531: KHONG tang connect_fail_count khi refresh fail.
        v1.0.541: Track response time de uu tien server nhanh.
        """
        try:
            t0 = time.time()
            resp = requests.get(f"{server.url}/api/status", timeout=10)
            response_time = time.time() - t0
            if resp.status_code == 200:
                data = resp.json()
                with self._lock:
                    server.queue_size = data.get('queue_size', 0)
                    server.chrome_ready = data.get('chrome_ready', False)
                    server.last_check = time.time()
                    server.last_response_time = response_time
                    # Server phan hoi OK → reset connection failures
                    server.connect_fail_count = 0
                return True
        except Exception:
            pass

        # Refresh fail - KHONG tang connect_fail (chi la status check)
        with self._lock:
            server.chrome_ready = False  # v1.0.541: Khong ket noi duoc → khong ready
        return False

    def refresh_all(self):
        """Check tat ca servers (song song, nhanh)."""
        threads = []
        for s in self.servers:
            if s.enabled:
                t = threading.Thread(target=self.refresh_status, args=(s,), daemon=True)
                threads.append(t)
                t.start()

        for t in threads:
            t.join(timeout=10)  # v1.0.542: Tang tu 5s len 10s cho server cham

        # Log v1.0.541: Hien thi chi tiet hon (chrome_ready, response_time)
        with self._lock:
            available = []
            unavailable = []
            for s in self.servers:
                if self._is_available(s):
                    rt = f"{s.last_response_time:.1f}s" if s.last_response_time > 0 else "?"
                    available.append(f"{s.name}(q={s.queue_size},rt={rt})")
                else:
                    reason = "disabled" if not s.enabled else ("chrome_not_ready" if not s.chrome_ready else "connect_fail")
                    unavailable.append(f"{s.name}({reason})")

        self._log(f"Servers OK: {available}")
        if unavailable:
            self._log(f"Servers NOT READY: {unavailable}", "warn")

    def pick_best_server(self, auto_reserve: bool = True) -> Optional[ServerInfo]:
        """
        Chon server tot nhat (queue + local_pending nho nhat).
        Auto refresh neu data cu (> REFRESH_INTERVAL).
        Return None neu khong co server kha dung.

        auto_reserve: Tu dong tang local_pending (+1) de thread khac chon server khac.
        Sau khi xong phai goi release_server() de giam lai.
        """
        now = time.time()

        with self._lock:
            candidates = [s for s in self.servers if self._is_available(s)]

        if not candidates:
            return None

        # Refresh neu data cu
        for s in candidates:
            if now - s.last_check > self.REFRESH_INTERVAL:
                self.refresh_status(s)

        # Chon server co tong queue nho nhat (server queue + local pending)
        with self._lock:
            candidates = [s for s in self.servers if self._is_available(s)]
            if not candidates:
                return None
            # v1.0.541: Sort uu tien: (1) queue nho, (2) it timeout, (3) phan hoi nhanh
            candidates.sort(key=lambda s: (
                s.queue_size + s.local_pending,       # Uu tien queue nho
                s.recent_timeout_count,               # Uu tien server it timeout
                s.task_fail_count,                     # Uu tien server it fail
                s.last_response_time,                  # Uu tien server phan hoi nhanh
            ))
            best = candidates[0]
            if auto_reserve:
                best.local_pending += 1

        return best

    def release_server(self, server: ServerInfo):
        """Giam local_pending sau khi task xong (success hoac fail)."""
        with self._lock:
            server.local_pending = max(0, server.local_pending - 1)

    def wait_for_server(self, max_wait: int = 300, log_interval: int = 30) -> Optional[ServerInfo]:
        """
        Cho den khi co server kha dung. Queue-based: server se san sang sau cooldown.

        Args:
            max_wait: Thoi gian cho toi da (seconds). Default 300s = 5 phut.
            log_interval: Log moi X giay.

        Returns:
            ServerInfo neu tim thay, None neu het thoi gian.
        """
        start = time.time()
        last_log = 0
        attempt = 0

        while time.time() - start < max_wait:
            # Thu pick server
            server = self.pick_best_server()
            if server:
                if attempt > 0:
                    elapsed = int(time.time() - start)
                    self._log(f"Server available sau {elapsed}s cho: {server.name} (q={server.queue_size})")
                return server

            # Chua co → cho va thu lai
            attempt += 1
            elapsed = int(time.time() - start)

            # Log dinh ky
            if elapsed - last_log >= log_interval:
                remaining = max_wait - elapsed
                # Tinh thoi gian cooldown con lai cua server gan nhat
                nearest_cooldown = self._nearest_cooldown()
                if nearest_cooldown > 0:
                    self._log(f"Cho server... ({elapsed}s/{max_wait}s, server gan nhat san sang trong ~{nearest_cooldown}s)")
                else:
                    self._log(f"Cho server... ({elapsed}s/{max_wait}s)")
                last_log = elapsed

            # Refresh de check lai
            self.refresh_all()

            # Cho 10s roi thu lai (dam bao khong am)
            remaining = max_wait - (time.time() - start)
            if remaining <= 0:
                break
            time.sleep(min(10, remaining))

        self._log(f"Het thoi gian cho server ({max_wait}s)", "warn")
        return None

    def _nearest_cooldown(self) -> int:
        """Tra ve so giay con lai cua server co cooldown gan het nhat."""
        now = time.time()
        nearest = float('inf')
        with self._lock:
            for s in self.servers:
                if s.enabled and s.connect_fail_count >= self.MAX_CONNECT_FAIL:
                    remaining = self.CONNECT_COOLDOWN - (now - s.last_connect_fail_time)
                    if 0 < remaining < nearest:
                        nearest = remaining
        return int(nearest) if nearest < float('inf') else 0

    def mark_success(self, server: ServerInfo):
        """Danh dau server thanh cong - reset failures + release slot."""
        with self._lock:
            server.connect_fail_count = 0
            server.task_fail_count = 0
            server.recent_timeout_count = 0  # v1.0.541: Reset timeout khi success
            server.total_completed += 1
            server.local_pending = max(0, server.local_pending - 1)

    def mark_submit_failed(self, server: ServerInfo, error: str = ""):
        """
        Khong submit duoc (connection error) → CO THE disable server.
        Day la loi NGHIEM TRONG - server co the da chet.
        """
        with self._lock:
            server.connect_fail_count += 1
            server.last_connect_fail_time = time.time()
            server.total_failed += 1
            server.local_pending = max(0, server.local_pending - 1)
        if server.connect_fail_count >= self.MAX_CONNECT_FAIL:
            self._log(f"Server {server.name} unreachable ({self.MAX_CONNECT_FAIL} connect fails). "
                       f"Cooldown {self.CONNECT_COOLDOWN}s", "warn")

    def mark_task_failed(self, server: ServerInfo, error: str = ""):
        """
        Task fail (Google API error, timeout, policy) → KHONG disable server.
        Server van hoat dong tot, chi la Google tra loi loi.
        """
        with self._lock:
            server.task_fail_count += 1
            server.total_failed += 1
            server.local_pending = max(0, server.local_pending - 1)
        # KHONG disable - server van OK, chi Google loi thoi

    # Backward compat
    def mark_failed(self, server: ServerInfo, error: str = ""):
        """Backward compat - mac dinh la task_failed (khong disable)."""
        self.mark_task_failed(server, error)

    def get_stats(self) -> Dict[str, Any]:
        """Thong ke tat ca servers."""
        with self._lock:
            return {
                "servers": [
                    {
                        "name": s.name,
                        "url": s.url,
                        "queue_size": s.queue_size,
                        "local_pending": s.local_pending,
                        "chrome_ready": s.chrome_ready,
                        "available": self._is_available(s),
                        "connect_fails": s.connect_fail_count,
                        "task_fails": s.task_fail_count,
                        "fail_count": s.connect_fail_count,  # backward compat
                        "completed": s.total_completed,
                        "failed": s.total_failed,
                    }
                    for s in self.servers
                ],
                "total_available": sum(1 for s in self.servers if self._is_available(s)),
            }
