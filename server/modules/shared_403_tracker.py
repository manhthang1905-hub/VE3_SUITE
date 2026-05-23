#!/usr/bin/env python3
"""
Shared 403 Tracker - Quản lý trạng thái 403 giữa các Chrome workers.

Logic:
- Mỗi worker khi gặp 403 và đã xóa data xong → đánh dấu "ready_for_ipv6_rotation"
- Chỉ rotate IPv6 khi TẤT CẢ workers đều ready
- Sau khi rotate, reset tất cả flags

Dùng file lock để tránh race condition giữa các process.
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


import json
import time
import sys
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime

# fcntl chỉ có trên Linux, Windows dùng msvcrt
if sys.platform == 'win32':
    import msvcrt
    def lock_file(f, exclusive=True):
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK, 1)
    def unlock_file(f):
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except:
            pass
else:
    import fcntl
    def lock_file(f, exclusive=True):
        fcntl.flock(f, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    def unlock_file(f):
        fcntl.flock(f, fcntl.LOCK_UN)


class Shared403Tracker:
    """Track 403 state across multiple Chrome workers."""

    def __init__(self, state_file: str = None, total_workers: int = 2):
        """
        Initialize tracker.

        Args:
            state_file: Path to shared state file
            total_workers: Number of Chrome workers (default: 2)
        """
        if state_file:
            self.state_file = Path(state_file)
        else:
            self.state_file = Path(__file__).parent.parent / "config" / ".403_tracker.json"

        self.total_workers = total_workers
        self._ensure_state_file()

    def _ensure_state_file(self):
        """Create state file if not exists."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_file.exists():
            self._write_state(self._default_state())

    def _default_state(self) -> Dict:
        """Default state structure."""
        return {
            "workers": {},  # worker_id -> {"ready_for_rotation": bool, "cleared_data": bool, "403_count": int}
            "last_ipv6_rotation": None,
            "total_workers": self.total_workers
        }

    def _read_state(self) -> Dict:
        """Read state with file lock."""
        try:
            with open(self.state_file, 'r') as f:
                try:
                    lock_file(f, exclusive=False)
                    state = json.load(f)
                except json.JSONDecodeError:
                    state = self._default_state()
                except:
                    state = self._default_state()
                finally:
                    unlock_file(f)
            return state
        except FileNotFoundError:
            return self._default_state()
        except:
            return self._default_state()

    def _write_state(self, state: Dict):
        """Write state with file lock."""
        try:
            with open(self.state_file, 'w') as f:
                try:
                    lock_file(f, exclusive=True)
                    json.dump(state, f, indent=2, default=str)
                finally:
                    unlock_file(f)
        except:
            pass  # Ignore lock errors on Windows

    def mark_403(self, worker_id: int) -> Dict:
        """
        Mark that a worker encountered 403.

        Args:
            worker_id: Worker ID (0, 1, 2, ...)

        Returns:
            Current worker state
        """
        state = self._read_state()
        worker_key = str(worker_id)

        if worker_key not in state["workers"]:
            state["workers"][worker_key] = {
                "ready_for_rotation": False,
                "cleared_data": False,
                "403_count": 0,
                "last_403": None
            }

        state["workers"][worker_key]["403_count"] += 1
        state["workers"][worker_key]["last_403"] = datetime.now().isoformat()

        self._write_state(state)
        return state["workers"][worker_key]

    def mark_cleared_data(self, worker_id: int):
        """
        Mark that a worker has cleared Chrome data.

        Args:
            worker_id: Worker ID
        """
        state = self._read_state()
        worker_key = str(worker_id)

        if worker_key not in state["workers"]:
            state["workers"][worker_key] = {
                "ready_for_rotation": False,
                "cleared_data": False,
                "403_count": 0,
                "last_403": None
            }

        state["workers"][worker_key]["cleared_data"] = True
        self._write_state(state)

    def mark_ready_for_rotation(self, worker_id: int):
        """
        Mark that a worker is ready for IPv6 rotation.
        Called when: cleared data + still getting 403.

        Args:
            worker_id: Worker ID
        """
        state = self._read_state()
        worker_key = str(worker_id)

        if worker_key not in state["workers"]:
            state["workers"][worker_key] = {
                "ready_for_rotation": False,
                "cleared_data": False,
                "403_count": 0,
                "last_403": None
            }

        state["workers"][worker_key]["ready_for_rotation"] = True
        state["workers"][worker_key]["ready_at"] = datetime.now().isoformat()
        self._write_state(state)

    def should_rotate_ipv6(self, worker_id: int) -> bool:
        """
        Check if IPv6 should be rotated.
        Only returns True if ALL workers are ready for rotation.

        Args:
            worker_id: Calling worker ID (for logging)

        Returns:
            True if all workers are ready and should rotate
        """
        state = self._read_state()

        # Count how many workers are ready for rotation
        ready_count = 0
        total_tracked = len(state["workers"])

        for wid, wstate in state["workers"].items():
            if wstate.get("ready_for_rotation", False):
                ready_count += 1

        # Need at least total_workers tracked and ALL ready
        # Hoặc nếu chỉ có 1 worker (single mode) thì cũng OK
        if self.total_workers == 1:
            return ready_count >= 1

        # Dual mode: cần CẢ 2 workers đều ready
        return ready_count >= self.total_workers

    def reset_after_rotation(self):
        """Reset all worker states after IPv6 rotation."""
        state = self._read_state()

        for worker_key in state["workers"]:
            state["workers"][worker_key] = {
                "ready_for_rotation": False,
                "cleared_data": False,
                "403_count": 0,
                "last_403": None
            }

        state["last_ipv6_rotation"] = datetime.now().isoformat()
        self._write_state(state)

    def reset_worker(self, worker_id: int):
        """Reset a specific worker's state (after success)."""
        state = self._read_state()
        worker_key = str(worker_id)

        if worker_key in state["workers"]:
            state["workers"][worker_key] = {
                "ready_for_rotation": False,
                "cleared_data": False,
                "403_count": 0,
                "last_403": None
            }
            self._write_state(state)

    def get_status(self) -> Dict:
        """Get current status for debugging."""
        return self._read_state()

    def wait_for_rotation_or_timeout(self, worker_id: int, timeout: int = 60) -> bool:
        """
        Wait until all workers are ready for rotation, or timeout.

        Args:
            worker_id: Calling worker ID
            timeout: Max wait time in seconds

        Returns:
            True if rotation should proceed, False if timeout
        """
        start = time.time()
        while time.time() - start < timeout:
            if self.should_rotate_ipv6(worker_id):
                return True
            time.sleep(2)
        return False


# Global instance - shared across all modules
_tracker: Optional[Shared403Tracker] = None


def get_403_tracker(total_workers: int = 2) -> Shared403Tracker:
    """Get or create shared 403 tracker."""
    global _tracker
    if _tracker is None:
        _tracker = Shared403Tracker(total_workers=total_workers)
    return _tracker


def reset_403_tracker():
    """Reset global tracker instance."""
    global _tracker
    _tracker = None
