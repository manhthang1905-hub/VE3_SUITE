"""
Central Logger - Hệ thống log tập trung
========================================
- Tất cả workers ghi log vào 1 file chung
- GUI có thể đọc log realtime
- Mỗi dòng log có: timestamp, worker_id, level, message
"""

import os
import sys
import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional
from collections import deque

# Log file location
TOOL_DIR = Path(__file__).parent.parent
LOG_DIR = TOOL_DIR / "logs"
LOG_FILE = LOG_DIR / "central.log"

# Ensure log directory exists
LOG_DIR.mkdir(exist_ok=True)

# Lock for thread-safe writing
_log_lock = threading.Lock()

# In-memory buffer for GUI (last 500 lines)
_log_buffer = deque(maxlen=500)

# Callbacks for realtime updates
_log_callbacks = []


def log(worker_id: str, message: str, level: str = "INFO"):
    """
    Ghi log từ bất kỳ worker nào.

    Args:
        worker_id: "excel", "chrome_1", "chrome_2", "main", etc.
        message: Nội dung log
        level: "INFO", "WARN", "ERROR", "DEBUG"
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{worker_id:10}] [{level:5}] {message}"

    with _log_lock:
        # Write to file
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            print(f"Log write error: {e}")

        # Add to buffer
        _log_buffer.append(line)

        # Notify callbacks
        for callback in _log_callbacks:
            try:
                callback(line)
            except:
                pass

    # Also print to console
    print(line)


def get_recent_logs(count: int = 100) -> list:
    """Lấy N dòng log gần nhất."""
    with _log_lock:
        return list(_log_buffer)[-count:]


def get_all_logs() -> list:
    """Đọc toàn bộ log từ file."""
    if not LOG_FILE.exists():
        return []

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return f.readlines()
    except:
        return []


def clear_logs():
    """Xóa log file."""
    with _log_lock:
        _log_buffer.clear()
        try:
            if LOG_FILE.exists():
                LOG_FILE.unlink()
        except:
            pass


def add_callback(callback: Callable[[str], None]):
    """Thêm callback để nhận log realtime."""
    _log_callbacks.append(callback)


def remove_callback(callback: Callable[[str], None]):
    """Xóa callback."""
    if callback in _log_callbacks:
        _log_callbacks.remove(callback)


def tail_log(count: int = 50) -> str:
    """Đọc N dòng cuối của log file (như tail -n)."""
    if not LOG_FILE.exists():
        return "No log file yet."

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return "".join(lines[-count:])
    except Exception as e:
        return f"Error reading log: {e}"


# ============================================================
# Logger class để dùng trong các module
# ============================================================

class WorkerLogger:
    """Logger instance cho 1 worker cụ thể."""

    def __init__(self, worker_id: str):
        self.worker_id = worker_id

    def info(self, message: str):
        log(self.worker_id, message, "INFO")

    def warn(self, message: str):
        log(self.worker_id, message, "WARN")

    def error(self, message: str):
        log(self.worker_id, message, "ERROR")

    def debug(self, message: str):
        log(self.worker_id, message, "DEBUG")


def get_logger(worker_id: str) -> WorkerLogger:
    """Tạo logger cho worker."""
    return WorkerLogger(worker_id)


# ============================================================
# Test
# ============================================================

if __name__ == "__main__":
    # Test logging
    logger = get_logger("test")
    logger.info("This is info")
    logger.warn("This is warning")
    logger.error("This is error")

    print("\n--- Recent logs ---")
    for line in get_recent_logs(10):
        print(line)
