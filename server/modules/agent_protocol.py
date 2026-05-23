#!/usr/bin/env python3
"""
Agent Protocol - Giao thức giao tiếp giữa VM Manager và Workers.

=============================================================
  AGENT PROTOCOL - Communication between Manager & Workers
=============================================================

Directories:
    .agent/tasks/       Manager ghi task cho worker đọc
    .agent/results/     Worker ghi kết quả cho Manager đọc
    .agent/status/      Worker cập nhật trạng thái (mỗi 5s)
    .agent/logs/        Worker ghi log chi tiết

Usage in Worker:
    from modules.agent_protocol import AgentWorker

    agent = AgentWorker("chrome_1")
    agent.log("Starting...")
    agent.update_status("working", progress=50, current_task="image_KA2-0001")
    agent.report_result(task_id, success=True, details={...})

Usage in Manager:
    from modules.agent_protocol import AgentManager

    manager = AgentManager()
    status = manager.get_worker_status("chrome_1")
    results = manager.collect_results()
    errors = manager.get_recent_errors("chrome_1")
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
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, asdict
from enum import Enum

# ================================================================================
# PATHS
# ================================================================================

TOOL_DIR = Path(__file__).parent.parent
AGENT_DIR = TOOL_DIR / ".agent"
TASKS_DIR = AGENT_DIR / "tasks"
RESULTS_DIR = AGENT_DIR / "results"
STATUS_DIR = AGENT_DIR / "status"
LOGS_DIR = AGENT_DIR / "logs"


def ensure_dirs():
    """Tạo các thư mục cần thiết."""
    for d in [AGENT_DIR, TASKS_DIR, RESULTS_DIR, STATUS_DIR, LOGS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


# ================================================================================
# DATA STRUCTURES
# ================================================================================

class WorkerState(Enum):
    IDLE = "idle"
    WORKING = "working"
    ERROR = "error"
    STOPPED = "stopped"


class ErrorType(Enum):
    CHROME_CRASH = "chrome_crash"           # Chrome không mở được
    CHROME_403 = "chrome_403"               # bị block bởi reCAPTCHA
    API_RATE_LIMIT = "api_rate_limit"       # API quá giới hạn
    API_ERROR = "api_error"                 # Lỗi API khác
    NETWORK_ERROR = "network_error"         # Lỗi mạng
    FILE_NOT_FOUND = "file_not_found"       # File không tìm thấy
    EXCEL_ERROR = "excel_error"             # Lỗi đọc/ghi Excel
    UNKNOWN = "unknown"                     # Lỗi không xác định


@dataclass
class WorkerStatus:
    """Trạng thái của worker."""
    worker_id: str
    state: str = "idle"
    progress: int = 0  # 0-100
    current_project: str = ""
    current_task: str = ""
    current_scene: int = 0
    total_scenes: int = 0
    current_step: int = 0  # For Excel Worker: step 1-7
    step_name: str = ""    # For Excel Worker: step name
    completed_count: int = 0
    failed_count: int = 0
    last_error: str = ""
    last_error_type: str = ""
    last_update: str = ""
    uptime_seconds: int = 0
    project_elapsed_seconds: int = 0  # v1.0.65: Time spent on current project
    # v1.0.47: Track specific scenes
    scenes_completed: List[int] = None  # List of completed scene IDs
    scenes_failed: List[int] = None     # List of failed scene IDs
    last_action: str = ""               # Last action description for GUI

    def __post_init__(self):
        if self.scenes_completed is None:
            self.scenes_completed = []
        if self.scenes_failed is None:
            self.scenes_failed = []

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> 'WorkerStatus':
        # Handle missing fields for backwards compatibility
        if 'scenes_completed' not in d:
            d['scenes_completed'] = []
        if 'scenes_failed' not in d:
            d['scenes_failed'] = []
        if 'last_action' not in d:
            d['last_action'] = ''
        if 'project_elapsed_seconds' not in d:
            d['project_elapsed_seconds'] = 0
        return cls(**d)


@dataclass
class TaskResult:
    """Kết quả của một task."""
    task_id: str
    worker_id: str
    success: bool
    project_code: str = ""
    task_type: str = ""  # "excel", "image", "video"
    scenes_completed: List[int] = None
    scenes_failed: List[int] = None
    error: str = ""
    error_type: str = ""
    duration_seconds: float = 0
    timestamp: str = ""
    details: Dict = None

    def __post_init__(self):
        if self.scenes_completed is None:
            self.scenes_completed = []
        if self.scenes_failed is None:
            self.scenes_failed = []
        if self.details is None:
            self.details = {}
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        return asdict(self)


# ================================================================================
# ERROR DETECTION
# ================================================================================

# Patterns để detect loại lỗi từ message
ERROR_PATTERNS = {
    ErrorType.CHROME_CRASH: [
        r"Chrome attempt \d+/\d+ failed",
        r"The browser connection fails",
        r"[x] Chrome error",
        r"[x] Không restart được Chrome",
    ],
    ErrorType.CHROME_403: [
        r"403.*reCAPTCHA",
        r"reCAPTCHA evaluation failed",
        r"403 Forbidden",
    ],
    ErrorType.API_RATE_LIMIT: [
        r"rate limit",
        r"too many requests",
        r"429",
    ],
    ErrorType.API_ERROR: [
        r"API error",
        r"API Error",
        r"DeepSeek.*error",
        r"Groq.*error",
    ],
    ErrorType.NETWORK_ERROR: [
        r"Network error",
        r"Connection refused",
        r"Timeout",
        r"getaddrinfo failed",
    ],
    ErrorType.FILE_NOT_FOUND: [
        r"File not found",
        r"No such file",
        r"FileNotFoundError",
    ],
    ErrorType.EXCEL_ERROR: [
        r"Excel.*error",
        r"openpyxl.*error",
        r"PermissionError.*xlsx",
    ],
}


def detect_error_type(message: str) -> ErrorType:
    """Detect loại lỗi từ message."""
    import re
    for error_type, patterns in ERROR_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, message, re.IGNORECASE):
                return error_type
    return ErrorType.UNKNOWN


# ================================================================================
# AGENT WORKER - Dùng trong các py con
# ================================================================================

class AgentWorker:
    """
    Agent cho Worker - giao tiếp với Manager.

    Dùng trong các script như _run_chrome1.py, run_excel_api.py
    """

    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.start_time = datetime.now()
        self.project_start_time = None  # v1.0.65: Track project start time
        self._status = WorkerStatus(worker_id=worker_id)
        self._log_file = None
        self._status_thread = None
        self._stop_flag = False

        ensure_dirs()
        self._init_log_file()

    def _init_log_file(self):
        """Khởi tạo log file."""
        log_path = LOGS_DIR / f"{self.worker_id}.log"
        self._log_file = open(log_path, 'a', encoding='utf-8')

    def log(self, message: str, level: str = "INFO"):
        """Ghi log."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{level}] {message}\n"

        # Ghi vào file
        if self._log_file:
            self._log_file.write(line)
            self._log_file.flush()

        # In ra console
        print(f"[{self.worker_id}] {line.strip()}")

    def log_error(self, message: str, error_type: ErrorType = None):
        """Ghi log lỗi."""
        if error_type is None:
            error_type = detect_error_type(message)

        self._status.last_error = message
        self._status.last_error_type = error_type.value
        self.log(message, "ERROR")
        self._save_status()

    def update_status(
        self,
        state: str = None,
        progress: int = None,
        current_project: str = None,
        current_task: str = None,
        current_scene: int = None,
        total_scenes: int = None,
        current_step: int = None,
        step_name: str = None,
        scene_completed: int = None,  # v1.0.47: Mark single scene as completed
        scene_failed: int = None,     # v1.0.47: Mark single scene as failed
        last_action: str = None,      # v1.0.47: Last action description
        clear_scenes: bool = False,   # v1.0.47: Clear scene lists (for new project)
    ):
        """Cập nhật trạng thái."""
        if state:
            self._status.state = state
        if progress is not None:
            self._status.progress = progress
        if current_project is not None:
            # v1.0.65: Track project start time
            if current_project != self._status.current_project:
                if current_project:  # New project started
                    self.project_start_time = datetime.now()
                else:  # Project cleared
                    self.project_start_time = None
            self._status.current_project = current_project
        if current_task:
            self._status.current_task = current_task
        if current_scene is not None:
            self._status.current_scene = current_scene
        if total_scenes is not None:
            self._status.total_scenes = total_scenes
        if current_step is not None:
            self._status.current_step = current_step
        if step_name is not None:
            self._status.step_name = step_name
        if last_action is not None:
            self._status.last_action = last_action

        # v1.0.47: Track completed/failed scenes
        if clear_scenes:
            self._status.scenes_completed = []
            self._status.scenes_failed = []
        if scene_completed is not None and scene_completed not in self._status.scenes_completed:
            self._status.scenes_completed.append(scene_completed)
            self._status.completed_count = len(self._status.scenes_completed)
        if scene_failed is not None and scene_failed not in self._status.scenes_failed:
            self._status.scenes_failed.append(scene_failed)
            self._status.failed_count = len(self._status.scenes_failed)

        self._save_status()

    def _save_status(self):
        """Lưu status vào file."""
        self._status.last_update = datetime.now().isoformat()
        self._status.uptime_seconds = int((datetime.now() - self.start_time).total_seconds())

        # v1.0.65: Calculate project elapsed time
        if self.project_start_time:
            self._status.project_elapsed_seconds = int((datetime.now() - self.project_start_time).total_seconds())
        else:
            self._status.project_elapsed_seconds = 0

        status_path = STATUS_DIR / f"{self.worker_id}.json"
        try:
            with open(status_path, 'w', encoding='utf-8') as f:
                json.dump(self._status.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving status: {e}")

    def report_success(
        self,
        task_id: str,
        project_code: str,
        task_type: str,
        scenes_completed: List[int] = None,
        duration: float = 0,
        details: Dict = None
    ):
        """Báo cáo task thành công."""
        self._status.completed_count += 1
        result = TaskResult(
            task_id=task_id,
            worker_id=self.worker_id,
            success=True,
            project_code=project_code,
            task_type=task_type,
            scenes_completed=scenes_completed or [],
            duration_seconds=duration,
            details=details or {}
        )
        self._save_result(result)
        self.log(f"Task completed: {task_id}")

    def report_failure(
        self,
        task_id: str,
        project_code: str,
        task_type: str,
        error: str,
        scenes_failed: List[int] = None,
        duration: float = 0,
        details: Dict = None
    ):
        """Báo cáo task thất bại."""
        self._status.failed_count += 1
        error_type = detect_error_type(error)

        result = TaskResult(
            task_id=task_id,
            worker_id=self.worker_id,
            success=False,
            project_code=project_code,
            task_type=task_type,
            scenes_failed=scenes_failed or [],
            error=error,
            error_type=error_type.value,
            duration_seconds=duration,
            details=details or {}
        )
        self._save_result(result)
        self.log_error(f"Task failed: {task_id} - {error}", error_type)

    def _save_result(self, result: TaskResult):
        """Lưu kết quả vào file."""
        result_path = RESULTS_DIR / f"{result.task_id}.json"
        try:
            with open(result_path, 'w', encoding='utf-8') as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving result: {e}")

    def get_task(self) -> Optional[Dict]:
        """Đọc task được giao (nếu có)."""
        task_path = TASKS_DIR / f"{self.worker_id}.json"
        if not task_path.exists():
            return None

        try:
            with open(task_path, 'r', encoding='utf-8') as f:
                task = json.load(f)
            # Xóa file sau khi đọc
            task_path.unlink()
            return task
        except Exception as e:
            print(f"Error reading task: {e}")
            return None

    def start_status_updater(self, interval: int = 5):
        """Bắt đầu thread cập nhật status định kỳ."""
        def updater():
            while not self._stop_flag:
                self._save_status()
                time.sleep(interval)

        self._status_thread = threading.Thread(target=updater, daemon=True)
        self._status_thread.start()

    def close(self):
        """Đóng agent."""
        self._stop_flag = True
        self._status.state = "stopped"
        self._save_status()

        if self._log_file:
            self._log_file.close()


# ================================================================================
# AGENT MANAGER - Dùng trong VM Manager
# ================================================================================

class AgentManager:
    """
    Agent cho Manager - đọc thông tin từ workers.

    Dùng trong vm_manager.py
    """

    def __init__(self):
        ensure_dirs()

    def get_worker_status(self, worker_id: str) -> Optional[WorkerStatus]:
        """Lấy trạng thái của worker."""
        status_path = STATUS_DIR / f"{worker_id}.json"
        if not status_path.exists():
            return None

        try:
            with open(status_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return WorkerStatus.from_dict(data)
        except Exception:
            return None

    def get_all_worker_status(self) -> Dict[str, WorkerStatus]:
        """Lấy trạng thái của tất cả workers."""
        statuses = {}
        for status_file in STATUS_DIR.glob("*.json"):
            worker_id = status_file.stem
            status = self.get_worker_status(worker_id)
            if status:
                statuses[worker_id] = status
        return statuses

    def collect_results(self) -> List[TaskResult]:
        """Thu thập tất cả kết quả từ workers."""
        results = []
        for result_file in RESULTS_DIR.glob("*.json"):
            try:
                with open(result_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Xử lý fields có thể None
                if data.get('scenes_completed') is None:
                    data['scenes_completed'] = []
                if data.get('scenes_failed') is None:
                    data['scenes_failed'] = []
                if data.get('details') is None:
                    data['details'] = {}

                result = TaskResult(**data)
                results.append(result)

                # Xóa file sau khi đọc
                result_file.unlink()
            except Exception as e:
                print(f"Error reading result {result_file}: {e}")

        return results

    def get_recent_logs(self, worker_id: str, lines: int = 50) -> List[str]:
        """Lấy log gần nhất của worker."""
        log_path = LOGS_DIR / f"{worker_id}.log"
        if not log_path.exists():
            return []

        try:
            with open(log_path, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
            return all_lines[-lines:]
        except Exception:
            return []

    def get_recent_errors(self, worker_id: str, lines: int = 10) -> List[str]:
        """Lấy các lỗi gần nhất của worker."""
        logs = self.get_recent_logs(worker_id, lines=200)
        errors = [l for l in logs if "[ERROR]" in l]
        return errors[-lines:]

    def send_task(self, worker_id: str, task: Dict) -> bool:
        """Gửi task cho worker."""
        task_path = TASKS_DIR / f"{worker_id}.json"
        try:
            with open(task_path, 'w', encoding='utf-8') as f:
                json.dump(task, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"Error sending task: {e}")
            return False

    def clear_logs(self, worker_id: str = None):
        """Xóa log files."""
        if worker_id:
            log_path = LOGS_DIR / f"{worker_id}.log"
            if log_path.exists():
                log_path.unlink()
        else:
            for log_file in LOGS_DIR.glob("*.log"):
                log_file.unlink()

    def is_worker_alive(self, worker_id: str, timeout_seconds: int = 30) -> bool:
        """Kiểm tra worker có còn sống không (dựa trên last_update)."""
        status = self.get_worker_status(worker_id)
        if not status:
            return False

        try:
            last_update = datetime.fromisoformat(status.last_update)
            age = (datetime.now() - last_update).total_seconds()
            return age < timeout_seconds
        except Exception:
            return False

    def get_error_summary(self) -> Dict[str, int]:
        """Lấy tóm tắt các loại lỗi."""
        summary = {}
        for status_file in STATUS_DIR.glob("*.json"):
            status = self.get_worker_status(status_file.stem)
            if status and status.last_error_type:
                error_type = status.last_error_type
                summary[error_type] = summary.get(error_type, 0) + 1
        return summary


# ================================================================================
# HELPER FUNCTIONS
# ================================================================================

def create_worker_agent(worker_id: str) -> AgentWorker:
    """Tạo agent cho worker."""
    return AgentWorker(worker_id)


def create_manager_agent() -> AgentManager:
    """Tạo agent cho manager."""
    return AgentManager()
