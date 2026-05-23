#!/usr/bin/env python3
"""
VM Manager - AI Agent Orchestrator với Dashboard
=================================================

Hệ thống AI Agent điều phối công việc với giao diện quản lý:
1. Dashboard hiển thị trạng thái toàn bộ hệ thống
2. Quản lý settings (Chrome count, IPv6, Excel mode...)
3. Giám sát và debug lỗi dễ dàng

Usage:
    python vm_manager.py                  # 2 Chrome workers
    python vm_manager.py --chrome 5       # 5 Chrome workers
"""

import subprocess
import sys
import os

# Fix Windows encoding issues
if sys.platform == "win32":
    if sys.stdout:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if sys.stderr:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    os.environ['PYTHONIOENCODING'] = 'utf-8'
import time
import json
import threading
import shutil
import yaml
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List, Set, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
import re

# Import Agent Protocol for worker monitoring
try:
    from modules.agent_protocol import AgentManager as AgentProtocolManager, WorkerStatus as AgentWorkerStatus
    AGENT_PROTOCOL_ENABLED = True
except ImportError:
    AGENT_PROTOCOL_ENABLED = False

# Import IPv6 Manager for rotation
try:
    from modules.ipv6_manager import get_ipv6_manager
    IPV6_MANAGER_ENABLED = True
except ImportError:
    IPV6_MANAGER_ENABLED = False

TOOL_DIR = Path(__file__).parent
AGENT_DIR = TOOL_DIR / ".agent"
TASKS_DIR = AGENT_DIR / "tasks"
RESULTS_DIR = AGENT_DIR / "results"
STATUS_DIR = AGENT_DIR / "status"
LOGS_DIR = AGENT_DIR / "logs"
CONFIG_FILE = TOOL_DIR / "config" / "settings.yaml"

# ================================================================================
# ENUMS & DATA STRUCTURES
# ================================================================================

class TaskType(Enum):
    EXCEL = "excel"
    IMAGE = "image"
    VIDEO = "video"


