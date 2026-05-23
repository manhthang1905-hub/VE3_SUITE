"""
Chrome Manager - Giám sát và điều khiển các Chrome workers.

Chức năng:
1. Theo dõi trạng thái của các Chrome (healthy, error, restarting)
2. Tự động restart Chrome bị lỗi (không cần kill tất cả)
3. Cung cấp API để check/restart Chrome từ flow chính

Usage:
    manager = get_chrome_manager()
    manager.register_chrome(worker_id=0, drission_api=api1)
    manager.register_chrome(worker_id=1, drission_api=api2)

    # Khi phát hiện lỗi
    manager.mark_error(worker_id=0, error="403 Forbidden")

    # Manager tự động restart
    manager.check_and_restart_failed()
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


import time
import threading
from typing import Dict, Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ChromeRestartExhaustedException(Exception):
    """Raised when Chrome has been restarted too many times and should stop."""
    pass


class ChromeStatus(Enum):
    """Trạng thái của Chrome worker."""
    IDLE = "idle"           # Chưa khởi động
    RUNNING = "running"     # Đang chạy bình thường
    ERROR = "error"         # Bị lỗi, cần restart
    RESTARTING = "restarting"  # Đang restart
    STOPPED = "stopped"     # Đã dừng
    EXHAUSTED = "exhausted"  # Đã restart quá nhiều lần, cần dừng hoàn toàn


@dataclass
class ChromeWorker:
    """Thông tin về một Chrome worker."""
    worker_id: int
    drission_api: Any = None  # DrissionFlowAPI instance
    status: ChromeStatus = ChromeStatus.IDLE
    last_error: str = ""
    error_count: int = 0
    restart_count: int = 0
    last_success_time: float = 0
    project_url: str = ""

    # Callbacks
    on_restart: Optional[Callable] = None


class ChromeManager:
    """
    Manager giám sát và điều khiển các Chrome workers.
    Singleton pattern - chỉ có 1 instance.
    """

    _instance = None
    _lock = threading.Lock()

    MAX_RESTARTS_PER_WORKER = 5  # Tối đa 5 lần restart mỗi worker
    MAX_CONSECUTIVE_ERRORS = 3   # 3 lỗi liên tiếp → restart

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.workers: Dict[int, ChromeWorker] = {}
        self.log_callback: Optional[Callable] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor = False
        self._initialized = True

    def log(self, msg: str, level: str = "INFO"):
        """Log message."""
        prefix = f"[ChromeManager] [{level}]"
        full_msg = f"{prefix} {msg}"
        if self.log_callback:
            self.log_callback(full_msg)
        else:
            print(full_msg)

    def set_log_callback(self, callback: Callable):
        """Set callback để log."""
        self.log_callback = callback

    def register_chrome(
        self,
        worker_id: int,
        drission_api: Any,
        project_url: str = "",
        on_restart: Optional[Callable] = None
    ):
        """
        Đăng ký một Chrome worker để quản lý.

        Args:
            worker_id: ID của worker (0, 1, 2, ...)
            drission_api: DrissionFlowAPI instance
            project_url: URL project để vào lại khi restart
            on_restart: Callback khi restart xong
        """
        worker = ChromeWorker(
            worker_id=worker_id,
            drission_api=drission_api,
            status=ChromeStatus.RUNNING,
            project_url=project_url,
            on_restart=on_restart,
            last_success_time=time.time()
        )
        self.workers[worker_id] = worker
        self.log(f"Registered Chrome worker {worker_id}")

    def unregister_chrome(self, worker_id: int):
        """Hủy đăng ký Chrome worker."""
        if worker_id in self.workers:
            del self.workers[worker_id]
            self.log(f"Unregistered Chrome worker {worker_id}")

    def mark_success(self, worker_id: int):
        """Đánh dấu worker vừa thành công."""
        if worker_id in self.workers:
            worker = self.workers[worker_id]
            worker.status = ChromeStatus.RUNNING
            worker.error_count = 0  # Reset error count
            worker.last_success_time = time.time()

    def mark_error(self, worker_id: int, error: str = ""):
        """
        Đánh dấu worker bị lỗi.

        Args:
            worker_id: ID của worker
            error: Mô tả lỗi
        """
        if worker_id not in self.workers:
            return

        worker = self.workers[worker_id]
        worker.error_count += 1
        worker.last_error = error

        self.log(f"Chrome {worker_id} error ({worker.error_count}): {error}", "WARN")

        # Nếu quá nhiều lỗi liên tiếp → đánh dấu cần restart
        if worker.error_count >= self.MAX_CONSECUTIVE_ERRORS:
            worker.status = ChromeStatus.ERROR
            self.log(f"Chrome {worker_id} marked for restart (too many errors)", "WARN")

    def get_status(self, worker_id: int) -> Optional[ChromeStatus]:
        """Lấy trạng thái của worker."""
        if worker_id in self.workers:
            return self.workers[worker_id].status
        return None

    def is_healthy(self, worker_id: int) -> bool:
        """Kiểm tra worker có đang healthy không."""
        status = self.get_status(worker_id)
        return status in (ChromeStatus.RUNNING, ChromeStatus.IDLE)

    def restart_chrome(self, worker_id: int) -> bool:
        """
        Restart một Chrome worker cụ thể.

        Args:
            worker_id: ID của worker cần restart

        Returns:
            True nếu restart thành công
        """
        if worker_id not in self.workers:
            self.log(f"Worker {worker_id} not found", "ERROR")
            return False

        worker = self.workers[worker_id]

        # Check giới hạn restart
        if worker.restart_count >= self.MAX_RESTARTS_PER_WORKER:
            worker.status = ChromeStatus.EXHAUSTED
            self.log(f"Chrome {worker_id} đã restart {worker.restart_count} lần - DỪNG HOÀN TOÀN!", "ERROR")
            raise ChromeRestartExhaustedException(
                f"Chrome {worker_id} đã restart {self.MAX_RESTARTS_PER_WORKER} lần. "
                f"Worker cần dừng hoàn toàn và khởi động lại."
            )

        worker.status = ChromeStatus.RESTARTING
        worker.restart_count += 1

        self.log(f"[SYNC] Restarting Chrome {worker_id} (lần {worker.restart_count})...")

        try:
            api = worker.drission_api
            if not api:
                self.log(f"Chrome {worker_id} không có API instance", "ERROR")
                return False

            # 1. Đóng Chrome hiện tại
            self.log(f"   → Đóng Chrome {worker_id}...")
            try:
                api.close()
            except:
                pass
            time.sleep(2)

            # 2. Mở lại Chrome
            self.log(f"   → Mở lại Chrome {worker_id}...")
            project_url = worker.project_url or getattr(api, '_current_project_url', None)

            if api.setup(project_url=project_url):
                # v1.0.163: Reset model flag để chọn lại khi generate
                if hasattr(api, '_model_selected'):
                    api._model_selected = False
                    self.log(f"   → Model flag reset")

                worker.status = ChromeStatus.RUNNING
                worker.error_count = 0
                worker.last_success_time = time.time()

                self.log(f"   [v] Chrome {worker_id} restarted thành công!")

                # Gọi callback nếu có
                if worker.on_restart:
                    try:
                        worker.on_restart(worker_id)
                    except:
                        pass

                return True
            else:
                self.log(f"   [x] Chrome {worker_id} restart thất bại", "ERROR")
                worker.status = ChromeStatus.ERROR
                return False

        except Exception as e:
            self.log(f"   [x] Restart error: {e}", "ERROR")
            worker.status = ChromeStatus.ERROR
            return False

    def check_and_restart_failed(self) -> int:
        """
        Kiểm tra và restart tất cả Chrome bị lỗi.

        Returns:
            Số lượng Chrome đã restart thành công
        """
        restarted = 0

        for worker_id, worker in list(self.workers.items()):
            if worker.status == ChromeStatus.ERROR:
                if self.restart_chrome(worker_id):
                    restarted += 1

        return restarted

    def start_monitor(self, check_interval: int = 30):
        """
        Bắt đầu thread monitor tự động kiểm tra và restart.

        Args:
            check_interval: Khoảng thời gian giữa các lần check (giây)
        """
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        self._stop_monitor = False

        def monitor_loop():
            while not self._stop_monitor:
                try:
                    restarted = self.check_and_restart_failed()
                    if restarted > 0:
                        self.log(f"Auto-restarted {restarted} Chrome(s)")
                except Exception as e:
                    self.log(f"Monitor error: {e}", "ERROR")

                # Sleep với check stop flag
                for _ in range(check_interval):
                    if self._stop_monitor:
                        break
                    time.sleep(1)

        self._monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitor_thread.start()
        self.log("Monitor thread started")

    def stop_monitor(self):
        """Dừng monitor thread."""
        self._stop_monitor = True
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        self.log("Monitor thread stopped")

    def get_summary(self) -> Dict:
        """Lấy tóm tắt trạng thái tất cả workers."""
        summary = {
            "total": len(self.workers),
            "running": 0,
            "error": 0,
            "restarting": 0,
            "workers": {}
        }

        for worker_id, worker in self.workers.items():
            summary["workers"][worker_id] = {
                "status": worker.status.value,
                "error_count": worker.error_count,
                "restart_count": worker.restart_count,
                "last_error": worker.last_error
            }

            if worker.status == ChromeStatus.RUNNING:
                summary["running"] += 1
            elif worker.status == ChromeStatus.ERROR:
                summary["error"] += 1
            elif worker.status == ChromeStatus.RESTARTING:
                summary["restarting"] += 1

        return summary

    def close_all(self):
        """Đóng tất cả Chrome workers."""
        self.stop_monitor()

        for worker_id, worker in list(self.workers.items()):
            try:
                if worker.drission_api:
                    worker.drission_api.close()
                worker.status = ChromeStatus.STOPPED
            except:
                pass

        self.workers.clear()
        self.log("All Chrome workers closed")

    def reset(self):
        """Reset manager về trạng thái ban đầu."""
        self.close_all()
        self._initialized = False


# Singleton accessor
_manager_instance: Optional[ChromeManager] = None


def get_chrome_manager() -> ChromeManager:
    """Lấy ChromeManager instance (singleton)."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = ChromeManager()
    return _manager_instance


def reset_chrome_manager():
    """Reset ChromeManager."""
    global _manager_instance
    if _manager_instance:
        _manager_instance.reset()
    _manager_instance = None