class TaskStatus(Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRY = "retry"


class WorkerStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    IDLE = "idle"
    WORKING = "working"
    ERROR = "error"


class QualityStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    PARTIAL = "partial"


@dataclass
class Task:
    task_id: str
    task_type: TaskType
    project_code: str
    scenes: List[int] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    assigned_to: Optional[str] = None
    created_at: str = ""
    assigned_at: str = ""
    completed_at: str = ""
    result: Dict = field(default_factory=dict)
    error: str = ""
    retry_count: int = 0
    max_retries: int = 3

    def to_dict(self) -> Dict:
        d = asdict(self)
        d['task_type'] = self.task_type.value
        d['status'] = self.status.value
        return d


@dataclass
class WorkerInfo:
    worker_id: str
    worker_type: str
    worker_num: int = 0
    process: Optional[subprocess.Popen] = None
    status: WorkerStatus = WorkerStatus.STOPPED
    current_task: Optional[str] = None
    start_time: Optional[datetime] = None
    completed_tasks: int = 0
    failed_tasks: int = 0
    last_error: str = ""
    last_restart_time: Optional[datetime] = None  # Để track restart cooldown
    restart_count: int = 0  # Số lần restart trong session


@dataclass
class ProjectStatus:
    """Trạng thái chi tiết của một project."""
    code: str
    srt_exists: bool = False
    excel_exists: bool = False
    excel_status: str = ""  # "none", "fallback", "partial", "complete"
    total_scenes: int = 0
    prompts_count: int = 0
    fallback_prompts: int = 0

    # Chi tiết Excel validation
    srt_scene_count: int = 0  # Số scene trong SRT
    excel_scene_count: int = 0  # Số scene trong Excel
    scenes_mismatch: bool = False  # SRT != Excel

    # Các loại prompts
    img_prompts_count: int = 0  # Số scene có img_prompt
    video_prompts_count: int = 0  # Số scene có video_prompt
    missing_img_prompts: List[int] = field(default_factory=list)  # Scenes thiếu img_prompt
    missing_video_prompts: List[int] = field(default_factory=list)  # Scenes thiếu video_prompt

    # Chi tiết fallback
    fallback_scenes: List[int] = field(default_factory=list)  # Scenes có [FALLBACK]

    # Characters & References (pre-Excel steps)
    characters_count: int = 0  # Số nhân vật trong Excel
    characters_with_ref: int = 0  # Số nhân vật có ảnh tham chiếu trong nv/
    characters_missing_ref: List[str] = field(default_factory=list)  # IDs nhân vật thiếu ảnh
    nv_images_count: int = 0  # Tổng số ảnh trong nv/ folder

    # Pre-Excel workflow steps
    step_srt: str = "pending"  # pending/done/error
    step_characters: str = "pending"  # pending/partial/done
    step_prompts: str = "pending"  # pending/partial/done

    # Video mode & Segment 1 (for BASIC mode)
    video_mode: str = "full"  # "basic" or "full"
    segment1_scenes: List[int] = field(default_factory=list)  # Scene IDs in Segment 1
    segment1_end_srt: int = 0  # Last SRT entry of Segment 1

    # Images & Videos
    images_done: int = 0
    images_missing: List[int] = field(default_factory=list)
    videos_done: int = 0
    videos_missing: List[int] = field(default_factory=list)
    # Videos needed based on mode (basic = only Segment 1, full = all)
    videos_needed: List[int] = field(default_factory=list)
    current_step: str = ""  # "excel", "image", "video", "done"
    errors: List[str] = field(default_factory=list)


# ================================================================================
# SETTINGS MANAGER
# ================================================================================

class SettingsManager:
    """Quản lý settings của hệ thống."""

    def __init__(self):
        self.config = self._load_config()
        # v1.0.674: Auto-fix video_mode bi luu sai "full" khi excel_mode = "small"
        em = self.config.get('excel_mode', '')
        vm = self.config.get('video_mode', '')
        if em == 'small' and vm != 'small':
            self.config['video_mode'] = 'small'
            self.save_config()

    def _load_config(self) -> Dict:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        return {}

    def save_config(self):
        # v1.0.307: Read-modify-write để không ghi đè settings từ GUI
        # GUI lưu deepseek_api_key, gemini_api_keys... vào file
        # Nếu overwrite toàn bộ sẽ mất những thay đổi đó
        file_config = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    file_config = yaml.safe_load(f) or {}
            except:
                pass
        # Merge: file_config làm base, self.config ghi đè
        merged = {**file_config, **self.config}
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            yaml.dump(merged, f, default_flow_style=False, allow_unicode=True)
        self.config = merged

    # Chrome settings
    @property
    def chrome_count(self) -> int:
        parallel = self.config.get('parallel_chrome', '1/2')
        if '/' in str(parallel):
            return int(str(parallel).split('/')[1])
        return int(parallel) if parallel else 2

    @chrome_count.setter
    def chrome_count(self, value: int):
        current = self.config.get('parallel_chrome', '1/2')
        if '/' in str(current):
            worker_num = str(current).split('/')[0]
            self.config['parallel_chrome'] = f"{worker_num}/{value}"
        else:
            self.config['parallel_chrome'] = value
        self.save_config()

    # Excel mode
    @property
    def excel_mode(self) -> str:
        return self.config.get('excel_mode', 'full')  # "full" or "basic"

    @excel_mode.setter
    def excel_mode(self, value: str):
        self.config['excel_mode'] = value
        self.save_config()

    @property
    def video_mode(self) -> str:
        return self.config.get('video_mode', 'full')  # "full" or "basic"

    @video_mode.setter
    def video_mode(self, value: str):
        # Normalize value (remove "(8s)" suffix if present)
        # v1.0.674: Fix "small" bi doi thanh "full" → project xong khong copy
        if "small" in value.lower():
            value = "small"
        elif "basic" in value.lower():
            value = "basic"
        else:
            value = "full"
        self.config['video_mode'] = value
        self.save_config()

    # IPv6 settings
    @property
    def ipv6_enabled(self) -> bool:
        return bool(self.config.get('ipv6_rotation', {}).get('enabled', False))

    @property
    def ipv6_list(self) -> List[str]:
        return self.config.get('ipv6_rotation', {}).get('ips', [])

    @property
    def ipv6_rotate_on_error(self) -> bool:
        return self.config.get('ipv6_rotation', {}).get('rotate_on_403', True)

    # API keys
    @property
    def has_deepseek_key(self) -> bool:
        return bool(self.config.get('deepseek_api_key'))

    @property
    def has_groq_keys(self) -> bool:
        return bool(self.config.get('groq_api_keys'))

    @property
    def has_gemini_keys(self) -> bool:
        return bool(self.config.get('gemini_api_keys'))

    def get_summary(self) -> Dict:
        return {
            'chrome_count': self.chrome_count,
            'excel_mode': self.excel_mode,
            'ipv6_enabled': self.ipv6_enabled,
            'ipv6_count': len(self.ipv6_list),
            'ipv6_rotate_on_error': self.ipv6_rotate_on_error,
            'api_keys': {
                'deepseek': self.has_deepseek_key,
                'groq': self.has_groq_keys,
                'gemini': self.has_gemini_keys,
            }
        }


# ================================================================================
# QUALITY CHECKER
# ================================================================================

class QualityChecker:
    """Kiểm tra chất lượng kết quả."""

    def __init__(self, projects_dir: Path):
        self.projects_dir = projects_dir

    def get_project_status(self, project_code: str) -> ProjectStatus:
        """Lấy trạng thái chi tiết của project."""
        status = ProjectStatus(code=project_code)
        project_dir = self.projects_dir / project_code

        # Check SRT và đếm số scene từ SRT
        srt_path = project_dir / f"{project_code}.srt"
        status.srt_exists = srt_path.exists()

        if status.srt_exists:
            try:
                # Đếm số scene trong SRT (mỗi subtitle block = 1 scene)
                with open(srt_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Đếm số block (mỗi block bắt đầu bằng số)
                    import re
                    blocks = re.findall(r'^\d+\s*$', content, re.MULTILINE)
                    status.srt_scene_count = len(blocks)
            except:
                pass

        # Check Excel
        excel_path = project_dir / f"{project_code}_prompts.xlsx"
        status.excel_exists = excel_path.exists()

        if not status.excel_exists:
            status.excel_status = "none"
            status.current_step = "excel"
            return status

        try:
            from modules.excel_manager import PromptWorkbook
            wb = PromptWorkbook(str(excel_path))
            scenes = wb.get_scenes()

            status.total_scenes = len(scenes)
            status.excel_scene_count = len(scenes)

            # Kiểm tra số scene khớp với SRT
            if status.srt_scene_count > 0 and status.srt_scene_count != status.excel_scene_count:
                status.scenes_mismatch = True

            # Chi tiết từng loại prompt
            for scene in scenes:
                scene_num = scene.scene_id

                # Check img_prompt
                if scene.img_prompt and scene.img_prompt.strip():
                    status.img_prompts_count += 1
                    # Check fallback
                    if "[FALLBACK]" in scene.img_prompt:
                        status.fallback_prompts += 1
                        status.fallback_scenes.append(scene_num)
                else:
                    status.missing_img_prompts.append(scene_num)

                # Check video_prompt
                if scene.video_prompt and scene.video_prompt.strip():
                    status.video_prompts_count += 1
                else:
                    status.missing_video_prompts.append(scene_num)

            status.prompts_count = status.img_prompts_count

            # Check characters from Excel
            try:
                characters = wb.get_characters()
                status.characters_count = len(characters)

                # Check nv/ folder for reference images
                nv_dir = project_dir / "nv"
                if nv_dir.exists():
                    nv_images = list(nv_dir.glob("*.png")) + list(nv_dir.glob("*.jpg")) + list(nv_dir.glob("*.jpeg"))
                    status.nv_images_count = len(nv_images)
                    nv_image_names = {img.stem.lower() for img in nv_images}

                    # Check which characters have reference images
                    for char in characters:
                        char_id = char.id.lower() if char.id else ""
                        if char_id in nv_image_names:
                            status.characters_with_ref += 1
                        elif char.image_file and (nv_dir / char.image_file).exists():
                            status.characters_with_ref += 1
                        else:
                            if char.id:
                                status.characters_missing_ref.append(char.id)
            except:
                pass

            # Set pre-Excel workflow steps
            status.step_srt = "done" if status.srt_exists else "pending"

            if status.characters_count == 0:
                status.step_characters = "pending"
            elif status.characters_with_ref == status.characters_count:
                status.step_characters = "done"
            elif status.characters_with_ref > 0:
                status.step_characters = "partial"
            else:
                status.step_characters = "pending"

            if status.img_prompts_count == 0:
                status.step_prompts = "pending"
            elif status.img_prompts_count == status.total_scenes and status.fallback_prompts == 0:
                status.step_prompts = "done"
            else:
                status.step_prompts = "partial"

            # Excel status - chi tiết hơn
            if status.prompts_count == 0:
                status.excel_status = "empty"
                status.current_step = "excel"
            elif status.scenes_mismatch:
                status.excel_status = "mismatch"  # SRT và Excel không khớp
                status.current_step = "excel"
            elif status.fallback_prompts > 0:
                status.excel_status = "fallback"
                status.current_step = "excel"  # Need API completion
            elif len(status.missing_img_prompts) > 0:
                status.excel_status = "partial"
                status.current_step = "excel"
            else:
                status.excel_status = "complete"
                status.current_step = "image"

            # Check images and videos
            # Images can be in img/ (active) or img_backup/ (moved after video creation)
            # Videos are .mp4 files in img/
            img_dir = project_dir / "img"
            img_backup_dir = project_dir / "img_backup"

            for scene in scenes:
                scene_id = scene.scene_id
                actual_img = img_dir / f"{scene_id}.png"
                backup_img = img_backup_dir / f"{scene_id}.png"
                actual_vid = img_dir / f"{scene_id}.mp4"

                # Check if has image OR video in img/ or img_backup/ OR permanently skipped (.SKIP)
                # v1.0.306: .SKIP = đã thử đủ số lần, không tạo được (content policy)
                # v1.0.447: Đếm cả .mp4 vào images_done (video xong sẽ xóa .png)
                skip_marker = img_dir / f"{scene_id}.SKIP"
                if actual_img.exists() or backup_img.exists() or skip_marker.exists() or actual_vid.exists():
                    status.images_done += 1
                else:
                    status.images_missing.append(scene_id)

                # Check video separately
                if actual_vid.exists():
                    status.videos_done += 1
                else:
                    status.videos_missing.append(scene_id)

            if status.excel_status == "complete":
                if status.images_done == status.total_scenes:
                    status.current_step = "video"
                else:
                    status.current_step = "image"

            # Get video_mode from SettingsManager
            try:
                settings = SettingsManager()
                status.video_mode = settings.video_mode
            except:
                status.video_mode = "full"

            # Get Segment 1 info for BASIC mode
            try:
                segments = wb.get_story_segments()
                if segments:
                    seg1 = segments[0]  # First segment
                    status.segment1_end_srt = seg1.get('srt_range_end', 0)

                    # v1.0.73 FIX: Use image_count to find Segment 1 scenes
                    # Segment 1 scenes are scene_ids 1 through image_count
                    seg1_image_count = seg1.get('image_count', 0)

                    if seg1_image_count > 0:
                        for scene in scenes:
                            if scene.scene_id <= seg1_image_count:
                                status.segment1_scenes.append(scene.scene_id)
            except:
                pass

            # Determine videos_needed based on mode
            # v1.0.84: SMALL mode = CHỈ ẢNH, KHÔNG VIDEO
            if status.video_mode == "small" or "small" in status.video_mode.lower():
                # SMALL mode: KHÔNG tạo video, chỉ tạo ảnh
                status.videos_needed = []  # Không cần video nào
                videos_complete = True  # Coi như video đã xong
            elif status.video_mode == "basic" or "basic" in status.video_mode.lower():
                # BASIC mode: only videos for Segment 1 scenes that have images
                for scene_id in status.segment1_scenes:
                    if scene_id not in status.images_missing:  # Has image
                        if scene_id in status.videos_missing:  # Needs video
                            status.videos_needed.append(scene_id)
                # BASIC: done when all Segment 1 videos are complete
                seg1_videos_done = len([s for s in status.segment1_scenes if s not in status.videos_missing])
                seg1_videos_needed = len(status.segment1_scenes)
                if seg1_videos_needed > 0:
                    videos_complete = (seg1_videos_done >= seg1_videos_needed)
                elif status.total_scenes > 0:
                    videos_complete = False
                else:
                    videos_complete = True
            else:
                # FULL mode: all scenes that have images need videos
                for scene_id in status.videos_missing:
                    if scene_id not in status.images_missing:  # Has image
                        status.videos_needed.append(scene_id)
                # FULL: done when all videos are complete
                videos_complete = (status.videos_done == status.total_scenes)

            # v1.0.76: Only mark as "done" if total_scenes > 0
            # Prevents auto-copy when Excel has no scenes yet
            # v1.0.305: Bo threshold 80% - chi done khi 100% anh xong
            # 80% khien GUI stop Chrome2+Excel som, master chi nhan 80% anh
            # Timeout 6 tieng se xu ly truong hop bi ket that su
            if status.total_scenes > 0:
                completion_pct = (status.images_done / status.total_scenes) * 100
                is_complete_enough = (status.images_done == status.total_scenes)

                if is_complete_enough:
                    # v1.0.441: video_mode=full → phải đợi video xong mới "done"
                    if status.video_mode in ("full",) and not videos_complete:
                        status.current_step = "video"  # Ảnh xong, chờ video
                    else:
                        status.current_step = "done"
                    if status.images_done < status.total_scenes:
                        status.errors.append(f"Completed with {status.images_done}/{status.total_scenes} images ({completion_pct:.1f}%)")

        except Exception as e:
            status.errors.append(str(e))

        return status

    def get_excel_validation_report(self, project_code: str) -> Dict:
        """Báo cáo chi tiết về Excel validation."""
        status = self.get_project_status(project_code)

        report = {
            "project": project_code,
            "excel_exists": status.excel_exists,
            "excel_status": status.excel_status,
            "is_complete": status.excel_status == "complete",

            # Scene counts
            "srt_scenes": status.srt_scene_count,
            "excel_scenes": status.excel_scene_count,
            "scenes_match": not status.scenes_mismatch,

            # Prompt stats
            "total_scenes": status.total_scenes,
            "img_prompts": status.img_prompts_count,
            "video_prompts": status.video_prompts_count,
            "fallback_count": status.fallback_prompts,

            # Missing details
            "missing_img_prompts": status.missing_img_prompts[:10],  # First 10
            "missing_video_prompts": status.missing_video_prompts[:10],
            "fallback_scenes": status.fallback_scenes[:10],

            # Progress
            "images_done": status.images_done,
            "videos_done": status.videos_done,
            "current_step": status.current_step,

            # Issues
            "issues": []
        }

        # Add issues
        if status.scenes_mismatch:
            report["issues"].append(f"Scene count mismatch: SRT={status.srt_scene_count}, Excel={status.excel_scene_count}")

        if status.missing_img_prompts:
            report["issues"].append(f"Missing img_prompt in {len(status.missing_img_prompts)} scenes")

        if status.missing_video_prompts:
            report["issues"].append(f"Missing video_prompt in {len(status.missing_video_prompts)} scenes")

        if status.fallback_prompts > 0:
            report["issues"].append(f"{status.fallback_prompts} scenes have [FALLBACK] prompts (need API)")

        return report

    def check_excel(self, project_code: str) -> tuple:
        status = self.get_project_status(project_code)
        if status.excel_status == "complete":
            return QualityStatus.PASS, {"total": status.total_scenes, "prompts": status.prompts_count}
        elif status.excel_status in ("partial", "fallback"):
            return QualityStatus.PARTIAL, {"fallback": status.fallback_prompts}
        return QualityStatus.FAIL, {}

    def check_images(self, project_code: str, scenes: List[int] = None) -> tuple:
        status = self.get_project_status(project_code)
        if scenes:
            missing = [s for s in scenes if s in status.images_missing]
        else:
            missing = status.images_missing

        if not missing:
            return QualityStatus.PASS, {"completed": status.images_done}
        elif status.images_done > 0:
            return QualityStatus.PARTIAL, {"missing": missing}
        return QualityStatus.FAIL, {"missing": missing}

    def check_videos(self, project_code: str, scenes: List[int] = None) -> tuple:
        status = self.get_project_status(project_code)
        if scenes:
            missing = [s for s in scenes if s in status.videos_missing]
        else:
            missing = status.videos_missing

        if not missing:
            return QualityStatus.PASS, {"completed": status.videos_done}
        elif status.videos_done > 0:
            return QualityStatus.PARTIAL, {"missing": missing}
        return QualityStatus.FAIL, {"missing": missing}


# ================================================================================
# DASHBOARD
# ================================================================================

class Dashboard:
    """Giao diện Dashboard để giám sát hệ thống."""

    def __init__(self, manager: 'VMManager'):
        self.manager = manager

    def clear_screen(self):
        os.system('cls' if sys.platform == 'win32' else 'clear')

    def render(self):
        """Render toàn bộ dashboard."""
        lines = []

        # Header
        lines.extend(self._render_header())

        # Settings
        lines.extend(self._render_settings())

        # Workers
        lines.extend(self._render_workers())

        # Projects
        lines.extend(self._render_projects())

        # Tasks
        lines.extend(self._render_tasks())

        # Errors
        lines.extend(self._render_errors())

        # Commands
        lines.extend(self._render_commands())

        return "\n".join(lines)

    def _render_header(self) -> List[str]:
        now = datetime.now().strftime("%H:%M:%S")
        return [
            "",
            "╔═══════════════════════════════════════════════════════════════════════════╗",
            f"║          VM MANAGER - AI Agent Dashboard           [{now}]         ║",
            "╠═══════════════════════════════════════════════════════════════════════════╣",
        ]

    def _render_settings(self) -> List[str]:
        s = self.manager.settings.get_summary()
        api_status = []
        if s['api_keys']['deepseek']:
            api_status.append("DeepSeek[v]")
        if s['api_keys']['groq']:
            api_status.append("Groq[v]")
        if s['api_keys']['gemini']:
            api_status.append("Gemini[v]")

        ipv6_info = f"IPv6: {'ON' if s['ipv6_enabled'] else 'OFF'}"
        if s['ipv6_enabled']:
            ipv6_info += f" ({s['ipv6_count']} IPs)"
            if s['ipv6_rotate_on_error']:
                ipv6_info += " [Auto-rotate on 403]"

        return [
            "║  SETTINGS:                                                                ║",
            f"║    Chrome Workers: {s['chrome_count']:<5} │ Excel Mode: {s['excel_mode']:<8} │ {ipv6_info:<25}║",
            f"║    API Keys: {' | '.join(api_status) if api_status else 'None configured':<60}║",
            "╠═══════════════════════════════════════════════════════════════════════════╣",
        ]

    def _render_workers(self) -> List[str]:
        lines = ["║  WORKERS:                                                                 ║"]

        for wid, w in self.manager.workers.items():
            emoji = {
                "stopped": "[STOP]️ ",
                "idle": "[IDLE]",
                "working": "[RUN]",
                "error": "[FAIL]"
            }.get(w.status.value, "[?]")

            # Get detailed info from Agent Protocol if available
            details = self.manager.get_worker_details(wid)
            task_info = ""
            progress_info = ""

            if details:
                # Progress bar cho working state
                if details.get("current_scene") and details.get("total_scenes"):
                    progress = int(details["current_scene"] / details["total_scenes"] * 100)
                    progress_info = f"[{progress:>3}%]"

                # Task info
                if details.get("current_project"):
                    task_info = f"→ {details['current_project']}"
                    if details.get("current_scene"):
                        task_info += f" scene {details['current_scene']}/{details['total_scenes']}"
            elif w.current_task:
                task_info = f"→ {w.current_task[:25]}"

            uptime = ""
            if details and details.get("uptime_seconds"):
                mins = details["uptime_seconds"] // 60
                uptime = f"({mins}m)"
            elif w.start_time:
                mins = int((datetime.now() - w.start_time).total_seconds() // 60)
                uptime = f"({mins}m)"

            line = f"║    {emoji} {wid:<12} {w.status.value:<8} done:{w.completed_tasks:<3} fail:{w.failed_tasks:<3} {uptime:<6} {progress_info} {task_info}"
            lines.append(f"{line:<76}║")

        lines.append("╠═══════════════════════════════════════════════════════════════════════════╣")
        return lines

    def _render_projects(self) -> List[str]:
        lines = ["║  PROJECTS:                                                                ║"]

        projects = self.manager.scan_projects()
        if not projects:
            lines.append("║    (No projects found)                                                    ║")
        else:
            for code in projects[:5]:  # Show first 5
                status = self.manager.quality_checker.get_project_status(code)

                # Excel status
                excel_emoji = {
                    "none": "[FAIL]",
                    "empty": "[FAIL]",
                    "fallback": "[WARN]",
                    "partial": "[WARN]",
                    "complete": "[OK]"
                }.get(status.excel_status, "[?]")

                # Progress
                img_pct = (status.images_done / status.total_scenes * 100) if status.total_scenes else 0
                vid_pct = (status.videos_done / status.total_scenes * 100) if status.total_scenes else 0

                step_emoji = {"excel": "[LIST]", "image": "[IMG]", "video": "[VIDEO]", "done": "[OK]"}.get(status.current_step, "[?]")

                line = (
                    f"║    {code:<12} │ "
                    f"Excel:{excel_emoji} {status.prompts_count}/{status.total_scenes} │ "
                    f"Img:{status.images_done}/{status.total_scenes} ({img_pct:.0f}%) │ "
                    f"Vid:{status.videos_done}/{status.total_scenes} ({vid_pct:.0f}%) │ "
                    f"{step_emoji}{status.current_step}"
                )
                lines.append(f"{line:<76}║")

            if len(projects) > 5:
                lines.append(f"║    ... và {len(projects) - 5} projects khác                                          ║")

        lines.append("╠═══════════════════════════════════════════════════════════════════════════╣")
        return lines

    def _render_tasks(self) -> List[str]:
        pending = len([t for t in self.manager.tasks.values() if t.status in (TaskStatus.PENDING, TaskStatus.RETRY)])
        running = len([t for t in self.manager.tasks.values() if t.status in (TaskStatus.ASSIGNED, TaskStatus.RUNNING)])
        completed = len([t for t in self.manager.tasks.values() if t.status == TaskStatus.COMPLETED])
        failed = len([t for t in self.manager.tasks.values() if t.status == TaskStatus.FAILED])

        return [
            "║  TASKS:                                                                   ║",
            f"║    [WAIT] Pending: {pending:<5}  [RUN] Running: {running:<5}  [OK] Done: {completed:<5}  [FAIL] Failed: {failed:<5}    ║",
            "╠═══════════════════════════════════════════════════════════════════════════╣",
        ]

    def _render_errors(self) -> List[str]:
        lines = ["║  RECENT ERRORS:                                                           ║"]

        # Get error summary from Agent Protocol
        error_summary = self.manager.get_error_summary()
        if error_summary:
            summary_parts = [f"{k}:{v}" for k, v in error_summary.items()]
            summary_line = f"║    Summary: {' | '.join(summary_parts)}"
            lines.append(f"{summary_line:<76}║")

        errors = []

        # Collect errors from Agent Protocol
        for wid in self.manager.workers:
            details = self.manager.get_worker_details(wid)
            if details and details.get("last_error"):
                error_type = details.get("last_error_type", "")
                error_msg = details["last_error"][:40]
                errors.append((wid, f"[{error_type}] {error_msg}"))

        # Collect errors from tasks
        for t in list(self.manager.tasks.values())[-3:]:
            if t.error:
                errors.append((t.task_id[:12], t.error[:45]))

        if not errors and not error_summary:
            lines.append("║    (No errors)                                                            ║")
        else:
            for source, error in errors[-4:]:
                line = f"║    [{source}] {error}"
                lines.append(f"{line:<76}║")

        lines.append("╠═══════════════════════════════════════════════════════════════════════════╣")
        return lines

    def _render_commands(self) -> List[str]:
        return [
            "║  COMMANDS:                                                                ║",
            "║    status    - Refresh     │ restart      - Restart all                  ║",
            "║    tasks     - Show tasks  │ restart N    - Restart Chrome N             ║",
            "║    scan      - Scan new    │ scale N      - Scale to N Chrome            ║",
            "║    logs N    - Worker logs │ errors       - Show all errors              ║",
            "║    detail N  - Worker info │ ipv6         - IPv6 status/rotate           ║",
            "║    set       - Settings    │ quit         - Exit                         ║",
            "╚═══════════════════════════════════════════════════════════════════════════╝",
        ]


# ================================================================================
# VM MANAGER - AI AGENT ORCHESTRATOR
# ================================================================================

class VMManager:
    """AI Agent Orchestrator với Dashboard."""

    def __init__(self, num_chrome_workers: int = 2, enable_excel: bool = True):
        self.num_chrome_workers = num_chrome_workers
        self.enable_excel = enable_excel

        # Setup
        self._setup_agent_dirs()
        self.settings = SettingsManager()

        # Agent Protocol for worker monitoring
        if AGENT_PROTOCOL_ENABLED:
            self.agent_protocol = AgentProtocolManager()
        else:
            self.agent_protocol = None

        # Workers
        self.workers: Dict[str, WorkerInfo] = {}
        self._init_workers()

        # Tasks
        self.tasks: Dict[str, Task] = {}
        self.project_tasks: Dict[str, List[str]] = {}

        # Quality & Dashboard
        self.quality_checker = QualityChecker(TOOL_DIR / "PROJECTS")
        self.dashboard = Dashboard(self)

        # Control
        self._stop_flag = False
        self._lock = threading.Lock()
        self.gui_mode = False  # Track if workers run in GUI mode (minimized CMD)
        self._orch_thread = None  # Tránh tạo nhiều orchestrate threads

        # IPv6 Manager for rotation
        if IPV6_MANAGER_ENABLED:
            self.ipv6_manager = get_ipv6_manager()
        else:
            self.ipv6_manager = None

        # Error tracking for intelligent restart/IPv6 rotation
        self.consecutive_403_count = 0  # Tổng 403 liên tiếp (all workers)
        self.worker_error_counts: Dict[str, int] = {}  # Per-worker consecutive errors
        self.max_403_before_ipv6 = 15  # Đổi IPv6 sau 15 lần 403 liên tiếp
        self.max_errors_before_clear = 9  # Xóa data Chrome sau 9 lần lỗi (5 + 2 + 2)

        # v1.0.173: Model switching khi 403
        # Models: GEM_PIX_2 (default), NARWHAL, IMAGEN_3_5
        self.available_models = ["GEM_PIX_2", "NARWHAL", "IMAGEN_3_5"]
        self.worker_current_model: Dict[str, int] = {}  # Index of current model per worker
        self.worker_model_403_counts: Dict[str, int] = {}  # 403 count for current model
        self.first_model_threshold = 5  # 5 lần 403 trước khi chuyển model đầu
        self.other_model_threshold = 2  # 2 lần 403 cho mỗi model khác

        # Auto-restart Chrome workers
        self.chrome_restart_interval = 3600  # 1 tiếng = 3600 giây
        self.chrome_last_restart = time.time()

        # Project timeout
        self.project_timeout = 6 * 3600  # 6 tiếng = 21600 giây
        self.project_start_time = None
        self.current_project_code = None

        # v1.0.234: Account issue detection (1h < 5 ảnh → switch account)
        self.account_issue_timeout = 1 * 3600  # 1 tiếng = 3600 giây
        self.account_issue_min_images = 5       # < 5 ảnh trong 1h = có vấn đề
        self.account_start_time = None          # Timer cho account hiện tại

        # Auto-detect
        self.auto_path = self._detect_auto_path()
        self.channel = self._get_channel_from_folder()

        # Watchdog: report status + read commands from master
        self._watchdog_thread = None
        self._vm_id = TOOL_DIR.parent.name  # e.g., "AR8-T1"

        # v1.0.592: Settings sync tu master
        self._settings_sync = None
        try:
            from modules.settings_sync import SettingsSync
            self._settings_sync = SettingsSync(
                vm_id=self._vm_id,
                local_config_path=CONFIG_FILE,
                log_func=lambda msg: self.log(msg, "SYSTEM", "INFO"),
            )
        except Exception:
            pass

        # v1.0.346: GUI callbacks - master commands gọi thẳng GUI buttons
        self._gui_start_callback = None  # Callback = ấn BẮT ĐẦU trên GUI
        self._gui_stop_callback = None   # Callback = ấn DỪNG trên GUI

    def _setup_agent_dirs(self):
        for d in [AGENT_DIR, TASKS_DIR, RESULTS_DIR, STATUS_DIR, LOGS_DIR]:
            d.mkdir(parents=True, exist_ok=True)
        for f in TASKS_DIR.glob("*.json"):
            f.unlink()
        for f in RESULTS_DIR.glob("*.json"):
            f.unlink()

    def _init_workers(self):
        if self.enable_excel:
            self.workers["excel"] = WorkerInfo(worker_id="excel", worker_type="excel")

        for i in range(self.num_chrome_workers):
            wid = f"chrome_{i+1}"
            self.workers[wid] = WorkerInfo(worker_id=wid, worker_type="chrome", worker_num=i+1)

    def _detect_auto_path(self) -> Optional[Path]:
        # v1.0.324: Dùng robust_copy.get_working_auto_path() với đầy đủ fallback paths
        try:
            from modules.robust_copy import get_working_auto_path
            result = get_working_auto_path(log=lambda msg, lvl="INFO": self.log(msg, "SYSTEM", lvl))
            if result:
                return Path(result)
            return None
        except ImportError:
            pass
        # Fallback nếu module chưa có
        for p in [Path(r"\\tsclient\D\AUTO"), Path(r"\\vmware-host\Shared Folders\D\AUTO"),
                  Path(r"Z:\AUTO"), Path(r"Y:\AUTO"), Path(r"D:\AUTO")]:
            try:
                if p.exists():
                    return p
            except:
                pass
        return None

    def _get_channel_from_folder(self) -> Optional[str]:
        folder = TOOL_DIR.parent.name
        if "-T" in folder:
            return folder.split("-T")[0]
        return None

    def _is_distributed_mode(self) -> bool:
        """v1.0.359: Check distributed mode từ settings.yaml."""
        try:
            import yaml
            settings_path = TOOL_DIR / "config" / "settings.yaml"
            if settings_path.exists():
                with open(settings_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f) or {}
                return config.get('distributed_mode', True)
        except Exception:
            pass
        return True

    # ================================================================================
    # CHROME AUTO-SCALING
    # ================================================================================

    def get_base_chrome_path(self) -> Optional[Path]:
        """Tìm Chrome portable gốc để copy."""
        candidates = [
            TOOL_DIR / "GoogleChromePortable",
            Path.home() / "Documents" / "GoogleChromePortable",
        ]
        for p in candidates:
            exe = p / "GoogleChromePortable.exe"
            if exe.exists():
                return p
        return None

    def get_chrome_path_for_worker(self, worker_num: int) -> Optional[Path]:
        """Lấy đường dẫn Chrome cho worker N."""
        if worker_num == 1:
            return self.get_base_chrome_path()

        # Worker 2+ dùng copy
        base = self.get_base_chrome_path()
        if not base:
            return None

        # Thử các tên khác nhau
        names = [
            f"GoogleChromePortable_{worker_num}",
            f"GoogleChromePortable - Copy{'' if worker_num == 2 else ' ' + str(worker_num - 1)}",
            f"GoogleChromePortable - Copy {worker_num - 1}" if worker_num > 2 else "GoogleChromePortable - Copy",
        ]

        for name in names:
            path = base.parent / name
            if (path / "GoogleChromePortable.exe").exists():
                return path

        return None

    def create_chrome_for_worker(self, worker_num: int) -> Optional[Path]:
        """
        Tạo Chrome portable cho worker N bằng cách copy từ base.
        Không copy Data folder để user cần login lại.

        Returns:
            Path to Chrome folder if created, None if failed
        """
        if worker_num == 1:
            return self.get_base_chrome_path()

        base = self.get_base_chrome_path()
        if not base:
            self.log("Base Chrome not found, cannot create new instances", "CHROME", "ERROR")
            return None

        target_name = f"GoogleChromePortable_{worker_num}"
        target_path = base.parent / target_name

        if target_path.exists():
            self.log(f"Chrome {worker_num} already exists: {target_path}", "CHROME")
            return target_path

        self.log(f"Creating Chrome {worker_num} from base...", "CHROME")

        try:
            # Copy entire folder except Data (so user needs to login)
            def ignore_data(directory, files):
                """Ignore Data folder for fresh login."""
                if directory == str(base):
                    return ['Data', 'User Data']
                return []

            shutil.copytree(base, target_path, ignore=ignore_data)
            self.log(f"Created Chrome {worker_num}: {target_path}", "CHROME", "SUCCESS")
            return target_path
        except Exception as e:
            self.log(f"Failed to create Chrome {worker_num}: {e}", "CHROME", "ERROR")
            return None

    def ensure_chrome_script(self, worker_num: int) -> Optional[Path]:
        """
        Đảm bảo script _run_chromeN.py tồn tại.
        Nếu chưa có, tạo từ template.
        """
        script_path = TOOL_DIR / f"_run_chrome{worker_num}.py"

        if script_path.exists():
            return script_path

        # Template cho Chrome worker script
        self.log(f"Creating script: {script_path.name}", "CHROME")

        # Copy từ _run_chrome1.py và sửa worker number
        base_script = TOOL_DIR / "_run_chrome1.py"
        if not base_script.exists():
            self.log("Base script _run_chrome1.py not found", "CHROME", "ERROR")
            return None

        try:
            with open(base_script, 'r', encoding='utf-8') as f:
                content = f.read()

            # Cập nhật parallel_chrome setting cho worker này
            # Thay "1/2" thành "N/total"
            import re
            # Tìm và thay thế pattern parallel_chrome
            content = re.sub(
                r"parallel_chrome\s*=\s*['\"][^'\"]*['\"]",
                f'parallel_chrome = "{worker_num}/{self.num_chrome_workers}"',
                content
            )

            # Cập nhật WORKER_ID nếu có
            content = re.sub(
                r"WORKER_ID\s*=\s*['\"]chrome_\d+['\"]",
                f'WORKER_ID = "chrome_{worker_num}"',
                content
            )

            # Cập nhật chrome portable path cho worker này
            chrome_path = self.get_chrome_path_for_worker(worker_num)
            if chrome_path:
                # Thêm logic để dùng Chrome portable riêng
                content = re.sub(
                    r"(# Chrome portable path)",
                    f'# Chrome portable path for worker {worker_num}\n'
                    f'CHROME_PORTABLE_{worker_num} = Path(r"{chrome_path}")',
                    content
                )

            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(content)

            self.log(f"Created script: {script_path.name}", "CHROME", "SUCCESS")
            return script_path
        except Exception as e:
            self.log(f"Failed to create script: {e}", "CHROME", "ERROR")
            return None

    def scale_chrome_workers(self, new_count: int) -> bool:
        """
        Scale số lượng Chrome workers.
        Tự động tạo Chrome portable và scripts nếu cần.

        Args:
            new_count: Số Chrome workers mới

        Returns:
            True nếu scale thành công
        """
        if new_count < 1:
            self.log("Chrome count must be >= 1", "CHROME", "ERROR")
            return False

        self.log(f"Scaling Chrome workers: {self.num_chrome_workers} → {new_count}", "CHROME")

        # Tạo Chrome portable và scripts cho workers mới
        for i in range(1, new_count + 1):
            # Kiểm tra/tạo Chrome portable
            chrome_path = self.get_chrome_path_for_worker(i)
            if not chrome_path:
                chrome_path = self.create_chrome_for_worker(i)
                if not chrome_path:
                    self.log(f"Failed to setup Chrome {i}", "CHROME", "ERROR")
                    return False

            # Kiểm tra/tạo script
            script = self.ensure_chrome_script(i)
            if not script:
                self.log(f"Failed to create script for Chrome {i}", "CHROME", "ERROR")
                return False

        # Cập nhật số workers
        old_count = self.num_chrome_workers
        self.num_chrome_workers = new_count
        self._init_workers()

        # Cập nhật settings
        self.settings.chrome_count = new_count

        self.log(f"Scaled to {new_count} Chrome workers", "CHROME", "SUCCESS")

        # Nếu có workers mới, thông báo cần login
        if new_count > old_count:
            new_workers = [f"chrome_{i}" for i in range(old_count + 1, new_count + 1)]
            self.log(f"New workers need Google login: {new_workers}", "CHROME", "WARN")

        return True

    def log(self, msg: str, source: str = "MANAGER", level: str = "INFO"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        emoji = {"INFO": "  ", "WARN": "[WARN]", "ERROR": "[FAIL]", "SUCCESS": "[OK]", "TASK": "[LIST]"}.get(level, "  ")
        line = f"[{timestamp}] [{source}] {emoji} {msg}"
        print(line)
        try:
            log_path = TOOL_DIR / "logs" / "manager.log"
            log_path.parent.mkdir(exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except:
            pass

    # ================================================================================
    # AGENT PROTOCOL INTEGRATION
    # ================================================================================

    def sync_worker_status(self):
        """Đồng bộ trạng thái worker từ Agent Protocol."""
        if not self.agent_protocol:
            return

        for worker_id, worker in self.workers.items():
            agent_status = self.agent_protocol.get_worker_status(worker_id)
            if agent_status:
                # Cập nhật status từ agent protocol
                if agent_status.state == "working":
                    worker.status = WorkerStatus.WORKING
                elif agent_status.state == "idle":
                    worker.status = WorkerStatus.IDLE
                elif agent_status.state == "error":
                    worker.status = WorkerStatus.ERROR
                    worker.last_error = agent_status.last_error
                elif agent_status.state == "stopped":
                    worker.status = WorkerStatus.STOPPED

                # Cập nhật task info
                if agent_status.current_task:
                    worker.current_task = agent_status.current_task
                worker.completed_tasks = agent_status.completed_count
                worker.failed_tasks = agent_status.failed_count

    def check_worker_health(self, cooldown_seconds: int = 120) -> List[tuple]:
        """
        Kiểm tra health của workers, trả về danh sách (worker_id, error_type).

        Args:
            cooldown_seconds: Thời gian chờ tối thiểu giữa các lần restart (mặc định 120s)

        Returns:
            List of tuples: [(worker_id, error_type), ...]
        """
        workers_with_errors = []

        if not self.agent_protocol:
            return workers_with_errors

        for worker_id, worker in self.workers.items():
            if worker.status == WorkerStatus.STOPPED:
                continue

            # Check cooldown - không action nếu vừa restart gần đây
            if worker.last_restart_time:
                elapsed = (datetime.now() - worker.last_restart_time).total_seconds()
                if elapsed < cooldown_seconds:
                    continue  # Skip, đang trong cooldown

            # Check if worker is alive (updated status within last 60s)
            if not self.agent_protocol.is_worker_alive(worker_id, timeout_seconds=60):
                self.log(f"{worker_id} không phản hồi (timeout 60s)", worker_id, "WARN")
                workers_with_errors.append((worker_id, "timeout"))
                continue

            # Check for critical errors - chỉ xử lý nếu error mới (sau lần restart cuối)
            agent_status = self.agent_protocol.get_worker_status(worker_id)
            if agent_status and agent_status.last_error_type:
                error_type = agent_status.last_error_type
                if error_type in ("chrome_crash", "chrome_403", "api_error"):
                    # Kiểm tra xem error này có phải sau lần restart cuối không
                    try:
                        error_time = datetime.fromisoformat(agent_status.last_update)
                        if worker.last_restart_time and error_time < worker.last_restart_time:
                            continue  # Error cũ, đã restart rồi
                    except:
                        pass

                    workers_with_errors.append((worker_id, error_type))

        return workers_with_errors

    def get_worker_details(self, worker_id: str) -> Optional[Dict]:
        """Lấy thông tin chi tiết của worker từ Agent Protocol."""
        if not self.agent_protocol:
            return None

        agent_status = self.agent_protocol.get_worker_status(worker_id)
        if not agent_status:
            return None

        return {
            "state": agent_status.state,
            "progress": agent_status.progress,
            "current_project": agent_status.current_project,
            "current_task": agent_status.current_task,
            "current_scene": agent_status.current_scene,
            "total_scenes": agent_status.total_scenes,
            "current_step": agent_status.current_step,
            "step_name": agent_status.step_name,
            "completed_count": agent_status.completed_count,
            "failed_count": agent_status.failed_count,
            "last_error": agent_status.last_error,
            "last_error_type": agent_status.last_error_type,
            "uptime_seconds": agent_status.uptime_seconds,
        }

    def get_worker_status(self, worker_id: str) -> Optional[Dict]:
        """
        Lấy trạng thái worker cho GUI hiển thị.
        Trả về dict với các thông tin cần thiết.
        """
        details = self.get_worker_details(worker_id)
        if not details:
            return None

        # Thêm thông tin cho GUI
        return {
            "state": details.get("state", "idle"),
            "current_project": details.get("current_project", ""),
            "current_task": details.get("current_task", ""),
            "current_step": details.get("current_step", 0),
            "step_name": details.get("step_name", ""),
            "current_scene": details.get("current_scene", ""),
            "total_scenes": details.get("total_scenes", 0),
            "completed_count": details.get("completed_count", 0),
            "failed_count": details.get("failed_count", 0),
            "progress": details.get("progress", 0),
        }

    def get_worker_logs(self, worker_id: str, lines: int = 20) -> List[str]:
        """Lấy log gần nhất của worker."""
        if not self.agent_protocol:
            return []
        return self.agent_protocol.get_recent_logs(worker_id, lines)

    def get_worker_log_file(self, worker_id: str, lines: int = 50) -> List[str]:
        """Đọc log từ file log của worker (cho hidden mode)."""
        log_file = AGENT_DIR / "logs" / f"{worker_id}.log"
        if not log_file.exists():
            return []
        try:
            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                all_lines = f.readlines()
                return all_lines[-lines:] if len(all_lines) > lines else all_lines
        except Exception as e:
            return [f"Error reading log: {e}"]

    def get_all_worker_logs(self, lines_per_worker: int = 20) -> Dict[str, List[str]]:
        """Lấy log của tất cả workers (cho GUI)."""
        logs = {}
        for worker_id in self.workers:
            # Try log file first (hidden mode), then agent protocol
            file_logs = self.get_worker_log_file(worker_id, lines_per_worker)
            if file_logs:
                logs[worker_id] = file_logs
            else:
                logs[worker_id] = self.get_worker_logs(worker_id, lines_per_worker)
        return logs

    def get_error_summary(self) -> Dict[str, int]:
        """Lấy tóm tắt các loại lỗi từ tất cả workers."""
        if not self.agent_protocol:
            return {}
        return self.agent_protocol.get_error_summary()

    # ================================================================================
    # ERROR TRACKING & IPv6 ROTATION
    # ================================================================================

    def track_worker_error(self, worker_id: str, error_type: str) -> str:
        """
        Track lỗi của worker và quyết định hành động.

        v1.0.173: Thử chuyển model trước khi xóa data
        - Model hiện tại: 5 lần 403
        - Model khác 1: 2 lần 403
        - Model khác 2: 2 lần 403
        - Tổng 9 lần → xóa data

        Returns:
            "none" - Không cần action
            "restart" - Restart worker đó
            "switch_model" - Chuyển sang model khác
            "clear_data" - Xóa data Chrome và login lại
            "rotate_ipv6" - Đổi IPv6 và restart tất cả
        """
        # Track per-worker errors
        if worker_id not in self.worker_error_counts:
            self.worker_error_counts[worker_id] = 0
        if worker_id not in self.worker_current_model:
            self.worker_current_model[worker_id] = 0  # Start with first model
        if worker_id not in self.worker_model_403_counts:
            self.worker_model_403_counts[worker_id] = 0

        if error_type == "chrome_403":
            # Track 403 globally
            self.consecutive_403_count += 1
            self.worker_error_counts[worker_id] += 1
            self.worker_model_403_counts[worker_id] += 1

            current_model_idx = self.worker_current_model[worker_id]
            current_model = self.available_models[current_model_idx]
            model_403_count = self.worker_model_403_counts[worker_id]

            # Threshold: 5 cho model đầu, 2 cho các model sau
            threshold = self.first_model_threshold if current_model_idx == 0 else self.other_model_threshold

            self.log(f"403 count: {self.worker_error_counts[worker_id]}/9 ({worker_id}), "
                     f"model {current_model}: {model_403_count}/{threshold}", "ERROR", "WARN")

            # v1.0.373: IPv6 rotation CHỈ do chrome_1 quyết định
            # Chrome 2 theo mạng hiện tại, không tự rotate IPv6
            if self.ipv6_manager and self.ipv6_manager.enabled:
                if self.consecutive_403_count >= self.max_403_before_ipv6:
                    if worker_id == "chrome_1":
                        return "rotate_ipv6"
                    else:
                        # Chrome 2: reset counter, chỉ restart (theo mạng Chrome 1 đã set)
                        self.log(f"[{worker_id}] Skip IPv6 rotation (chi Chrome 1 quyet dinh)", worker_id, "INFO")
                        self.consecutive_403_count = 0

            # Check if need to switch model
            if model_403_count >= threshold:
                # Còn model khác để thử?
                if current_model_idx < len(self.available_models) - 1:
                    return "switch_model"
                else:
                    # Đã thử hết tất cả models → xóa data
                    return "clear_data"

            return "restart"

        elif error_type in ("chrome_crash", "timeout", "unknown"):
            self.worker_error_counts[worker_id] += 1

            if self.worker_error_counts[worker_id] >= self.max_errors_before_clear:
                return "clear_data"

            return "restart"

        return "none"

    def reset_error_tracking(self, worker_id: str = None, reset_model: bool = True):
        """
        Reset error tracking.

        Args:
            worker_id: Worker cụ thể hoặc None = tất cả
            reset_model: True = reset về model 0 (sau xóa data), False = giữ model (sau thành công)
        """
        if worker_id:
            self.worker_error_counts[worker_id] = 0
            self.worker_model_403_counts[worker_id] = 0
            # v1.0.173: Chỉ reset model khi cần (sau xóa data)
            if reset_model:
                self.worker_current_model[worker_id] = 0
                # Xóa file model để worker dùng mặc định
                try:
                    model_file = TOOL_DIR / ".agent" / "status" / f"{worker_id}_model.txt"
                    if model_file.exists():
                        model_file.unlink()
                except:
                    pass
        else:
            # Reset all
            self.consecutive_403_count = 0
            self.worker_error_counts.clear()
            self.worker_model_403_counts.clear()
            if reset_model:
                self.worker_current_model.clear()

    def perform_ipv6_rotation(self) -> bool:
        """
        Thực hiện IPv6 rotation:
        1. Tắt tất cả Chrome workers
        2. Đổi IPv6
        3. Khởi động lại tất cả

        Returns:
            True nếu thành công
        """
        if not self.ipv6_manager or not self.ipv6_manager.enabled:
            self.log("IPv6 rotation disabled or not available", "IPv6", "WARN")
            return False

        self.log("Starting IPv6 rotation...", "IPv6", "WARN")

        # 1. Stop all Chrome workers
        for wid, w in self.workers.items():
            if w.worker_type == "chrome":
                self.stop_worker(wid)

        # 2. Kill all Chrome processes
        self.kill_all_chrome()
        time.sleep(2)

        # 3. Rotate IPv6
        result = self.ipv6_manager.rotate_ipv6()

        if result["success"]:
            self.log(f"IPv6 rotated: {result['message']}", "IPv6", "SUCCESS")

            # 4. Reset error tracking
            self.reset_error_tracking()

            # 5. Wait and restart Chrome workers
            time.sleep(3)
            for wid, w in self.workers.items():
                if w.worker_type == "chrome":
                    self.start_worker(wid, gui_mode=self.gui_mode)
                    time.sleep(2)

            return True
        else:
            self.log(f"IPv6 rotation failed: {result['message']}", "IPv6", "ERROR")
            return False

    def clear_chrome_data(self, worker_id: str) -> bool:
        """
        Xóa data Chrome và khởi động lại.
        Worker sẽ cần login lại.
        """
        self.log(f"Clearing Chrome data for {worker_id}...", worker_id, "WARN")

        # Stop worker
        self.stop_worker(worker_id)
        self.kill_chrome_by_worker(worker_id)  # ✅ CHỈ kill worker này!

        # Get Chrome profile path
        w = self.workers.get(worker_id)
        if not w:
            return False

        # Xóa data Chrome Portable - giữ lại First Run để không bị thông báo lần đầu
        try:
            # Determine which Chrome folder based on worker_id
            if worker_id == "chrome_1":
                chrome_data = TOOL_DIR / "GoogleChromePortable" / "Data"
            else:
                chrome_data = TOOL_DIR / "GoogleChromePortable - Copy" / "Data"

            if chrome_data.exists():
                self.log(f"Deleting Chrome data: {chrome_data}", worker_id, "WARN")

                profile_dir = chrome_data / "profile"
                first_run_file = profile_dir / "First Run"

                # Backup First Run if exists
                first_run_backup = None
                if first_run_file.exists():
                    first_run_backup = first_run_file.read_bytes() if first_run_file.stat().st_size > 0 else b''

                # Delete everything in Data folder
                for item in chrome_data.iterdir():
                    try:
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()
                        self.log(f"  Deleted: {item.name}", worker_id)
                    except Exception as e:
                        self.log(f"  Failed to delete {item.name}: {e}", worker_id, "WARN")

                # Restore First Run to avoid first-run prompts
                profile_dir.mkdir(parents=True, exist_ok=True)
                first_run_file.touch()
                if first_run_backup:
                    first_run_file.write_bytes(first_run_backup)
                self.log(f"  Restored: profile/First Run", worker_id)
        except Exception as e:
            self.log(f"Error clearing Chrome data: {e}", worker_id, "ERROR")

        self.reset_error_tracking(worker_id)

        time.sleep(3)
        self.start_worker(worker_id, gui_mode=self.gui_mode)

        self.log(f"Chrome data cleared, {worker_id} restarted", worker_id, "SUCCESS")
        return True

    def handle_worker_error(self, worker_id: str, error_type: str):
        """
        Xử lý lỗi worker theo logic thông minh:
        - 403 lần 1-5 → Restart (model hiện tại)
        - 403 lần 6-7 → Chuyển model 2, restart
        - 403 lần 8-9 → Chuyển model 3, restart
        - 403 lần 10+ → Clear data + Restart
        - 403 lỗi 5 lần (any worker) → Đổi IPv6 + Restart all
        """
        action = self.track_worker_error(worker_id, error_type)

        if action == "rotate_ipv6":
            self.log(f"403 threshold reached ({self.consecutive_403_count}), rotating IPv6...", "MANAGER", "ERROR")
            self.perform_ipv6_rotation()

        elif action == "switch_model":
            self.switch_worker_model(worker_id)

        elif action == "clear_data":
            self.log(f"Error threshold reached for {worker_id}, clearing data...", worker_id, "ERROR")
            self.clear_chrome_data(worker_id)

        elif action == "restart":
            self.restart_worker(worker_id)
            # Reset count cho worker này sau restart
            self.worker_error_counts[worker_id] = max(0, self.worker_error_counts.get(worker_id, 1) - 1)

    def switch_worker_model(self, worker_id: str):
        """
        v1.0.173: Chuyển sang model khác khi gặp 403 nhiều lần.
        """
        # Chuyển sang model tiếp theo
        current_idx = self.worker_current_model.get(worker_id, 0)
        new_idx = current_idx + 1

        if new_idx >= len(self.available_models):
            self.log(f"[{worker_id}] Đã thử hết tất cả models!", worker_id, "ERROR")
            return

        new_model = self.available_models[new_idx]
        old_model = self.available_models[current_idx]

        self.worker_current_model[worker_id] = new_idx
        self.worker_model_403_counts[worker_id] = 0  # Reset 403 count cho model mới

        self.log(f"[{worker_id}] Chuyển model: {old_model} → {new_model} (index {new_idx})", worker_id, "WARN")

        # Ghi model index vào file để worker đọc và click chọn trên giao diện
        self._write_worker_model(worker_id, str(new_idx))

        # Restart worker để áp dụng model mới
        self.restart_worker(worker_id)

    def _write_worker_model(self, worker_id: str, model_name: str):
        """Ghi model vào file để worker đọc."""
        try:
            model_file = TOOL_DIR / ".agent" / "status" / f"{worker_id}_model.txt"
            model_file.parent.mkdir(parents=True, exist_ok=True)
            model_file.write_text(model_name)
            self.log(f"  → Model file: {model_file}", worker_id)
        except Exception as e:
            self.log(f"  → Error writing model file: {e}", worker_id, "WARN")

    def get_worker_model(self, worker_id: str) -> str:
        """Đọc model hiện tại của worker."""
        try:
            model_file = TOOL_DIR / ".agent" / "status" / f"{worker_id}_model.txt"
            if model_file.exists():
                return model_file.read_text().strip()
        except:
            pass
        return self.available_models[0]  # Default: GEM_PIX_2

    # ================================================================================
    # TASK MANAGEMENT
    # ================================================================================

    def create_task(self, task_type: TaskType, project_code: str, scenes: List[int] = None) -> Task:
        task_id = f"{task_type.value}_{project_code}_{datetime.now().strftime('%H%M%S%f')[:10]}"
        task = Task(
            task_id=task_id,
            task_type=task_type,
            project_code=project_code,
            scenes=scenes or [],
            created_at=datetime.now().isoformat(),
        )
        self.tasks[task_id] = task
        if project_code not in self.project_tasks:
            self.project_tasks[project_code] = []
        self.project_tasks[project_code].append(task_id)
        self.log(f"Created: {task_id}", "TASK", "TASK")
        return task

    def assign_task(self, task: Task, worker_id: str) -> bool:
        if worker_id not in self.workers:
            return False
        worker = self.workers[worker_id]
        if worker.current_task:
            return False

        task.status = TaskStatus.ASSIGNED
        task.assigned_to = worker_id
        task.assigned_at = datetime.now().isoformat()
        worker.current_task = task.task_id
        worker.status = WorkerStatus.WORKING

        task_file = TASKS_DIR / f"{worker_id}.json"
        with open(task_file, 'w', encoding='utf-8') as f:
            json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)

        self.log(f"Assigned: {task.task_id} → {worker_id}", "TASK", "TASK")
        return True

    def get_pending_tasks(self, task_type: TaskType = None) -> List[Task]:
        return [t for t in self.tasks.values()
                if t.status in (TaskStatus.PENDING, TaskStatus.RETRY)
                and (task_type is None or t.task_type == task_type)]

    def get_idle_worker(self, worker_type: str) -> Optional[str]:
        for wid, w in self.workers.items():
            if w.worker_type == worker_type and not w.current_task:
                if w.status in (WorkerStatus.IDLE, WorkerStatus.STOPPED):
                    return wid
        return None

    def collect_results(self):
        for f in RESULTS_DIR.glob("*.json"):
            try:
                with open(f, 'r', encoding='utf-8') as rf:
                    result = json.load(rf)
                task_id = result.get('task_id')
                if task_id in self.tasks:
                    task = self.tasks[task_id]
                    task.result = result
                    task.completed_at = datetime.now().isoformat()
                    task.status = TaskStatus.COMPLETED if result.get('success') else TaskStatus.FAILED
                    task.error = result.get('error', '')

                    if task.assigned_to in self.workers:
                        w = self.workers[task.assigned_to]
                        w.current_task = None
                        w.status = WorkerStatus.IDLE
                        if task.status == TaskStatus.COMPLETED:
                            w.completed_tasks += 1
                            # v1.0.173: Reset 403 count khi thành công, GIỮ model hiện tại
                            self.reset_error_tracking(task.assigned_to, reset_model=False)
                        else:
                            w.failed_tasks += 1
                            w.last_error = task.error
                f.unlink()
            except Exception as e:
                self.log(f"Result error: {e}", "ERROR", "ERROR")

    def check_and_retry(self, task: Task) -> Optional[Task]:
        if task.retry_count >= task.max_retries:
            return None

        if task.task_type == TaskType.EXCEL:
            status, details = self.quality_checker.check_excel(task.project_code)
        elif task.task_type == TaskType.IMAGE:
            status, details = self.quality_checker.check_images(task.project_code, task.scenes)
        else:
            status, details = self.quality_checker.check_videos(task.project_code, task.scenes)

        if status == QualityStatus.PASS:
            return None

        missing = details.get('missing', task.scenes)
        retry = self.create_task(task.task_type, task.project_code, missing)
        retry.retry_count = task.retry_count + 1
        retry.status = TaskStatus.RETRY
        return retry

    # ================================================================================
    # PROJECT & TASK CREATION
    # ================================================================================

    def scan_projects(self) -> List[str]:
        """Scan và sort projects theo priority: GẦN XONG làm trước."""
        projects = []
        local = TOOL_DIR / "PROJECTS"
        if not local.exists():
            return projects

        # v1.0.359: Distributed mode → bỏ channel filter
        distributed = self._is_distributed_mode()

        for item in local.iterdir():
            if item.is_dir():
                code = item.name
                if not distributed and self.channel and not code.startswith(f"{self.channel}-"):
                    # v1.0.295: Vẫn include project "done" dù khác channel → để copy sang master
                    if (item / f"{code}.srt").exists():
                        try:
                            st = self.quality_checker.get_project_status(code)
                            self.log(f"[DEBUG] {code} (other channel): step={st.current_step}, imgs={st.images_done}/{st.total_scenes}, video_mode={st.video_mode}", "MANAGER")
                            if st.current_step == "done":
                                projects.append(code)
                        except Exception as e:
                            self.log(f"[DEBUG] {code} get_status error: {e}", "MANAGER")
                    continue
                if (item / f"{code}.srt").exists():
                    projects.append(code)

        # v1.0.82: Sort by completion - project gần xong làm trước
        # Priority: images_done / total_scenes (cao hơn = ưu tiên hơn)
        def get_completion(code: str) -> float:
            try:
                status = self.quality_checker.get_project_status(code)
                if status.total_scenes == 0:
                    return -1  # Chưa có scenes → priority thấp nhất
                return status.images_done / status.total_scenes
            except:
                return -1

        # Sort descending: completion cao → làm trước
        return sorted(projects, key=get_completion, reverse=True)

    def create_thumbnail(self, project_code: str):
        """Create thumbnail from main character (not child) for master."""
        src_dir = TOOL_DIR / "PROJECTS" / project_code
        excel_path = src_dir / f"{project_code}_prompts.xlsx"

        if not excel_path.exists():
            return

        try:
            from modules.excel_manager import PromptWorkbook
            wb = PromptWorkbook(str(excel_path))
            characters = wb.get_characters()

            if not characters:
                return

            # Find main character (not child, not location)
            main_char = None

            # Filter out locations (id starts with "loc" or role="location")
            actual_characters = [
                c for c in characters
                if not c.id.lower().startswith("loc") and c.role != "location"
            ]

            if not actual_characters:
                return

            # Strategy 1: Find role="main" or "protagonist" and not child
            for char in actual_characters:
                if ("main" in char.role.lower() or "protagonist" in char.role.lower()) and not char.is_child:
                    main_char = char
                    break

            # Strategy 2: If no main, find first non-child character
            if not main_char:
                for char in actual_characters:
                    if not char.is_child:
                        main_char = char
                        break

            # Strategy 3: If all are children, use first character
            if not main_char and actual_characters:
                main_char = actual_characters[0]

            if not main_char or not main_char.image_file:
                return

            # Find the image file
            nv_dir = src_dir / "nv"
            if not nv_dir.exists():
                return

            src_image = nv_dir / main_char.image_file
            if not src_image.exists():
                # Try without extension
                for ext in [".png", ".jpg", ".jpeg"]:
                    alt = nv_dir / f"{main_char.id}{ext}"
                    if alt.exists():
                        src_image = alt
                        break

            if not src_image.exists():
                return

            # Create thumb folder and copy
            thumb_dir = src_dir / "thumb"
            thumb_dir.mkdir(exist_ok=True)

            import shutil
            dest_image = thumb_dir / src_image.name
            shutil.copy2(str(src_image), str(dest_image))

            self.log(f"Created thumbnail: {main_char.id} ({main_char.role}, child={main_char.is_child})", "SYSTEM", "SUCCESS")

        except Exception as e:
            self.log(f"Failed to create thumbnail: {e}", "SYSTEM", "ERROR")

    def copy_project_to_master(self, project_code: str, delete_after: bool = True) -> bool:
        """Copy project folder to master (AUTO path).

        v1.0.94: Tách riêng logic xóa ra function _delete_project_folder.
        v1.0.100: Return True/False để caller biết kết quả.
        Args:
            project_code: Mã project
            delete_after: Nếu True, xóa local folder sau khi copy (default: True cho backward compat)
        Returns:
            True nếu copy thành công, False nếu thất bại
        """
        src_dir = TOOL_DIR / "PROJECTS" / project_code
        if not src_dir.exists():
            self.log(f"Project {project_code} not found in PROJECTS/", "SYSTEM", "ERROR")
            return False

        # Create thumbnail before copying
        self.create_thumbnail(project_code)

        # v1.0.324: Robust copy với fallback AUTO paths
        _log = lambda msg, lvl="INFO": self.log(msg, "SYSTEM", lvl)
        copy_success = False
        used_path = None
        try:
            from modules.robust_copy import robust_copy_to_master
            copy_success, used_path = robust_copy_to_master(
                str(src_dir),
                f"visual/{project_code}",
                current_auto_path=str(self.auto_path) if self.auto_path else None,
                max_retries=3, retry_delay=5,
                log=_log,
            )
            # Cập nhật auto_path nếu đã switch sang path khác
            if used_path and (not self.auto_path or str(self.auto_path) != used_path):
                self.log(f"AUTO path switched: {self.auto_path} → {used_path}", "SYSTEM", "INFO")
                self.auto_path = Path(used_path)
        except ImportError:
            if not self.auto_path:
                self.log("No AUTO path detected, skip copy", "SYSTEM", "WARN")
                return False
            # Fallback nếu module chưa có
            import shutil
            dest_dir = self.auto_path / "visual" / project_code
            dest_dir.mkdir(parents=True, exist_ok=True)
            copy_success = True
            for item in src_dir.iterdir():
                dest_item = dest_dir / item.name
                try:
                    if item.is_dir():
                        if dest_item.exists():
                            shutil.rmtree(str(dest_item))
                        shutil.copytree(str(item), str(dest_item))
                    else:
                        shutil.copy2(str(item), str(dest_item))
                except Exception as e:
                    self.log(f"Failed to copy {item.name}: {e}", "SYSTEM", "ERROR")
                    copy_success = False

        if copy_success:
            dest_desc = used_path or str(self.auto_path)
            self.log(f"Copied {project_code} to {dest_desc}/visual/{project_code}", "SYSTEM", "SUCCESS")
            # v1.0.326: Release claim nếu distributed mode
            self._release_claim(project_code)
        else:
            self.log(f"Copy {project_code} FAILED - all paths tried!", "SYSTEM", "ERROR")

        # v1.0.94: Chỉ xóa nếu delete_after=True (backward compatible)
        if copy_success and delete_after:
            self._delete_project_folder(project_code)

        return copy_success

    def _release_claim(self, project_code: str):
        """Release claim khi project xong (distributed mode)."""
        try:
            from modules.robust_copy import TaskQueue
            import yaml
            settings_path = TOOL_DIR / "config" / "settings.yaml"
            if not settings_path.exists():
                return
            with open(settings_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}
            if not config.get('distributed_mode', True):
                return
            if not self.auto_path:
                return
            master_projects = str(self.auto_path / "ve3-tool-simple" / "PROJECTS")
            vm_id = TOOL_DIR.parent.name
            tq = TaskQueue(master_projects, vm_id,
                           log=lambda msg, lvl="INFO": self.log(msg, "SYSTEM", lvl))
            tq.release(project_code)
            self.log(f"Released claim: {project_code}", "SYSTEM", "INFO")
        except Exception as e:
            self.log(f"Release claim error: {e}", "SYSTEM", "WARN")

    def _delete_project_folder(self, project_code: str):
        """v1.0.94: Xóa local project folder với retry logic mạnh hơn.

        Flow:
        1. Thử xóa cả folder (5 lần, delay 3s mỗi lần)
        2. Nếu fail, thử xóa từng file riêng lẻ
        3. Cuối cùng thử xóa folder rỗng
        """
        import shutil
        import gc
        import subprocess

        src_dir = TOOL_DIR / "PROJECTS" / project_code
        if not src_dir.exists():
            return  # Đã xóa rồi

        self.log(f"Deleting local folder: {project_code}...", "SYSTEM")

        # Method 1: Thử xóa cả folder
        for retry in range(5):
            try:
                gc.collect()
                shutil.rmtree(str(src_dir))
                self.log(f"Deleted local folder: {src_dir}", "SYSTEM", "SUCCESS")
                return  # Success!
            except Exception as e:
                if retry < 4:
                    delay = 3  # 3s mỗi lần
                    self.log(f"Delete retry {retry+1}/5, waiting {delay}s... ({type(e).__name__})", "SYSTEM", "WARN")
                    time.sleep(delay)
                else:
                    self.log(f"rmtree failed after 5 attempts: {e}", "SYSTEM", "WARN")

        # Method 2: Xóa từng file riêng lẻ
        self.log("Trying to delete files individually...", "SYSTEM", "WARN")
        deleted_files = 0
        locked_files = []
        try:
            for item in list(src_dir.rglob("*")):
                if item.is_file():
                    try:
                        item.unlink()
                        deleted_files += 1
                    except Exception as e:
                        locked_files.append(item.name)

            self.log(f"Deleted {deleted_files} files, {len(locked_files)} locked", "SYSTEM", "INFO")

            if locked_files:
                self.log(f"Locked files: {locked_files[:5]}{'...' if len(locked_files) > 5 else ''}", "SYSTEM", "WARN")
        except Exception as e:
            self.log(f"Individual delete error: {e}", "SYSTEM", "WARN")

        # Method 3: Dùng Windows cmd để force delete (rd /s /q)
        if src_dir.exists():
            try:
                self.log("Trying Windows force delete (rd /s /q)...", "SYSTEM", "WARN")
                result = subprocess.run(
                    ['cmd', '/c', 'rd', '/s', '/q', str(src_dir)],
                    capture_output=True, timeout=30
                )
                if not src_dir.exists():
                    self.log("Windows force delete succeeded", "SYSTEM", "SUCCESS")
                    return
            except Exception as e:
                self.log(f"Windows force delete failed: {e}", "SYSTEM", "WARN")

        # Final check
        if src_dir.exists():
            remaining = list(src_dir.rglob("*"))
            self.log(f"Could not fully delete folder, {len(remaining)} items remain", "SYSTEM", "ERROR")
            # v1.0.279: Tạo marker _COPIED_TO_VISUAL để Chrome scan bỏ qua project này
            # Tránh trường hợp: .account.json bị xóa nhưng directory còn → Chrome pick up lại → rotate account
            try:
                marker = src_dir / "_COPIED_TO_VISUAL"
                marker.touch()
                self.log(f"Created _COPIED_TO_VISUAL marker (partial delete) → Chrome will skip", "SYSTEM", "WARN")
            except Exception as me:
                self.log(f"Could not create marker: {me}", "SYSTEM", "WARN")
        else:
            self.log("Local folder deleted successfully", "SYSTEM", "SUCCESS")

    def create_tasks_for_project(self, project_code: str):
        status = self.quality_checker.get_project_status(project_code)
        self.log(f"[DEBUG] create_tasks: {project_code} step={status.current_step} imgs={status.images_done}/{status.total_scenes} auto_path={self.auto_path}", "MANAGER")

        # Start project timer nếu đây là project mới
        if self.current_project_code != project_code:
            self.current_project_code = project_code
            self.project_start_time = time.time()
            self.account_start_time = time.time()  # v1.0.234: Reset account timer
            self.log(f"Started tracking project {project_code} (6h timeout)", "MANAGER")

        # Check if project is completed
        if status.current_step == "done":
            # v1.0.666: Re-detect auto_path neu mat ket noi (SMB auto-reconnect)
            if not self.auto_path or not self.auto_path.exists():
                self.auto_path = self._detect_auto_path()
                if self.auto_path:
                    self.log(f"AUTO path detected: {self.auto_path}", "SYSTEM", "SUCCESS")

            # Copy to master if AUTO path exists
            if not self.auto_path:
                self.log(f"[WARN] {project_code} XONG nhung KHONG CO AUTO PATH! Kiem tra ket noi may chu (Z:\\AUTO)", "SYSTEM", "WARN")
            if self.auto_path and project_code not in getattr(self, '_completed_projects', set()):
                self.log("=" * 60, "SYSTEM")
                self.log(f"PROJECT COMPLETED: {project_code}", "SYSTEM", "SUCCESS")
                self.log("=" * 60, "SYSTEM")

                # v1.0.94: STEP 1 - DỪNG tất cả workers + Kill Chrome
                self.log("Step 1: Stopping all workers and Chrome...", "SYSTEM")
                for wid in list(self.workers.keys()):
                    self.stop_worker(wid)
                self.kill_all_chrome()

                # v1.0.98: XÓA agent status files để Chrome 2 không follow project cũ
                self.log("Step 1.5: Clearing agent status files...", "SYSTEM")
                self._clear_agent_status()

                # v1.0.94: Tăng wait time lên 10s để Windows giải phóng file locks hoàn toàn
                self.log("Waiting 10s for file locks to release...", "SYSTEM")
                time.sleep(10)

                # v1.0.94: Force garbage collection để giải phóng Python file handles
                import gc
                gc.collect()

                # v1.0.94: STEP 2 - COPY sang máy chủ (không xóa local trong step này)
                copy_ok = False
                try:
                    self.log(f"Step 2: Copying {project_code} to master...", "SYSTEM")
                    self.copy_project_to_master(project_code, delete_after=False)
                    self.log(f"Copied {project_code} successfully", "SYSTEM", "SUCCESS")
                    copy_ok = True
                except Exception as e:
                    self.log(f"Failed to copy {project_code}: {e}", "SYSTEM", "ERROR")

                # v1.0.94: STEP 3 - XÓA local folder riêng biệt (sau khi copy xong)
                if copy_ok:
                    self.log("Step 3: Deleting local folder...", "SYSTEM")
                    # Đợi thêm 5s nữa để đảm bảo copy hoàn tất
                    time.sleep(5)
                    gc.collect()
                    self._delete_project_folder(project_code)

                # Mark as completed to avoid re-copying
                if not hasattr(self, '_completed_projects'):
                    self._completed_projects = set()
                self._completed_projects.add(project_code)

                # Reset project timer for next project
                self.project_start_time = None
                self.current_project_code = None

                # v1.0.363: STEP 4 - Restart workers (guard trong start_worker skip nếu đang chạy)
                self.log("Step 4: Restarting workers for next project...", "SYSTEM")
                for wid in list(self.workers.keys()):
                    w = self.workers[wid]
                    time.sleep(2)
                    self.start_worker(wid, gui_mode=self.gui_mode)
                    w.last_restart_time = datetime.now()
                    w.restart_count += 1

                # v1.0.94: STEP 5 - Verify deletion sau khi restart
                src_dir = TOOL_DIR / "PROJECTS" / project_code
                if src_dir.exists():
                    self.log(f"Warning: Local folder still exists, retry cleanup...", "SYSTEM", "WARN")
                    time.sleep(3)
                    self._delete_project_folder(project_code)

                self.log("Ready for next project", "SYSTEM", "SUCCESS")
            return

        if status.current_step == "excel":
            # v1.0.315: Chỉ tạo task excel MỚI nếu chưa có task pending/assigned
            existing_excel = [t for t in self.tasks.values()
                              if t.task_type == TaskType.EXCEL
                              and t.project_code == project_code
                              and t.status in (TaskStatus.PENDING, TaskStatus.ASSIGNED)]
            if not existing_excel:
                self.create_task(TaskType.EXCEL, project_code)
        elif status.current_step == "image" and status.images_missing:
            # v1.0.315: Chỉ tạo task image MỚI nếu chưa có task pending/assigned
            existing_img = [t for t in self.tasks.values()
                            if t.task_type == TaskType.IMAGE
                            and t.project_code == project_code
                            and t.status in (TaskStatus.PENDING, TaskStatus.ASSIGNED)]
            if not existing_img:
                self._create_excel_backup_if_needed(project_code)
                self._distribute_tasks(TaskType.IMAGE, project_code, status.images_missing)
        elif status.current_step == "video" and status.videos_needed:
            # v1.0.315: Chỉ tạo task video MỚI nếu chưa có task pending/assigned
            existing_vid = [t for t in self.tasks.values()
                            if t.task_type == TaskType.VIDEO
                            and t.project_code == project_code
                            and t.status in (TaskStatus.PENDING, TaskStatus.ASSIGNED)]
            if not existing_vid:
                self._distribute_tasks(TaskType.VIDEO, project_code, status.videos_needed)

    def _distribute_tasks(self, task_type: TaskType, project_code: str, scenes: List[int]):
        n = self.num_chrome_workers
        chunks = [[] for _ in range(n)]
        for i, scene in enumerate(sorted(scenes)):
            chunks[i % n].append(scene)
        for chunk in chunks:
            if chunk:
                self.create_task(task_type, project_code, chunk)

    # ================================================================================
    # WORKER CONTROL
    # ================================================================================

    def kill_chrome_by_worker(self, worker_id: str):
        """
        Kill CHỈ Chrome của 1 worker cụ thể (không kill worker khác).

        Args:
            worker_id: "chrome_1" hoặc "chrome_2"
        """
        if sys.platform != "win32":
            # Linux: khó phân biệt, dùng kill_all_chrome
            self.log(f"Linux: Cannot kill specific Chrome for {worker_id}, skipping", worker_id, "WARN")
            return

        self.log(f"Killing Chrome processes for {worker_id}...", worker_id, "WARN")

        # Xác định Chrome folder path dựa vào worker_id
        # Chrome path có dạng: "...\GoogleChromePortable\App\Chrome-bin\chrome.exe"
        # Chrome 2 có: "...\GoogleChromePortable - Copy\App\Chrome-bin\chrome.exe"
        if worker_id == "chrome_1":
            # Chrome 1: Chứa "GoogleChromePortable\App" NHƯNG KHÔNG chứa "- Copy"
            chrome_marker = "GoogleChromePortable\\App"
            exclude_marker = "GoogleChromePortable - Copy"
        else:  # chrome_2
            # Chrome 2: Chứa "GoogleChromePortable - Copy\App"
            chrome_marker = "GoogleChromePortable - Copy\\App"
            exclude_marker = None

        try:
            # v1.0.174: Tăng timeout wmic lên 15s và thêm fallback
            result = subprocess.run(
                ["wmic", "process", "where", "name='chrome.exe'", "get", "processid,commandline"],
                capture_output=True, text=True, timeout=15  # Tăng từ 5s lên 15s
            )

            killed_count = 0
            for line in result.stdout.split('\n'):
                # Check nếu command line chứa chrome_marker
                if chrome_marker in line:
                    # Nếu là Chrome 1, phải đảm bảo KHÔNG chứa "- Copy"
                    if exclude_marker and exclude_marker in line:
                        continue  # Skip Chrome 2

                    # Extract PID (số cuối cùng trong dòng)
                    parts = line.strip().split()
                    if parts:
                        pid = parts[-1]
                        if pid.isdigit():
                            from modules.process_killer import kill_pid
                            kill_pid(pid)
                            killed_count += 1

            self.log(f"Killed {killed_count} Chrome for {worker_id}", worker_id, "SUCCESS")

        except subprocess.TimeoutExpired:
            self.log(f"wmic timeout, killing ALL Chrome...", worker_id, "WARN")
            from modules.process_killer import kill_all_by_name
            kill_all_by_name("chrome.exe")
            self.log(f"Killed all Chrome (fallback)", worker_id, "WARN")
        except Exception as e:
            self.log(f"Error killing Chrome: {e}", worker_id, "WARN")

    def _clear_agent_status(self):
        """
        v1.0.98: Xóa agent status files khi reset.

        Chrome 2 đọc status của Chrome 1 để follow project.
        Nếu không xóa, Chrome 2 sẽ follow project cũ sau khi restart.
        """
        try:
            status_dir = TOOL_DIR / ".agent" / "status"
            if status_dir.exists():
                for f in status_dir.glob("*.json"):
                    try:
                        f.unlink()
                        self.log(f"  Cleared {f.name}", "SYSTEM")
                    except Exception as e:
                        self.log(f"  Cannot delete {f.name}: {e}", "SYSTEM", "WARN")
        except Exception as e:
            self.log(f"Error clearing agent status: {e}", "SYSTEM", "WARN")

    def _clear_chrome_data_for_new_account(self):
        """
        v1.0.105: Xóa Chrome data để đăng nhập tài khoản mới.

        Khi xoay vòng tài khoản, cần xóa data Chrome để:
        - Logout khỏi tài khoản cũ
        - Login tài khoản mới khi Chrome khởi động lại
        """
        try:
            import shutil

            # Chrome 1 data path
            chrome1_data = TOOL_DIR / "GoogleChromePortable" / "Data" / "profile"
            # Chrome 2 data path
            chrome2_data = TOOL_DIR / "GoogleChromePortable - Copy" / "Data" / "profile"

            for data_path in [chrome1_data, chrome2_data]:
                if data_path.exists():
                    # Xóa tất cả trừ "First Run" file
                    first_run = data_path / "First Run"
                    first_run_exists = first_run.exists()

                    for item in data_path.iterdir():
                        if item.name == "First Run":
                            continue
                        try:
                            if item.is_dir():
                                shutil.rmtree(item, ignore_errors=True)
                            else:
                                item.unlink()
                        except Exception as e:
                            self.log(f"Cannot delete {item.name}: {e}", "SYSTEM", "WARN")

                    # Tạo lại First Run nếu đã bị xóa
                    if not first_run.exists():
                        first_run.touch()

                    self.log(f"Cleared Chrome data: {data_path.parent.parent.name}", "SYSTEM")

        except Exception as e:
            self.log(f"Error clearing Chrome data: {e}", "SYSTEM", "WARN")

    def kill_all_chrome(self):
        """Kill TẤT CẢ Chrome + CMD windows khi tắt tool."""
        self.log("Killing all Chrome + CMD processes...", "SYSTEM")
        if sys.platform == "win32":
            # 1. Kill Chrome browsers
            from modules.process_killer import kill_all_by_name, kill_pid
            kill_all_by_name("chrome.exe")
            kill_all_by_name("GoogleChromePortable.exe")
            self.log("Killed Chrome processes", "SYSTEM")

            # 2. Force kill Python worker processes bằng WMIC
            try:
                result = subprocess.run(
                    ["wmic", "process", "where", "name='python.exe'", "get", "processid,commandline"],
                    capture_output=True, text=True, timeout=5
                )
                killed_count = 0
                for line in result.stdout.split('\n'):
                    if any(x in line for x in ['_run_chrome1', '_run_chrome2', 'run_excel_api', 'run_worker']):
                        # Extract PID and kill
                        parts = line.strip().split()
                        if parts:
                            pid = parts[-1]
                            if pid.isdigit():
                                kill_pid(pid)
                                killed_count += 1
                self.log(f"Killed {killed_count} Python worker processes", "SYSTEM")
            except Exception as e:
                self.log(f"Error killing Python processes: {e}", "SYSTEM", "WARN")

            # 3. Kill ALL CMD windows liên quan đến tool
            try:
                import ctypes
                from ctypes import wintypes

                user32 = ctypes.windll.user32
                killed_cmd = 0

                def enum_and_kill_cmd(hwnd, lParam):
                    nonlocal killed_cmd
                    if user32.IsWindowVisible(hwnd):
                        # Get window class name
                        class_name = ctypes.create_unicode_buffer(256)
                        user32.GetClassNameW(hwnd, class_name, 256)

                        # Check if it's a CMD/Console window
                        if class_name.value in ["ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"]:
                            # Get window title
                            length = user32.GetWindowTextLengthW(hwnd)
                            title = ""
                            if length > 0:
                                title_buf = ctypes.create_unicode_buffer(length + 1)
                                user32.GetWindowTextW(hwnd, title_buf, length + 1)
                                title = title_buf.value.upper()

                            # Kill CMD windows có title liên quan đến tool
                            keywords = ["EXCEL", "CHROME", "PYTHON", "_RUN_CHROME", "RUN_WORKER", "RUN_EXCEL"]
                            if any(kw in title for kw in keywords) or not title:
                                # Force close window
                                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                                killed_cmd += 1
                    return True

                WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
                user32.EnumWindows(WNDENUMPROC(enum_and_kill_cmd), 0)
                self.log(f"Closed {killed_cmd} CMD windows", "SYSTEM")
            except Exception as e:
                self.log(f"Error closing CMD windows: {e}", "SYSTEM", "WARN")

            # 4. Final cleanup - kill any remaining related processes
            time.sleep(0.5)
            try:
                # Kill cmd.exe spawned by our tool
                # v1.0.386: Bỏ taskkill by WINDOWTITLE - không cần thiết và hay crash
                pass
            except:
                pass

        else:
            subprocess.run(["pkill", "-f", "chrome"], capture_output=True)
            subprocess.run(["pkill", "-f", "_run_chrome"], capture_output=True)
            subprocess.run(["pkill", "-f", "run_excel_api"], capture_output=True)

        time.sleep(1)
        self.log("All processes killed", "SYSTEM")

    # ================================================================================
    # CHROME WINDOW MANAGEMENT (Hide/Show by moving off-screen)
    # ================================================================================

    def get_chrome_windows(self) -> List[int]:
        """Lấy danh sách handle của các cửa sổ Chrome (kể cả khi bị move off-screen)."""
        if sys.platform != "win32":
            return []

        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            chrome_windows = []

            def enum_windows_callback(hwnd, lParam):
                try:
                    # Check visible state (even if off-screen)
                    if user32.IsWindowVisible(hwnd):
                        # Get class name
                        class_name = ctypes.create_unicode_buffer(256)
                        user32.GetClassNameW(hwnd, class_name, 256)

                        # Chrome browser windows have class "Chrome_WidgetWin_*"
                        if class_name.value.startswith("Chrome_WidgetWin"):
                            skip = False

                            # Skip small windows: notifications, popups, dialogs (v1.0.268)
                            # Main browser window is always large (>= 400x300)
                            try:
                                from ctypes import wintypes as _wt
                                class _RECT(ctypes.Structure):
                                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
                                _rc = _RECT()
                                user32.GetWindowRect(hwnd, ctypes.byref(_rc))
                                _w = _rc.right - _rc.left
                                _h = _rc.bottom - _rc.top
                                if _w < 400 or _h < 300:
                                    skip = True  # Notification / popup nhỏ → bỏ qua
                            except Exception:
                                pass

                            # Skip non-GoogleChromePortable (VS Code, other Electron apps)
                            # v1.0.268: dung QueryFullProcessImageNameW (mo tin cay hon)
                            if not skip:
                                try:
                                    _pid = ctypes.c_ulong(0)
                                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(_pid))
                                    _ph = ctypes.windll.kernel32.OpenProcess(0x1000, False, _pid.value)
                                    if _ph:
                                        _buf = ctypes.create_unicode_buffer(1024)
                                        _sz = ctypes.c_uint32(1024)
                                        ctypes.windll.kernel32.QueryFullProcessImageNameW(_ph, 0, _buf, ctypes.byref(_sz))
                                        ctypes.windll.kernel32.CloseHandle(_ph)
                                        _exe = _buf.value.lower()
                                        if "googlechromeportable" not in _exe:
                                            skip = True  # VS Code / other Electron → skip
                                except Exception:
                                    pass

                            if not skip:
                                chrome_windows.append(hwnd)
                except:
                    pass  # Ignore errors in callback
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)

            return chrome_windows
        except Exception as e:
            self.log(f"Error getting Chrome windows: {e}", "CHROME", "ERROR")
            return []

    def hide_chrome_windows(self):
        """
        Ẩn các cửa sổ Chrome bằng cách di chuyển ra ngoài màn hình.
        Chrome vẫn chạy và có thể xử lý CAPTCHA.
        """
        if sys.platform != "win32":
            self.log("Window hiding only supported on Windows", "CHROME", "WARN")
            return False

        try:
            import ctypes
            user32 = ctypes.windll.user32

            chrome_windows = self.get_chrome_windows()
            if not chrome_windows:
                self.log("No Chrome windows found", "CHROME", "WARN")
                return False

            # Di chuyển tất cả cửa sổ Chrome ra ngoài màn hình (x = -3000)
            for hwnd in chrome_windows:
                # SWP_NOSIZE = 0x0001, SWP_NOZORDER = 0x0004
                user32.SetWindowPos(hwnd, 0, -3000, 100, 0, 0, 0x0001 | 0x0004)

            self.log(f"Hidden {len(chrome_windows)} Chrome windows (moved off-screen)", "CHROME", "SUCCESS")
            return True
        except Exception as e:
            self.log(f"Error hiding Chrome windows: {e}", "CHROME", "ERROR")
            return False

    def show_chrome_windows(self):
        """
        Hiện các cửa sổ Chrome - đặt bên phải màn hình, TO HƠN để dễ quan sát.
        Chrome 1: Phía trên bên phải
        Chrome 2: Phía dưới bên phải
        """
        if sys.platform != "win32":
            self.log("Window showing only supported on Windows", "CHROME", "WARN")
            return False

        try:
            import ctypes
            user32 = ctypes.windll.user32

            chrome_windows = self.get_chrome_windows()
            if not chrome_windows:
                self.log("No Chrome windows found", "CHROME", "WARN")
                return False

            # Get screen size
            screen_width = user32.GetSystemMetrics(0)  # SM_CXSCREEN
            screen_height = user32.GetSystemMetrics(1)  # SM_CYSCREEN

            # Chrome window size - Chia đều chiều cao cho 2 Chrome
            chrome_width = max(int(screen_width * 0.55), 1200)  # 55% màn hình, tối thiểu 1200

            # Mỗi Chrome chiếm 1/2 chiều cao màn hình, trừ đi khoảng cách
            gap = 20  # Khoảng cách giữa 2 Chrome và với viền màn hình
            chrome_height = (screen_height - gap * 3) // 2  # Chia đều cho 2 Chrome

            # Tối thiểu 600px
            chrome_height = max(chrome_height, 600)

            for i, hwnd in enumerate(chrome_windows):
                # Restore window if minimized
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE

                # Chiều rộng giống nhau, bên phải màn hình
                x = screen_width - chrome_width - 10

                if i == 0:
                    # Chrome 1 - Top half (nửa trên)
                    y = gap
                else:
                    # Chrome 2 - Bottom half (nửa dưới)
                    y = gap + chrome_height + gap

                # Use MoveWindow (works better than SetWindowPos)
                user32.MoveWindow(hwnd, x, y, chrome_width, chrome_height, True)

                # Bring to front
                user32.SetForegroundWindow(hwnd)

            self.log(f"Shown {len(chrome_windows)} Chrome windows (right side, large)", "CHROME", "SUCCESS")
            return True
        except Exception as e:
            self.log(f"Error showing Chrome windows: {e}", "CHROME", "ERROR")
            return False

    def get_cmd_windows(self) -> List[int]:
        """Lấy danh sách handle của TẤT CẢ cửa sổ CMD (Excel, Chrome 1, Chrome 2).

        v1.0.369: Tìm bằng PID (từ w.process) thay vì title.
        Python có thể thay đổi title CMD → tìm bằng title không match.
        """
        if sys.platform != "win32":
            return []

        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            # Collect PIDs from tracked workers
            worker_pids = {}  # pid → worker_id
            for wid, w in self.workers.items():
                if w.process and w.process.poll() is None:
                    worker_pids[w.process.pid] = wid

            if not worker_pids:
                return []

            # Find console windows owned by worker PIDs
            cmd_windows = []

            def enum_windows_callback(hwnd, lParam):
                if user32.IsWindowVisible(hwnd):
                    pid = ctypes.c_ulong(0)
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value in worker_pids:
                        wid = worker_pids[pid.value]
                        cmd_windows.append((hwnd, wid))
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)

            # Fallback: Tìm bằng title nếu PID không match
            # (PID có thể là cmd.exe → window thuộc child python.exe)
            if not cmd_windows:
                def enum_by_title(hwnd, lParam):
                    if user32.IsWindowVisible(hwnd):
                        length = user32.GetWindowTextLengthW(hwnd)
                        if length > 0:
                            title = ctypes.create_unicode_buffer(length + 1)
                            user32.GetWindowTextW(hwnd, title, length + 1)
                            title_upper = title.value.upper()
                            if any(x in title_upper for x in ["EXCEL", "CHROME 1", "CHROME 2", "CHROME1", "CHROME2",
                                                                "RUN_EXCEL", "_RUN_CHROME"]):
                                cmd_windows.append((hwnd, title.value))
                    return True
                user32.EnumWindows(WNDENUMPROC(enum_by_title), 0)

            # Sort: excel first, then chrome_1, then chrome_2
            def sort_key(x):
                wid = x[1]
                if "excel" in str(wid).lower():
                    return 0
                if "1" in str(wid):
                    return 1
                return 2
            cmd_windows.sort(key=sort_key)
            return [hwnd for hwnd, _ in cmd_windows]
        except Exception as e:
            self.log(f"Error getting CMD windows: {e}", "CHROME", "ERROR")
            return []

    def hide_cmd_windows(self):
        """Ẩn các cửa sổ CMD của Chrome workers."""
        if sys.platform != "win32":
            return False

        try:
            import ctypes
            user32 = ctypes.windll.user32

            cmd_windows = self.get_cmd_windows()
            for hwnd in cmd_windows:
                # Move off-screen
                user32.SetWindowPos(hwnd, 0, -3000, 100, 0, 0, 0x0001 | 0x0004)

            if cmd_windows:
                self.log(f"Hidden {len(cmd_windows)} CMD windows", "CHROME", "SUCCESS")
            return True
        except Exception as e:
            self.log(f"Error hiding CMD windows: {e}", "CHROME", "ERROR")
            return False

    def show_cmd_windows(self):
        """Hiện các cửa sổ CMD ở giữa màn hình, xếp chồng nhau."""
        if sys.platform != "win32":
            return False

        try:
            import ctypes
            user32 = ctypes.windll.user32

            cmd_windows = self.get_cmd_windows()
            if not cmd_windows:
                return False

            # Get screen size
            screen_width = user32.GetSystemMetrics(0)
            screen_height = user32.GetSystemMetrics(1)

            # CMD window size - nhỏ hơn Chrome
            cmd_width = 800
            cmd_height = 600

            # Position ở giữa màn hình, xếp chồng với offset
            x_start = (screen_width - cmd_width) // 2
            y_start = (screen_height - cmd_height) // 2

            for i, hwnd in enumerate(cmd_windows):
                # Restore if minimized
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE

                # Xếp chồng với offset nhỏ để dễ phân biệt
                x = x_start + (i * 30)
                y = y_start + (i * 30)

                # Move to position
                user32.MoveWindow(hwnd, x, y, cmd_width, cmd_height, True)

            self.log(f"Shown {len(cmd_windows)} CMD windows (center)", "CHROME", "SUCCESS")
            return True
        except Exception as e:
            self.log(f"Error showing CMD windows: {e}", "CHROME", "ERROR")
            return False

    def arrange_all_windows(self, tool_x=0, tool_y=0, tool_w=700, tool_h=450):
        """
        Layout A: Tool + CMDs cot trai, Chrome 1+2 cot phai.

        [VE3 Tool  ] [Chrome 1 - nua tren]
        [CMD Excel ] [                   ]
        [CMD Chr1  ] [Chrome 2 - nua duoi]
        [CMD Chr2  ] [                   ]
        """
        if sys.platform != "win32":
            return False

        try:
            import ctypes
            user32 = ctypes.windll.user32

            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)

            right_x = tool_x + tool_w
            right_w = screen_w - right_x

            # Chrome 1 (nua tren phai), Chrome 2 (nua duoi phai)
            chrome_h = screen_h // 2
            chrome_windows = self.get_chrome_windows()

            # v1.0.269: Pick exactly 1 Chrome1 + 1 Chrome2 from potentially many windows
            # (old chrome.exe processes may linger after restart, causing duplicates)
            def _get_exe(hwnd):
                try:
                    _k32 = ctypes.windll.kernel32
                    _pid = ctypes.c_ulong(0)
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(_pid))
                    _h = _k32.OpenProcess(0x1000, False, _pid.value)
                    if _h:
                        try:
                            _buf = ctypes.create_unicode_buffer(1024)
                            _sz = ctypes.c_uint32(1024)
                            _k32.QueryFullProcessImageNameW(_h, 0, _buf, ctypes.byref(_sz))
                            return _buf.value.lower()
                        finally:
                            _k32.CloseHandle(_h)
                except Exception:
                    pass
                return ""

            chrome1_wins = [w for w in chrome_windows if ' - copy' not in _get_exe(w)]
            chrome2_wins = [w for w in chrome_windows if ' - copy' in _get_exe(w)]
            # EnumWindows returns Z-order (topmost first) → [0] = most recently active window
            to_arrange = []
            if chrome1_wins:
                to_arrange.append((0, chrome1_wins[0]))   # Chrome 1 → top (y=0)
            if chrome2_wins:
                to_arrange.append((1, chrome2_wins[0]))   # Chrome 2 → bottom (y=chrome_h)

            for slot, hwnd in to_arrange:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.MoveWindow(hwnd, right_x, slot * chrome_h, right_w, chrome_h, True)

            # CMDs xep doc duoi tool (trai)
            cmd_windows = self.get_cmd_windows()
            n = len(cmd_windows)
            if n > 0:
                cmd_area_h = screen_h - (tool_y + tool_h)
                cmd_h = max(cmd_area_h // n, 120)
                for i, hwnd in enumerate(cmd_windows):
                    user32.ShowWindow(hwnd, 9)
                    y = tool_y + tool_h + i * cmd_h
                    user32.MoveWindow(hwnd, tool_x, y, tool_w, cmd_h, True)

            self.log(
                f"Arranged: tool({tool_w}x{tool_h}) | "
                f"{n} CMDs | {len(chrome_windows)} Chrome",
                "CHROME", "SUCCESS"
            )
            return True
        except Exception as e:
            self.log(f"arrange_all_windows error: {e}", "CHROME", "ERROR")
            return False

    def show_chrome_with_cmd(self):
        """
        Show TẤT CẢ windows (CMD, Chrome browsers).
        Layout: 1 hàng ngang trên cùng màn hình

        [Excel CMD] [Chrome 1] [Chrome 1 CMD] [Chrome 2] [Chrome 2 CMD]
        """
        if sys.platform != "win32":
            self.log("Window showing only supported on Windows", "CHROME", "WARN")
            return False

        try:
            import ctypes
            user32 = ctypes.windll.user32

            chrome_windows = self.get_chrome_windows()
            cmd_windows = self.get_cmd_windows()  # [Excel, Chrome1 CMD, Chrome2 CMD]

            # Get screen size
            screen_width = user32.GetSystemMetrics(0)
            screen_height = user32.GetSystemMetrics(1)

            # Kich thuoc - Chrome TO HON de de quan sat
            cmd_width = 350
            cmd_height = 450
            chrome_width = 550  # TO HON
            chrome_height = 450

            gap = 5
            y_top = 20  # Tat ca tren cung 1 hang

            # Layout: [Excel CMD] [Chrome1] [Chrome1 CMD] [Chrome2] [Chrome2 CMD]
            x = 10

            # 1. Excel CMD (nho, goc trai)
            if len(cmd_windows) >= 1:
                user32.SetWindowPos(cmd_windows[0], 0, x, y_top, cmd_width, cmd_height, 0x0004)
                user32.ShowWindow(cmd_windows[0], 9)
                x += cmd_width + gap

            # 2. Chrome 1 (to, de xem)
            if len(chrome_windows) >= 1:
                user32.SetWindowPos(chrome_windows[0], 0, x, y_top, chrome_width, chrome_height, 0x0004)
                user32.ShowWindow(chrome_windows[0], 9)
                x += chrome_width + gap

            # 3. Chrome 1 CMD (nho)
            if len(cmd_windows) >= 2:
                user32.SetWindowPos(cmd_windows[1], 0, x, y_top, cmd_width, cmd_height, 0x0004)
                user32.ShowWindow(cmd_windows[1], 9)
                x += cmd_width + gap

            # 4. Chrome 2 (to, de xem)
            if len(chrome_windows) >= 2:
                user32.SetWindowPos(chrome_windows[1], 0, x, y_top, chrome_width, chrome_height, 0x0004)
                user32.ShowWindow(chrome_windows[1], 9)
                x += chrome_width + gap

            # 5. Chrome 2 CMD (nho)
            if len(cmd_windows) >= 3:
                user32.SetWindowPos(cmd_windows[2], 0, x, y_top, cmd_width, cmd_height, 0x0004)
                user32.ShowWindow(cmd_windows[2], 9)

            self.log(f"Shown {len(cmd_windows)} CMD + {len(chrome_windows)} Chrome (1 row)", "CHROME", "SUCCESS")
            return True
        except Exception as e:
            self.log(f"Error showing Chrome with CMD: {e}", "CHROME", "ERROR")
            return False

    def toggle_chrome_visibility(self) -> bool:
        """
        Toggle hiển thị Chrome windows.
        Returns: True nếu đang hiển thị, False nếu đang ẩn
        """
        if sys.platform != "win32":
            return True

        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32

            chrome_windows = self.get_chrome_windows()
            if not chrome_windows:
                return True

            # Kiểm tra vị trí hiện tại của cửa sổ đầu tiên
            rect = wintypes.RECT()
            user32.GetWindowRect(chrome_windows[0], ctypes.byref(rect))

            # Nếu x < 0 (đang ẩn), thì show
            if rect.left < 0:
                self.show_chrome_windows()
                return True
            else:
                self.hide_chrome_windows()
                return False
        except Exception:
            return True

    def start_worker(self, worker_id: str, gui_mode: bool = False) -> bool:
        """
        Start a worker process.

        Args:
            worker_id: ID of worker to start
            gui_mode: If True, start with minimized CMD window but Chrome visible
        """
        if worker_id not in self.workers:
            return False
        w = self.workers[worker_id]
        if w.process and w.process.poll() is None:
            return True

        self.log(f"Starting {worker_id}...", worker_id)
        try:
            if w.worker_type == "excel":
                script = TOOL_DIR / "run_excel_api.py"
                args = "--loop"  # Excel worker chạy loop liên tục
            else:
                script = TOOL_DIR / f"_run_chrome{w.worker_num}.py"
                args = ""  # Chrome workers chạy bình thường

            if not script.exists():
                # Fallback nếu không có script riêng
                self.log(f"Script not found: {script.name}", worker_id, "ERROR")
                w.status = WorkerStatus.ERROR
                return False

            # Ensure logs directory exists
            log_dir = LOGS_DIR
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f"{worker_id}.log"

            # Clear old log file for fresh start
            if log_file.exists():
                try:
                    with open(log_file, 'w', encoding='utf-8') as f:
                        f.write(f"[{datetime.now().strftime('%H:%M:%S')}] Worker {worker_id} started (fresh session)\n")
                except:
                    pass  # Ignore if can't clear

            if sys.platform == "win32":
                # v1.0.363: Dùng CREATE_NEW_CONSOLE + cmd /k thay vì "start" command
                # "start" tạo process tách rời → w.process không track được CMD thật
                # → poll() luôn trả 0 → guard fail → double CMD hoặc mất CMD
                # cmd /k + CREATE_NEW_CONSOLE: w.process track đúng cmd.exe process
                title = f"{w.worker_type.upper()} {w.worker_num or ''}".strip()
                cmd_args = f"python -X utf8 {script.name}"
                if args:
                    cmd_args += f" {args}"

                # Prepare environment with UTF-8 encoding for subprocess
                worker_env = os.environ.copy()
                worker_env['PYTHONIOENCODING'] = 'utf-8'
                worker_env['PYTHONUTF8'] = '1'

                # v1.0.368: cmd /c "title ... && ..." KHÔNG dùng shell=True
                # shell=True + CREATE_NEW_CONSOLE = cmd lồng cmd → cửa sổ không hiện
                cmd_str = f'title {title} && chcp 65001 >nul && cd /d {TOOL_DIR} && {cmd_args}'
                w.process = subprocess.Popen(
                    ['cmd', '/k', cmd_str],
                    cwd=str(TOOL_DIR),
                    env=worker_env,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )

                self.log(f"CMD created: PID={w.process.pid}, cmd=[cmd /k {cmd_str[:60]}...]", worker_id, "INFO")
            else:
                # Linux/Mac
                worker_env = os.environ.copy()
                worker_env['PYTHONIOENCODING'] = 'utf-8'
                worker_env['PYTHONUTF8'] = '1'
                cmd_list = [sys.executable, '-X', 'utf8', str(script)]
                if args:
                    cmd_list.extend(args.split())
                w.process = subprocess.Popen(cmd_list, cwd=str(TOOL_DIR), env=worker_env)

            w.status = WorkerStatus.IDLE
            w.start_time = datetime.now()
            self.log(f"{worker_id} started", worker_id, "SUCCESS")
            return True
        except Exception as e:
            self.log(f"Failed: {e}", worker_id, "ERROR")
            import traceback
            self.log(f"Traceback: {traceback.format_exc()}", worker_id, "ERROR")
            w.status = WorkerStatus.ERROR
            w.last_error = str(e)
            return False

    def stop_worker(self, worker_id: str):
        if worker_id not in self.workers:
            return
        w = self.workers[worker_id]
        if w.process:
            pid = w.process.pid
            try:
                # v1.0.367: Kill TOÀN BỘ process tree (cmd.exe + python con)
                # terminate() chỉ kill cmd.exe, python con chạy tiếp thành orphan
                if sys.platform == "win32":
                    from modules.process_killer import kill_pid_tree
                    kill_pid_tree(pid)
                else:
                    w.process.terminate()
                w.process.wait(timeout=5)
            except:
                try:
                    w.process.kill()
                except:
                    pass
            w.process = None

        # v1.0.367: Kill python processes chạy script tương ứng (phòng orphan)
        if sys.platform == "win32":
            try:
                script_name = ""
                if w.worker_type == "excel":
                    script_name = "run_excel_api.py"
                elif w.worker_type == "chrome" and w.worker_num:
                    script_name = f"_run_chrome{w.worker_num}.py"
                if script_name:
                    subprocess.run(
                        f'wmic process where "commandline like \'%{script_name}%\'" call terminate',
                        shell=True, capture_output=True, timeout=10
                    )
            except:
                pass

        # Close log handle if exists (hidden mode)
        if hasattr(w, '_log_handle') and w._log_handle:
            try:
                w._log_handle.close()
            except:
                pass
            w._log_handle = None
        w.status = WorkerStatus.STOPPED
        w.current_task = None

    def restart_worker(self, worker_id: str):
        self.log(f"Restarting {worker_id}...", worker_id, "WARN")
        self.stop_worker(worker_id)

        w = self.workers[worker_id]
        if w.worker_type == "chrome":
            self.kill_chrome_by_worker(worker_id)  # ✅ CHỈ kill worker này!

        time.sleep(3)
        self.start_worker(worker_id, gui_mode=self.gui_mode)

        # Track restart time và count
        w.last_restart_time = datetime.now()
        w.restart_count += 1
        self.log(f"{worker_id} restarted (count: {w.restart_count})", worker_id, "SUCCESS")

    def auto_restart_chrome_workers(self):
        """Tự động restart Chrome workers mỗi 1 tiếng để tránh lỗi."""
        self.log("=" * 60, "SYSTEM")
        self.log("AUTO-RESTART CHROME WORKERS (1 TIẾNG)", "SYSTEM")
        self.log("=" * 60, "SYSTEM")

        # 1. Stop tất cả Chrome workers
        chrome_workers = [wid for wid in self.workers if wid.startswith("chrome_")]
        for wid in chrome_workers:
            self.log(f"Stopping {wid}...", wid)
            self.stop_worker(wid)

        # 2. Kill all Chrome processes
        self.log("Killing all Chrome processes...", "SYSTEM")
        self.kill_all_chrome()
        time.sleep(2)

        # 3. Restart Chrome workers
        for wid in chrome_workers:
            self.log(f"Starting {wid}...", wid)
            time.sleep(2)
            self.start_worker(wid, gui_mode=self.gui_mode)

            # Track restart
            w = self.workers[wid]
            w.last_restart_time = datetime.now()
            w.restart_count += 1

        self.log("Chrome workers restarted successfully", "SYSTEM", "SUCCESS")

    def _create_excel_backup_if_needed(self, project_code: str):
        """v1.0.234: Tạo backup Excel khi bắt đầu bước image (chỉ tạo 1 lần)."""
        excel_path = TOOL_DIR / "PROJECTS" / project_code / f"{project_code}_prompts.xlsx"
        backup_path = TOOL_DIR / "PROJECTS" / project_code / f"{project_code}_prompts_backup.xlsx"
        if excel_path.exists() and not backup_path.exists():
            try:
                shutil.copy2(str(excel_path), str(backup_path))
                self.log(f"[Backup] Created Excel backup: {backup_path.name}", "SYSTEM")
            except Exception as e:
                self.log(f"[Backup] Error creating backup: {e}", "SYSTEM", "WARN")

    def _restore_excel_from_backup(self, project_code: str):
        """v1.0.262: Restore Excel từ backup dùng os.replace() - ATOMIC, tránh mất Excel nếu copy fail."""
        excel_path = TOOL_DIR / "PROJECTS" / project_code / f"{project_code}_prompts.xlsx"
        backup_path = TOOL_DIR / "PROJECTS" / project_code / f"{project_code}_prompts_backup.xlsx"
        if backup_path.exists():
            try:
                # Copy backup → temp trước, KHÔNG xóa Excel gốc
                temp_path = excel_path.parent / f"{excel_path.stem}_restore_temp.xlsx"
                shutil.copy2(str(backup_path), str(temp_path))
                # Atomic replace: swap temp → excel (nếu fail, Excel gốc vẫn còn)
                import os
                os.replace(str(temp_path), str(excel_path))
                self.log(f"[Backup] Restored Excel from backup - clean slate", "SYSTEM", "SUCCESS")
            except Exception as e:
                self.log(f"[Backup] Error restoring backup: {e}", "SYSTEM", "ERROR")
                # Dọn temp nếu còn
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except Exception:
                    pass
        else:
            self.log(f"[Backup] No backup found for {project_code}", "SYSTEM", "WARN")

    def handle_account_issue(self, project_code: str):
        """
        v1.0.234: Xử lý khi account có vấn đề (1 tiếng < 5 ảnh).

        Flow:
        1. Đọc account từ Excel (giữ lại để sau restore vẫn dùng đúng account)
        2. Restore Excel backup (trạng thái sạch, giữ nguyên prompts)
        3. Ghi lại account vào Excel sau restore (tránh PRE-LOGIN rotate sai)
        4. Xóa ảnh đã tạo (< 5, từ account xấu)
        5. Restart workers
        """
        self.log("=" * 60, "SYSTEM")
        self.log(f"ACCOUNT ISSUE: {project_code} - 1h < 5 ảnh → Reset + retry", "SYSTEM", "WARN")
        self.log("=" * 60, "SYSTEM")

        # 1. v1.0.362: Đọc account từ _CLAIMED (không dùng Excel nữa)
        excel_path = TOOL_DIR / "PROJECTS" / project_code / f"{project_code}_prompts.xlsx"
        saved_account_info = None
        try:
            claimed_path = TOOL_DIR / "PROJECTS" / project_code / "_CLAIMED"
            if claimed_path.exists():
                claimed_lines = claimed_path.read_text(encoding='utf-8').strip().split('\n')
                if len(claimed_lines) >= 4 and claimed_lines[3].strip():
                    parts = claimed_lines[3].strip().split('|')
                    if len(parts) >= 2:
                        saved_account_info = {
                            'email': parts[0].strip(),
                            'index': 0,
                            'channel': '',
                        }
                        self.log(f"[Account] _CLAIMED account: {saved_account_info['email']}", "SYSTEM")
        except Exception as e:
            self.log(f"[Account] Không đọc được _CLAIMED: {e}", "SYSTEM", "WARN")

        # 2. Restore Excel backup (về trạng thái sạch, giữ nguyên prompts)
        self._restore_excel_from_backup(project_code)

        # 3. Ghi lại account - v1.0.264: lưu .account.json TRƯỚC (không phụ thuộc Excel)
        if saved_account_info and saved_account_info.get('email'):
            try:
                from google_login import save_project_account_json, save_account_to_excel, get_project_account_json
                project_dir_acc = TOOL_DIR / "PROJECTS" / project_code
                # v1.0.267: Chỉ ghi .account.json khi chưa tồn tại
                _existing_json = get_project_account_json(project_dir_acc)
                if not _existing_json.get('email'):
                    save_project_account_json(
                        project_dir_acc,
                        saved_account_info.get('channel', ''),
                        saved_account_info.get('index', 0),
                        saved_account_info.get('email', '')
                    )
                    self.log(f"[Account] Đã ghi .account.json: {saved_account_info.get('email')}", "SYSTEM")
                else:
                    self.log(f"[Account] .account.json da co ({_existing_json.get('email')}) → giu nguyen", "SYSTEM")
                # Ghi thêm vào Excel (secondary, retry 3 lần)
                if excel_path.exists():
                    for _attempt in range(3):
                        try:
                            save_account_to_excel(
                                str(excel_path),
                                saved_account_info.get('channel', ''),
                                saved_account_info.get('index', 0),
                                saved_account_info.get('email', '')
                            )
                            break
                        except Exception:
                            if _attempt < 2:
                                time.sleep(2)
            except Exception as e:
                self.log(f"[Account] Lỗi ghi account: {e}", "SYSTEM", "ERROR")

        # 3. Xóa ảnh đã tạo (< 5 ảnh)
        img_dir = TOOL_DIR / "PROJECTS" / project_code / "img"
        if img_dir.exists():
            for f in list(img_dir.glob("*.png")) + list(img_dir.glob("*.jpg")):
                try:
                    f.unlink()
                except Exception:
                    pass
            self.log("[Account] Cleared img/", "SYSTEM")

        # 4. Reset workers - KHÔNG dùng stop_all() vì sẽ kill orchestrate thread!
        # Chỉ stop từng worker riêng lẻ, giữ _stop_flag = False
        for wid in list(self.workers.keys()):
            self.stop_worker(wid)
        time.sleep(2)
        self.kill_all_chrome()
        self._clear_agent_status()
        time.sleep(2)
        for wid in list(self.workers.keys()):
            time.sleep(2)
            self.start_worker(wid, gui_mode=self.gui_mode)

        # 5. Reset account timer (account mới có 1 tiếng)
        self.account_start_time = time.time()

        self.log("Reset xong - cùng project, tài khoản mới", "SYSTEM", "SUCCESS")

    def handle_project_timeout(self, project_code: str):
        """Xử lý khi project quá 6 tiếng: Copy kết quả về máy chủ và chuyển project tiếp theo."""
        self.log("=" * 60, "SYSTEM")
        self.log(f"PROJECT TIMEOUT: {project_code} (6 TIẾNG)", "SYSTEM", "WARN")
        self.log("=" * 60, "SYSTEM")

        # 1. Stop tất cả workers
        self.log("Stopping all workers...", "SYSTEM")
        for wid in list(self.workers.keys()):
            self.stop_worker(wid)

        # v1.0.98: Clear agent status files
        self.log("Clearing agent status files...", "SYSTEM")
        self._clear_agent_status()

        # 2. Copy kết quả về máy chủ (nếu có AUTO path VÀ có ảnh)
        # v1.0.74: Chỉ copy nếu có ảnh - tránh copy project rỗng
        if self.auto_path:
            project_dir = TOOL_DIR / "PROJECTS" / project_code
            img_dir = project_dir / "img"
            img_count = 0
            if img_dir.exists():
                img_count = len(list(img_dir.glob("*.png"))) + len(list(img_dir.glob("*.jpg")))

            if img_count > 0:
                try:
                    self.log(f"Có {img_count} ảnh - Copying {project_code} to master...", "SYSTEM")
                    self.copy_project_to_master(project_code)
                    self.log(f"Copied {project_code} successfully", "SYSTEM", "SUCCESS")
                except Exception as e:
                    self.log(f"Failed to copy {project_code}: {e}", "SYSTEM", "ERROR")
            else:
                self.log(f"KHÔNG có ảnh - Bỏ qua copy {project_code}", "SYSTEM", "WARN")

        # 3. Mark project as completed (timeout)
        if project_code in self.project_tasks:
            for task_id in self.project_tasks[project_code]:
                if task_id in self.tasks:
                    task = self.tasks[task_id]
                    if task.status != TaskStatus.COMPLETED:
                        task.status = TaskStatus.COMPLETED
                        task.error = "TIMEOUT_6H"

        # 4. Kill all Chrome
        self.log("Killing all Chrome processes...", "SYSTEM")
        self.kill_all_chrome()
        time.sleep(2)

        # 5. v1.0.363: Restart workers (guard trong start_worker skip nếu đang chạy)
        for wid in list(self.workers.keys()):
            self.log(f"Restarting {wid}...", wid)
            time.sleep(2)
            self.start_worker(wid, gui_mode=self.gui_mode)

        # 6. Reset project timer
        self.project_start_time = None
        self.current_project_code = None

        self.log("Ready for next project", "SYSTEM", "SUCCESS")

    def check_and_auto_recover(self) -> bool:
        """Check for connection errors and auto-recover if needed.

        Returns True if recovery was triggered.
        """
        # Check each Chrome worker's recent logs for connection errors
        # v1.0.173: Giảm từ 5 xuống 2 để reset nhanh hơn khi bị disconnect
        error_threshold = 2  # Number of consecutive errors to trigger recovery

        for worker_id in self.workers:
            if not worker_id.startswith("chrome_"):
                continue

            logs = self.get_worker_log_file(worker_id, lines=20)
            if not logs:
                continue

            # Count recent connection errors
            # Detect both English and Chinese error messages
            connection_errors = 0
            for line in logs[-10:]:  # Check last 10 lines
                line_lower = line.lower()
                # English: "connection disconnected/lost"
                if "connection" in line_lower and ("disconnected" in line_lower or "lost" in line_lower):
                    connection_errors += 1
                # Chinese: "与页面的连接已断开" (DrissionPage error)
                elif "与页面的连接已断开" in line:
                    connection_errors += 1
                # Also detect consecutive RETRY errors
                elif "RETRY" in line and "error" in line_lower:
                    connection_errors += 1

            if connection_errors >= error_threshold:
                self.log(f"[AUTO-RECOVERY] Detected {connection_errors} connection errors in {worker_id}", "SYSTEM", "WARN")
                self.log("[AUTO-RECOVERY] Killing all Chrome and restarting workers...", "SYSTEM", "WARN")

                # Kill all Chrome
                self.kill_all_chrome()
                time.sleep(2)

                # Restart all Chrome workers
                for wid in list(self.workers.keys()):
                    if wid.startswith("chrome_"):
                        self.stop_worker(wid)

                time.sleep(3)

                for wid in list(self.workers.keys()):
                    if wid.startswith("chrome_"):
                        self.start_worker(wid, gui_mode=self.gui_mode)
                        time.sleep(2)

                self.log("[AUTO-RECOVERY] Chrome workers restarted!", "SYSTEM", "SUCCESS")
                return True

        return False

    def restart_all_chrome(self):
        """Restart all Chrome workers (kill Chrome first)."""
        self.log("Restarting all Chrome workers...", "SYSTEM", "WARN")

        # Kill all Chrome processes
        self.kill_all_chrome()
        time.sleep(2)

        # Stop all Chrome workers
        for wid in list(self.workers.keys()):
            if wid.startswith("chrome_"):
                self.stop_worker(wid)

        time.sleep(3)

        # Start all Chrome workers
        for wid in list(self.workers.keys()):
            if wid.startswith("chrome_"):
                self.start_worker(wid, gui_mode=self.gui_mode)
                time.sleep(2)

        self.log("All Chrome workers restarted!", "SYSTEM", "SUCCESS")

    def start_all(self, gui_mode: bool = False):
        """Start all workers + orchestration (giống GUI _start()).

        Args:
            gui_mode: If True, minimize CMD windows and log to files (for GUI mode)
        """
        self.gui_mode = gui_mode  # Track mode for restart
        self._start_time = time.time()
        self.chrome_last_restart = time.time()  # Reset timer tránh auto-restart
        self._stop_flag = False

        self.kill_all_chrome()
        if self.enable_excel:
            self.start_worker("excel", gui_mode=gui_mode)
            time.sleep(2)
        for i in range(1, self.num_chrome_workers + 1):
            self.start_worker(f"chrome_{i}", gui_mode=gui_mode)
            time.sleep(2)

        # Start orchestration thread (scan projects, health check, etc.)
        if self._orch_thread is None or not self._orch_thread.is_alive():
            self._orch_thread = threading.Thread(target=self.orchestrate, daemon=True)
            self._orch_thread.start()

        # Start watchdog (report status + read commands from master)
        self.start_watchdog()

    def stop_all(self):
        self._stop_flag = True
        for wid in list(self.workers.keys()):
            self.stop_worker(wid)
        self.kill_all_chrome()

    # ================================================================================
    # WATCHDOG - Report status + read commands from master
    # ================================================================================

    def start_watchdog(self):
        """Start watchdog thread (gửi status + đọc commands từ master)."""
        if not self.auto_path:
            self.log("No AUTO path - watchdog disabled", "MANAGER", "WARN")
            return
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.start()
        self.log(f"Watchdog started (VM_ID={self._vm_id})", "MANAGER")

    def _watchdog_loop(self):
        """Loop: mỗi 10s gửi status + check commands.
        v1.0.339: Watchdog LUÔN chạy (kể cả khi _stop_flag=True)
        để VM vẫn nhận lệnh RUN từ master khi đang stopped.
        """
        while True:
            try:
                self._report_status_to_master()
                self._check_master_commands()
                self._sync_master_settings()
            except Exception as e:
                self.log(f"Watchdog error: {e}", "MANAGER", "ERROR")
            time.sleep(10)

    def _report_status_to_master(self):
        """Ghi status JSON lên AUTO/status/{VM_ID}.json."""
        if not self.auto_path:
            return
        try:
            status_dir = self.auto_path / "ve3-tool-simple" / "control" / "status"
            status_dir.mkdir(parents=True, exist_ok=True)
            status_file = status_dir / f"{self._vm_id}.json"

            # Tính uptime
            uptime_min = 0
            if hasattr(self, '_start_time'):
                uptime_min = int((time.time() - self._start_time) / 60)

            # Worker states
            worker_states = {}
            for wid, w in self.workers.items():
                worker_states[wid] = {
                    "status": w.status.value if hasattr(w.status, 'value') else str(w.status),
                    "current_task": w.current_task or "",
                }

            # Determine overall state
            running_workers = sum(1 for w in self.workers.values()
                                  if w.process and w.process.poll() is None)
            if self._stop_flag:
                state = "stopped"
            elif running_workers > 0:
                state = f"running ({running_workers} workers)"
            else:
                state = "idle"

            # Read version
            version = ""
            try:
                version = (TOOL_DIR / "VERSION.txt").read_text(encoding='utf-8').split('\n')[0].strip()
            except Exception:
                pass

            # v1.0.366: Thêm tiến độ project cho master theo dõi
            project_code = self.current_project_code or ""
            project_elapsed_min = 0
            images_done = 0
            total_scenes = 0
            excel_step = ""

            # Nếu chưa có current_project_code → scan PROJECTS folder
            if not project_code:
                try:
                    projects_dir = TOOL_DIR / "PROJECTS"
                    if projects_dir.exists():
                        for item in projects_dir.iterdir():
                            if item.is_dir() and (item / "_CLAIMED").exists():
                                project_code = item.name
                                break
                except Exception:
                    pass

            if project_code:
                # Thời gian chạy project hiện tại
                if self.project_start_time:
                    project_elapsed_min = int((time.time() - self.project_start_time) / 60)

                # Tiến độ ảnh + Excel step
                try:
                    pstatus = self.quality_checker.get_project_status(project_code)
                    images_done = pstatus.images_done
                    total_scenes = pstatus.total_scenes
                    excel_step = pstatus.current_step or ""
                except Exception:
                    # Fallback: đếm ảnh trực tiếp
                    try:
                        img_dir = TOOL_DIR / "PROJECTS" / project_code / "img"
                        if img_dir.exists():
                            images_done = len(list(img_dir.glob("scene_*.png")))
                    except Exception:
                        pass

            # v1.0.372: Thêm completed_today + last_image_time cho master control
            completed_today = len(getattr(self, '_completed_projects', set()))

            last_image_time = ""
            if project_code:
                try:
                    img_dir = TOOL_DIR / "PROJECTS" / project_code / "img"
                    if img_dir.exists():
                        newest = 0
                        for f in img_dir.glob("scene_*.png"):
                            mt = f.stat().st_mtime
                            if mt > newest:
                                newest = mt
                        if newest > 0:
                            last_image_time = datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            data = {
                "channel": self._vm_id,
                "state": state,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "version": version,
                "uptime_minutes": uptime_min,
                "project": project_code,
                "project_elapsed_minutes": project_elapsed_min,
                "images_done": images_done,
                "total_scenes": total_scenes,
                "excel_step": excel_step,
                "completed_today": completed_today,
                "last_image_time": last_image_time,
                "workers": worker_states,
            }

            status_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass  # Silent fail - network có thể mất tạm thời

    def _sync_master_settings(self):
        """v1.0.592: Sync settings tu master (control/settings/) vao local."""
        if not self._settings_sync or not self.auto_path:
            return
        try:
            changes = self._settings_sync.check_and_sync(
                auto_path=self.auto_path,
                min_interval=60,  # Check moi 60s
            )
            if changes:
                # Reload local settings vao memory
                self.settings.config = self.settings._load_config()
                self.log(f"[SYNC] Da cap nhat {len(changes)} settings tu master", "SYSTEM", "SUCCESS")
        except Exception as e:
            pass  # Silent fail

    def _check_master_commands(self):
        """Đọc commands từ AUTO/commands/{VM_ID}.* và thực thi."""
        if not self.auto_path:
            return
        try:
            cmd_dir = self.auto_path / "ve3-tool-simple" / "control" / "commands"
            cmd_dir.mkdir(parents=True, exist_ok=True)

            for cmd_file in cmd_dir.glob(f"{self._vm_id}.*"):
                # Skip ACK files
                if cmd_file.name.endswith('.ack'):
                    continue
                cmd_name = cmd_file.suffix.lstrip('.')  # e.g., "run", "stop", "update"
                self.log(f"Master command: {cmd_name}", "MANAGER", "INFO")

                try:
                    if cmd_name == "run":
                        # v1.0.367: Ưu tiên GUI callback (để arrange cửa sổ)
                        # Nếu không có GUI → start trực tiếp
                        if self._gui_start_callback:
                            self.log("Master RUN → GUI BẮT ĐẦU", "MANAGER", "INFO")
                            self._gui_start_callback()
                        else:
                            self._stop_flag = False
                            self.start_all(gui_mode=self.gui_mode)
                        self._ack_command(cmd_name, "OK")
                    elif cmd_name == "stop":
                        # v1.0.367: Gọi stop_all() trực tiếp (không qua GUI callback)
                        # GUI callback chỉ schedule trên Tkinter thread → không stop ngay
                        self.log("Master STOP → Dừng tất cả workers", "MANAGER", "INFO")
                        self.stop_all()
                        # Cập nhật GUI nếu có
                        if self._gui_stop_callback:
                            self._gui_stop_callback()
                        self._ack_command(cmd_name, "OK")
                    elif cmd_name == "update":
                        self._ack_command(cmd_name, "STARTED")
                        self._do_git_update()
                        # ACK OK trước khi os.execv trong _do_git_update
                        # (nếu đến đây = execv không chạy → fallback restart)
                        self._ack_command(cmd_name, "OK")
                    elif cmd_name == "done":
                        # v1.0.364: Master ấn DONE → ép hoàn thành project hiện tại
                        if self.current_project_code:
                            self._ack_command(cmd_name, f"STARTED: {self.current_project_code}")
                            self._force_complete_current_project()
                            self._ack_command(cmd_name, "OK")
                        else:
                            self._ack_command(cmd_name, "NO_PROJECT")
                    else:
                        self._ack_command(cmd_name, "UNKNOWN")
                except Exception as e:
                    self.log(f"Command '{cmd_name}' error: {e}", "MANAGER", "ERROR")
                    self._ack_command(cmd_name, f"ERROR: {e}")

                # Xóa command file sau khi xử lý
                try:
                    cmd_file.unlink()
                except Exception:
                    pass
        except Exception:
            pass

    def _force_complete_current_project(self):
        """v1.0.364: Master ấn DONE → ép hoàn thành project hiện tại.
        Copy kết quả về master VISUAL rồi chuyển sang mã mới."""
        project_code = self.current_project_code
        if not project_code:
            return

        self.log("=" * 60, "SYSTEM")
        self.log(f"FORCE COMPLETE (Master): {project_code}", "SYSTEM", "WARN")
        self.log("=" * 60, "SYSTEM")

        import gc

        # 1. Stop all workers + Kill Chrome
        self.log("Step 1: Stopping all workers...", "SYSTEM")
        for wid in list(self.workers.keys()):
            self.stop_worker(wid)
        self.kill_all_chrome()
        self._clear_agent_status()
        time.sleep(5)
        gc.collect()

        # 2. Copy to master
        copy_ok = False
        try:
            self.log(f"Step 2: Copying {project_code} to master...", "SYSTEM")
            self.copy_project_to_master(project_code, delete_after=False)
            self.log(f"Copied {project_code} successfully", "SYSTEM", "SUCCESS")
            copy_ok = True
        except Exception as e:
            self.log(f"Failed to copy {project_code}: {e}", "SYSTEM", "ERROR")

        # 3. Delete local folder
        if copy_ok:
            self.log("Step 3: Deleting local folder...", "SYSTEM")
            time.sleep(3)
            gc.collect()
            self._delete_project_folder(project_code)

        # 4. Mark completed
        if not hasattr(self, '_completed_projects'):
            self._completed_projects = set()
        self._completed_projects.add(project_code)
        self.project_start_time = None
        self.current_project_code = None

        # 5. Restart workers
        self.log("Step 4: Restarting workers...", "SYSTEM")
        for wid in list(self.workers.keys()):
            time.sleep(2)
            self.start_worker(wid, gui_mode=self.gui_mode)

        self.log("Ready for next project", "SYSTEM", "SUCCESS")

    def _ack_command(self, cmd_name: str, result: str):
        """Ghi ACK lên master để master biết lệnh đã được thực hiện."""
        try:
            cmd_dir = self.auto_path / "ve3-tool-simple" / "control" / "commands"
            ack_file = cmd_dir / f"{self._vm_id}.{cmd_name}.ack"
            ack_content = json.dumps({
                "vm_id": self._vm_id,
                "command": cmd_name,
                "result": result,
                "timestamp": datetime.now().isoformat(),
            })
            ack_file.write_text(ack_content, encoding='utf-8')
            self.log(f"ACK: {cmd_name} → {result}", "MANAGER")
        except Exception:
            pass

    def _do_git_update(self):
        """Pull code mới - hỗ trợ cả git và ZIP download."""
        import urllib.request
        import zipfile

        GITHUB_ZIP_URL = "https://github.com/nguyenvantuong161978-dotcom/ve3-tool-simple/archive/refs/heads/main.zip"
        GITHUB_GIT_URL = "https://github.com/nguyenvantuong161978-dotcom/ve3-tool-simple.git"

        self.log("Update started...", "MANAGER")
        try:
            self.stop_all()

            # Thử git trước
            git_ok = False
            try:
                r = subprocess.run(['git', '--version'], capture_output=True, timeout=10)
                git_ok = (r.returncode == 0)
            except Exception:
                pass

            if git_ok:
                # Đảm bảo remote URL đúng
                try:
                    r = subprocess.run(['git', 'remote', 'get-url', 'origin'],
                                      cwd=str(TOOL_DIR), capture_output=True, text=True, timeout=10)
                    if r.returncode != 0:
                        subprocess.run(['git', 'remote', 'add', 'origin', GITHUB_GIT_URL],
                                      cwd=str(TOOL_DIR), capture_output=True, timeout=10)
                    elif GITHUB_GIT_URL not in r.stdout.strip():
                        subprocess.run(['git', 'remote', 'set-url', 'origin', GITHUB_GIT_URL],
                                      cwd=str(TOOL_DIR), capture_output=True, timeout=10)
                except Exception:
                    pass

                for cmd in [['git', 'fetch', 'origin', 'main'],
                            ['git', 'checkout', 'main'],
                            ['git', 'reset', '--hard', 'origin/main']]:
                    subprocess.run(cmd, cwd=str(TOOL_DIR), capture_output=True, text=True, timeout=120)
                self.log("Git update OK", "MANAGER")
            else:
                # Không có git → tải ZIP
                self.log("No git - downloading ZIP...", "MANAGER")
                import ssl
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

                zip_path = TOOL_DIR / "update_temp.zip"
                extract_dir = TOOL_DIR / "update_temp"

                download_url = f"{GITHUB_ZIP_URL}?t={int(time.time())}"
                with urllib.request.urlopen(download_url, context=ssl_ctx) as resp:
                    with open(str(zip_path), 'wb') as f:
                        f.write(resp.read())

                with zipfile.ZipFile(str(zip_path), 'r') as zf:
                    zf.extractall(str(extract_dir))

                src = extract_dir / "ve3-tool-simple-main"
                files_to_update = [
                    "vm_manager.py", "vm_manager_gui.py", "run_excel_api.py",
                    "run_worker.py", "START.py", "START.bat", "requirements.txt",
                    "_run_chrome1.py", "_run_chrome2.py", "google_login.py", "VERSION.txt",
                ]
                for fn in files_to_update:
                    s = src / fn
                    if s.exists():
                        shutil.copy2(str(s), str(TOOL_DIR / fn))

                # Copy modules/ (bao gom subfolders nhu proxy_providers/)
                src_mod = src / "modules"
                dst_mod = TOOL_DIR / "modules"
                if src_mod.exists():
                    for py in src_mod.glob("*.py"):
                        shutil.copy2(str(py), str(dst_mod / py.name))
                    # v1.0.615: Copy subfolders (proxy_providers/, ...)
                    for sub in src_mod.iterdir():
                        if sub.is_dir() and not sub.name.startswith('__'):
                            dst_sub = dst_mod / sub.name
                            dst_sub.mkdir(parents=True, exist_ok=True)
                            for py in sub.glob("*.py"):
                                shutil.copy2(str(py), str(dst_sub / py.name))

                # v1.0.593: Copy control/ (master_control.py)
                src_ctrl = src / "control"
                dst_ctrl = TOOL_DIR / "control"
                if src_ctrl.exists():
                    dst_ctrl.mkdir(parents=True, exist_ok=True)
                    for py in src_ctrl.glob("*.py"):
                        shutil.copy2(str(py), str(dst_ctrl / py.name))

                # v1.0.624: Copy ve3/ (VE3 Simple tool)
                src_ve3 = src / "ve3"
                dst_ve3 = TOOL_DIR / "ve3"
                if src_ve3.exists():
                    if dst_ve3.exists():
                        shutil.rmtree(str(dst_ve3))
                    shutil.copytree(str(src_ve3), str(dst_ve3), ignore=shutil.ignore_patterns('__pycache__', 'PROJECTS', '*.pyc'))

                # v1.0.617: Xoa __pycache__ de Python load code moi
                for cache_dir in TOOL_DIR.rglob("__pycache__"):
                    try:
                        shutil.rmtree(str(cache_dir))
                    except Exception:
                        pass

                # Cleanup
                if zip_path.exists():
                    zip_path.unlink()
                if extract_dir.exists():
                    shutil.rmtree(str(extract_dir))

                self.log("ZIP update OK", "MANAGER")

            # v1.0.373: Restart TOÀN BỘ Python process để load code mới
            self.log("Restarting tool (load code moi)...", "MANAGER")

            # XÓA command file TRƯỚC khi execv
            # Nếu không xóa → restart → watchdog thấy file → UPDATE lại → LOOP VÔ HẠN
            try:
                cmd_dir = self.auto_path / "ve3-tool-simple" / "control" / "commands"
                for f in cmd_dir.glob(f"{self._vm_id}.update*"):
                    if not f.name.endswith('.ack'):
                        f.unlink()
            except Exception:
                pass

            # ACK trước khi execv (execv replace process → code sau không chạy)
            self._ack_command("update", "OK - RESTARTING")
            time.sleep(1)

            import os as _os
            _os.execv(sys.executable, [sys.executable] + sys.argv)

        except Exception as e:
            self.log(f"Update error: {e}", "MANAGER", "ERROR")

    # ================================================================================
    # ORCHESTRATION
    # ================================================================================

    def orchestrate(self):
        self.log("Orchestration started", "MANAGER")
        health_check_counter = 0

        while True:  # Không bao giờ thoát - _stop_flag chỉ tạm dừng, không kill thread
            if self._stop_flag:
                time.sleep(2)
                continue
            try:
                # 0. Check auto-restart Chrome workers (mỗi 1 tiếng)
                if time.time() - self.chrome_last_restart >= self.chrome_restart_interval:
                    self.log("Auto-restart Chrome workers (1 tiếng)", "MANAGER")
                    self.auto_restart_chrome_workers()
                    self.chrome_last_restart = time.time()

                # 0b. Check project timeout (6 tiếng)
                if self.project_start_time and self.current_project_code:
                    elapsed = time.time() - self.project_start_time
                    if elapsed >= self.project_timeout:
                        self.log(f"Project {self.current_project_code} timeout (6 tiếng) - Moving to next", "MANAGER")
                        self.handle_project_timeout(self.current_project_code)

                # 0c. v1.0.234: Check account issue (1 tiếng mà < 5 ảnh → switch account)
                if self.account_start_time and self.current_project_code:
                    elapsed_acc = time.time() - self.account_start_time
                    if elapsed_acc >= self.account_issue_timeout:
                        project_dir = TOOL_DIR / "PROJECTS" / self.current_project_code
                        img_dir = project_dir / "img"
                        img_count = len(list(img_dir.glob("*.png"))) + len(list(img_dir.glob("*.jpg"))) if img_dir.exists() else 0
                        if img_count < self.account_issue_min_images:
                            self.log(f"Account issue: {img_count} ảnh sau 1 tiếng → Switch account", "MANAGER", "WARN")
                            self.handle_account_issue(self.current_project_code)
                        else:
                            # Đủ ảnh → tắt timer (không check lại)
                            self.account_start_time = None

                # 1. Sync worker status từ Agent Protocol
                self.sync_worker_status()

                # 2. Collect results từ workers
                self.collect_results()

                # 3. Health check mỗi 30s (6 vòng x 5s)
                # v1.0.341: Skip 60s đầu để workers ổn định (tránh restart do log cũ)
                health_check_counter += 1
                _uptime = time.time() - self._start_time if hasattr(self, '_start_time') else 999
                if health_check_counter >= 6 and _uptime > 60:
                    health_check_counter = 0
                    workers_with_errors = self.check_worker_health()
                    for wid, error_type in workers_with_errors:
                        self.handle_worker_error(wid, error_type)

                    # Auto-recovery for Chrome connection lost errors
                    # v1.0.341: Skip 60s đầu sau start (tránh đọc log cũ → restart trùng)
                    if hasattr(self, '_start_time') and (time.time() - self._start_time) > 60:
                        if self.check_and_auto_recover():
                            # If recovery was triggered, skip this iteration
                            continue

                # 4. Check completed tasks và retry nếu cần
                for task in list(self.tasks.values()):
                    if task.status == TaskStatus.COMPLETED:
                        retry = self.check_and_retry(task)
                        if retry:
                            task.status = TaskStatus.FAILED

                # 5. Scan projects - CHỈ LÀM 1 PROJECT TẠI 1 THỜI ĐIỂM
                # Ưu tiên project đang làm dở, không nhảy sang project khác
                projects = self.scan_projects()
                if projects:
                    # Ưu tiên project đang làm dở (current_project_code)
                    if self.current_project_code and self.current_project_code in projects:
                        target_project = self.current_project_code
                    else:
                        # Không có project đang làm → lấy project đầu tiên
                        target_project = projects[0]

                    # Chỉ tạo task cho 1 project này
                    self.create_tasks_for_project(target_project)

                # 6. Assign pending tasks cho workers
                for task in self.get_pending_tasks(TaskType.EXCEL):
                    wid = self.get_idle_worker("excel")
                    if wid:
                        self.assign_task(task, wid)

                for task in self.get_pending_tasks(TaskType.IMAGE):
                    wid = self.get_idle_worker("chrome")
                    if wid:
                        self.assign_task(task, wid)

                for task in self.get_pending_tasks(TaskType.VIDEO):
                    wid = self.get_idle_worker("chrome")
                    if wid:
                        self.assign_task(task, wid)

                time.sleep(5)
            except Exception as e:
                self.log(f"Error: {e}", "ERROR", "ERROR")
                time.sleep(10)

    # ================================================================================
    # INTERACTIVE
    # ================================================================================

    def run_interactive(self):
        self.dashboard.clear_screen()
        print(self.dashboard.render())

        self.start_all()

        self._stop_flag = False
        if self._orch_thread is None or not self._orch_thread.is_alive():
            self._orch_thread = threading.Thread(target=self.orchestrate, daemon=True)
            self._orch_thread.start()

        try:
            while not self._stop_flag:
                try:
                    cmd = input("\n[VM Manager] > ").strip().lower()

                    if not cmd:
                        continue
                    elif cmd == "status":
                        self.dashboard.clear_screen()
                        print(self.dashboard.render())
                    elif cmd == "tasks":
                        print("\n  TASKS:")
                        for tid, t in self.tasks.items():
                            print(f"    {tid}: {t.status.value} → {t.assigned_to or '-'}")
                    elif cmd == "scan":
                        projects = self.scan_projects()
                        print(f"\n  Found {len(projects)} projects: {projects}")
                    elif cmd == "restart":
                        for wid in self.workers:
                            self.restart_worker(wid)
                    elif cmd.startswith("restart "):
                        try:
                            num = int(cmd.split()[-1])
                            self.restart_worker(f"chrome_{num}")
                        except:
                            pass
                    elif cmd.startswith("scale "):
                        try:
                            num = int(cmd.split()[-1])
                            # Stop existing workers
                            self.stop_all()
                            # Scale with auto-creation of Chrome profiles
                            if self.scale_chrome_workers(num):
                                self.start_all(gui_mode=self.gui_mode)
                                print(f"  Scaled to {num} Chrome workers")
                            else:
                                print(f"  Failed to scale to {num} workers")
                        except Exception as e:
                            print(f"  Error: {e}")
                    elif cmd.startswith("logs "):
                        try:
                            parts = cmd.split()
                            if len(parts) >= 2:
                                target = parts[1]
                                if target.isdigit():
                                    worker_id = f"chrome_{target}"
                                elif target == "excel":
                                    worker_id = "excel"
                                else:
                                    worker_id = target
                                logs = self.get_worker_logs(worker_id, 20)
                                print(f"\n  LOGS [{worker_id}]:")
                                for log in logs:
                                    print(f"    {log.strip()}")
                                if not logs:
                                    print("    (No logs available)")
                        except Exception as e:
                            print(f"  Error: {e}")
                    elif cmd == "logs":
                        print("\n  Usage: logs <worker_id>  (e.g., logs 1, logs excel)")
                    elif cmd == "errors":
                        print("\n  ERROR SUMMARY:")
                        error_summary = self.get_error_summary()
                        if error_summary:
                            for error_type, count in error_summary.items():
                                print(f"    {error_type}: {count}")
                        else:
                            print("    (No errors)")

                        print("\n  WORKER ERRORS:")
                        for wid in self.workers:
                            details = self.get_worker_details(wid)
                            if details and details.get("last_error"):
                                print(f"    [{wid}] {details['last_error_type']}: {details['last_error'][:60]}")
                    elif cmd.startswith("detail "):
                        try:
                            parts = cmd.split()
                            if len(parts) >= 2:
                                target = parts[1]
                                if target.isdigit():
                                    worker_id = f"chrome_{target}"
                                elif target == "excel":
                                    worker_id = "excel"
                                else:
                                    worker_id = target

                                if worker_id in self.workers:
                                    w = self.workers[worker_id]
                                    details = self.get_worker_details(worker_id)

                                    print(f"\n  WORKER DETAIL: {worker_id}")
                                    print(f"  {'='*50}")
                                    print(f"    Type:           {w.worker_type}")
                                    print(f"    Status:         {w.status.value}")
                                    print(f"    Completed:      {w.completed_tasks}")
                                    print(f"    Failed:         {w.failed_tasks}")
                                    print(f"    Restart count:  {w.restart_count}")

                                    if w.last_restart_time:
                                        elapsed = int((datetime.now() - w.last_restart_time).total_seconds())
                                        print(f"    Last restart:   {elapsed}s ago")

                                    if details:
                                        print(f"\n    [From Agent Protocol]")
                                        print(f"    State:          {details.get('state', '-')}")
                                        print(f"    Progress:       {details.get('progress', 0)}%")
                                        print(f"    Project:        {details.get('current_project', '-')}")
                                        print(f"    Scene:          {details.get('current_scene', 0)}/{details.get('total_scenes', 0)}")
                                        print(f"    Uptime:         {details.get('uptime_seconds', 0)}s")
                                        if details.get('last_error'):
                                            print(f"    Last error:     [{details.get('last_error_type')}] {details.get('last_error')[:50]}")
                                else:
                                    print(f"  Worker not found: {worker_id}")
                        except Exception as e:
                            print(f"  Error: {e}")
                    elif cmd == "detail":
                        print("\n  Usage: detail <worker>  (e.g., detail 1, detail excel)")
                    elif cmd == "ipv6":
                        # Hiển thị trạng thái IPv6
                        if self.ipv6_manager:
                            status = self.ipv6_manager.get_status()
                            print(f"\n  IPv6 STATUS:")
                            print(f"    Enabled:        {status['enabled']}")
                            print(f"    Interface:      {status['interface']}")
                            print(f"    Current IPs:    {status['current_ipv6']}")
                            print(f"    Available:      {status['available_count']}")
                            print(f"    Rotations:      {status['rotation_count']}")
                            print(f"    Last rotation:  {status['last_rotation'] or 'Never'}")
                            print(f"\n  ERROR TRACKING:")
                            print(f"    403 count:      {self.consecutive_403_count}/{self.max_403_before_ipv6}")
                            for wid, count in self.worker_error_counts.items():
                                print(f"    {wid}:      {count}/{self.max_errors_before_clear}")
                        else:
                            print("\n  IPv6 Manager not available")
                    elif cmd == "ipv6 rotate":
                        # Manual rotation
                        if self.ipv6_manager and self.ipv6_manager.enabled:
                            print("\n  Rotating IPv6...")
                            self.perform_ipv6_rotation()
                        else:
                            print("\n  IPv6 rotation disabled or not available")
                    elif cmd == "set":
                        print(f"\n  SETTINGS:")
                        for k, v in self.settings.get_summary().items():
                            print(f"    {k}: {v}")
                    elif cmd in ("quit", "exit", "q"):
                        break
                    else:
                        print("  Commands: status, tasks, scan, restart, scale, logs, errors, detail, ipv6, set, quit")

                except (EOFError, KeyboardInterrupt):
                    break

        finally:
            self.stop_all()
            print("\nVM Manager stopped.")


# ================================================================================
# MAIN
# ================================================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="VM Manager - AI Agent")
    parser.add_argument("--chrome", "-c", type=int, default=2)
    parser.add_argument("--no-excel", action="store_true")
    args = parser.parse_args()

    manager = VMManager(num_chrome_workers=args.chrome, enable_excel=not args.no_excel)
    manager.run_interactive()


if __name__ == "__main__":
    main()
