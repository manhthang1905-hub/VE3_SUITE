#!/usr/bin/env python3
"""
Run Excel API - Tạo Excel prompts từ SRT bằng API.

=============================================================
  EXCEL API WORKER - Standalone Mode with Agent Protocol
=============================================================

Chạy riêng biệt với Chrome workers, có thể:
1. Quét và tạo Excel cho tất cả projects thiếu
2. Tạo Excel cho 1 project cụ thể
3. Fix/hoàn thiện Excel có [FALLBACK] prompts

Usage:
    python run_excel_api.py                    # Quét và tạo Excel tự động
    python run_excel_api.py KA2-0001           # Tạo Excel cho 1 project
    python run_excel_api.py --fix KA2-0001     # Fix Excel có [FALLBACK]
    python run_excel_api.py --scan             # Chỉ quét, không tạo
    python run_excel_api.py --loop             # Chạy loop liên tục
"""

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
import shutil
import yaml
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Callable

# Add current directory to path
TOOL_DIR = Path(__file__).parent
sys.path.insert(0, str(TOOL_DIR))

# Agent Protocol - giao tiếp với VM Manager
try:
    from modules.agent_protocol import AgentWorker, ErrorType
    AGENT_ENABLED = True
except ImportError:
    AGENT_ENABLED = False
    AgentWorker = None

# Central Logger
try:
    from modules.central_logger import get_logger
    _logger = get_logger("excel")
except ImportError:
    class FakeLogger:
        def info(self, msg): print(f"[excel] {msg}")
        def warn(self, msg): print(f"[excel] WARN: {msg}")
        def error(self, msg): print(f"[excel] ERROR: {msg}")
    _logger = FakeLogger()

# Global agent instance
_agent: Optional['AgentWorker'] = None

# ================================================================================
# CONFIGURATION
# ================================================================================

SCAN_INTERVAL = 60          # Lần đầu: 60 giây
SCAN_INTERVAL_LONG = 1800   # Từ lần 2 trở đi: 30 phút (ít mở Excel hơn → ít conflict với Chrome)

# Auto-detect network paths
# Ưu tiên SMB share (Z:) trước vì ổn định hơn tsclient khi copy file lớn
POSSIBLE_AUTO_PATHS = [
    Path(r"Z:\AUTO"),                              # SMB Share (ưu tiên - ổn định nhất)
    Path(r"Y:\AUTO"),                              # Mapped drive
    Path(r"\\tsclient\D\AUTO"),
    Path(r"\\vmware-host\Shared Folders\D\AUTO"),
    Path(r"\\VBOXSVR\AUTO"),
    Path(r"D:\AUTO"),
]


# ================================================================================
# HELPERS
# ================================================================================

def log(msg: str, level: str = "INFO"):
    """Log với timestamp và gửi đến Agent."""
    global _agent
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix = {
        "INFO": "   ",
        "WARN": " [WARN]",
        "ERROR": " [FAIL]",
        "SUCCESS": " [OK]",
    }.get(level, "   ")

    # Log to central logger
    full_msg = f"{prefix} {msg}"
    if level == "ERROR":
        _logger.error(full_msg)
    elif level == "WARN":
        _logger.warn(full_msg)
    else:
        _logger.info(full_msg)

    # Gửi đến Agent nếu có
    if _agent and AGENT_ENABLED:
        if level == "ERROR":
            _agent.log_error(msg)
        else:
            _agent.log(msg, level)


def safe_path_exists(path: Path) -> bool:
    """Safely check if path exists (handle network errors)."""
    try:
        return path.exists()
    except (OSError, PermissionError):
        return False


def detect_auto_path() -> Optional[Path]:
    """Detect network AUTO path.
    v1.0.324: Dùng robust_copy.get_working_auto_path() với đầy đủ fallback paths.
    """
    try:
        from modules.robust_copy import get_working_auto_path
        result = get_working_auto_path(log=lambda msg, lvl="INFO": log(msg, lvl))
        if result:
            log(f"Found AUTO path: {result}")
            return Path(result)
        return None
    except ImportError:
        pass
    # Fallback nếu module chưa có
    for p in POSSIBLE_AUTO_PATHS:
        if safe_path_exists(p):
            log(f"Found AUTO path: {p}")
            return p
    return None


def get_channel_from_folder() -> Optional[str]:
    """Get channel filter from folder name (e.g., KA2-T1 → KA2)."""
    folder = TOOL_DIR.parent.name
    if "-T" in folder:
        return folder.split("-T")[0]
    elif folder.startswith("KA") or folder.startswith("AR"):
        return folder.split("-")[0] if "-" in folder else folder[:3]
    return None


def matches_channel(project_name: str, channel: Optional[str] = None) -> bool:
    """Check if project matches channel filter."""
    if not channel:
        return True
    return project_name.startswith(f"{channel}-")


def is_project_in_visual(name: str, auto_path: Optional[Path]) -> bool:
    """
    v1.0.69: Check if project already exists in VISUAL folder on master.
    Nếu đã có trong VISUAL → skip, không import lại.
    """
    if not auto_path:
        return False

    # v1.0.69: Thống nhất dùng "visual" chữ thường
    visual_dir = auto_path / "visual" / name

    try:
        if not visual_dir.exists():
            return False

        # Check if has images (project hoàn thành)
        img_dir = visual_dir / "img"
        if not img_dir.exists():
            return False

        img_files = list(img_dir.glob("*.png")) + list(img_dir.glob("*.jpg"))
        if len(img_files) > 0:
            log(f"  [{name}] Already in VISUAL ({len(img_files)} images) - Skip")
            return True

        return False
    except (OSError, PermissionError):
        return False


def import_from_master(master_dir: Path, name: str, local_projects: Path, already_claimed: bool = False) -> Optional[Path]:
    """
    Copy project từ master về local để xử lý.

    Args:
        master_dir: Thư mục project trên master
        name: Tên project
        local_projects: Thư mục PROJECTS local
        already_claimed: True nếu đã claim qua TaskQueue.claim_next() rồi, skip claim step

    Returns:
        Path tới project local nếu thành công, None nếu lỗi

    FIX v1.0.46: KHÔNG XÓA local folder nếu đã có Excel hoặc images!
    Bug cũ: Nếu local có Excel nhưng thiếu SRT → xóa hết rồi import lại.
    """
    local_dir = local_projects / name

    # Đã có trong local rồi - kiểm tra CẢ SRT, Excel VÀ images
    if local_dir.exists():
        srt_path = local_dir / f"{name}.srt"
        excel_path = local_dir / f"{name}_prompts.xlsx"
        img_dir = local_dir / "img"

        # Nếu có SRT → OK, dùng local
        if srt_path.exists():
            return local_dir

        # Nếu có Excel → KHÔNG XÓA! Đã có công việc rồi
        if excel_path.exists():
            log(f"[IMPORT] {name}: Local has Excel, keeping existing work")
            return local_dir

        # Nếu có images → KHÔNG XÓA! Đã tạo ảnh rồi
        if img_dir.exists():
            img_files = list(img_dir.glob("*.png")) + list(img_dir.glob("*.jpg"))
            if len(img_files) > 0:
                log(f"[IMPORT] {name}: Local has {len(img_files)} images, keeping existing work")
                return local_dir

    # v1.0.354: CLAIM TRƯỚC → COPY SAU (tránh nhiều VM copy cùng mã)
    try:
        local_projects.mkdir(parents=True, exist_ok=True)

        # === BƯỚC 1: CLAIM trên master TRƯỚC KHI copy ===
        # v1.0.355: Skip claim nếu đã claim qua TaskQueue.claim_next()
        vm_id = TOOL_DIR.parent.name
        import socket
        _hostname = socket.gethostname()
        claimed_ok = already_claimed  # True nếu scan đã claim rồi

        _claim_rejected = False  # True nếu máy khác đã claim (không fallback)
        if not claimed_ok:
            try:
                from modules.robust_copy import TaskQueue
                auto_path = master_dir.parent.parent.parent  # AUTO
                tq = TaskQueue(
                    master_projects=str(master_dir.parent),
                    vm_id=vm_id,
                    visual_path=str(auto_path / "visual"),
                    tool_dir=str(TOOL_DIR),
                    log=lambda msg, lvl="INFO": log(f"{msg}", lvl),
                )
                if tq.claim(name):
                    log(f"[IMPORT] Claimed on master: {name} → {vm_id}")
                    claimed_ok = True
                else:
                    log(f"[IMPORT] {name} đã bị máy khác claim - SKIP!", "WARN")
                    _claim_rejected = True  # Máy khác đã claim → KHÔNG fallback
            except Exception as e:
                log(f"[IMPORT] TaskQueue.claim() error: {e}", "WARN")

        # v1.0.387: Fallback dùng O_CREAT|O_EXCL (atomic) thay vì write_text (race condition)
        if not claimed_ok and not _claim_rejected:
            try:
                claimed_file = master_dir / "_CLAIMED"
                claim_content = f"{vm_id}\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{_hostname}\n"
                try:
                    fd = os.open(str(claimed_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.write(fd, claim_content.encode('utf-8'))
                    os.close(fd)
                    import time as _t
                    _t.sleep(3)  # Đợi NFS sync
                    # Đọc lại verify
                    try:
                        read_back = claimed_file.read_text(encoding='utf-8').strip()
                        first_line = read_back.split('\n')[0].strip()
                        if first_line == vm_id:
                            log(f"[IMPORT] Claimed (fallback+atomic): {name} → {vm_id}")
                            claimed_ok = True
                        else:
                            log(f"[IMPORT] Fallback claim bị ghi đè bởi {first_line} - SKIP!", "WARN")
                    except Exception:
                        log(f"[IMPORT] Fallback verify failed - SKIP!", "WARN")
                except FileExistsError:
                    log(f"[IMPORT] _CLAIMED đã tồn tại (máy khác claim trước) - SKIP!", "WARN")
            except Exception as e:
                log(f"[IMPORT] Fallback claim FAILED: {e}", "ERROR")

        if not claimed_ok:
            log(f"[IMPORT] Cannot claim {name} - skip!", "WARN")
            return None

        # === BƯỚC 2: COPY từ master về local (sau khi đã claim) ===
        log(f"[IMPORT] Copying {name} from master to local...")

        if local_dir.exists():
            excel_path = local_dir / f"{name}_prompts.xlsx"
            img_dir = local_dir / "img"
            if excel_path.exists():
                log(f"[IMPORT] {name}: ABORT - Local has Excel, not deleting!")
                return local_dir
            if img_dir.exists() and len(list(img_dir.glob("*.png"))) > 0:
                log(f"[IMPORT] {name}: ABORT - Local has images, not deleting!")
                return local_dir
            shutil.rmtree(local_dir)

        try:
            from modules.robust_copy import robust_copy_tree
            _log = lambda msg, lvl="INFO": log(f"{msg}", lvl)
            ok = robust_copy_tree(str(master_dir), str(local_dir), max_retries=3, retry_delay=5, verify=True, log=_log)
            if not ok:
                log(f"[IMPORT] Robust copy thất bại cho {name}!", "ERROR")
                return None
        except ImportError:
            shutil.copytree(master_dir, local_dir)
        log(f"[IMPORT] Copied: {name}")

        # === BƯỚC 3: Copy _CLAIMED về local ===
        try:
            master_claimed = master_dir / "_CLAIMED"
            local_claimed = local_dir / "_CLAIMED"
            if master_claimed.exists():
                shutil.copy2(str(master_claimed), str(local_claimed))
                log(f"[IMPORT] _CLAIMED copied to local: {name}")
        except Exception as e:
            log(f"[IMPORT] Copy _CLAIMED to local error: {e}", "WARN")

        # === BƯỚC 4: Đọc topic từ _CLAIMED (dòng 5) → update settings.yaml ===
        # v1.0.444: Tự động đổi topic + excel_mode theo chủ đề trong _CLAIMED
        # Mapping: truyện → story/small, tâm lý → psychology/full, tài chính → finance_history/full
        #          tài chính vn → finance_history_vn/full
        try:
            local_claimed = local_dir / "_CLAIMED"
            if local_claimed.exists():
                claimed_lines = local_claimed.read_text(encoding='utf-8').strip().split('\n')
                if len(claimed_lines) >= 5 and claimed_lines[4].strip():
                    raw_topic = claimed_lines[4].strip().lower()
                    # Mapping chủ đề tiếng Việt → topic code + excel_mode
                    TOPIC_MAPPING = {
                        'truyện': ('story', 'small'),
                        'truyen': ('story', 'small'),
                        'story': ('story', 'small'),
                        'tâm lý': ('psychology', 'full'),
                        'tam ly': ('psychology', 'full'),
                        'psychology': ('psychology', 'full'),
                        'tài chính': ('finance_history', 'full'),
                        'tai chinh': ('finance_history', 'full'),
                        'finance_history': ('finance_history', 'full'),
                        'tài chính vn': ('finance_history_vn', 'full'),
                        'tai chinh vn': ('finance_history_vn', 'full'),
                        'finance_history_vn': ('finance_history_vn', 'full'),
                        'tâm lý video': ('psychology_video', 'full'),
                        'tam ly video': ('psychology_video', 'full'),
                        'psychology_video': ('psychology_video', 'full'),
                        'tài chính video': ('finance_video', 'full'),
                        'tai chinh video': ('finance_video', 'full'),
                        'finance_video': ('finance_video', 'full'),
                    }
                    mapped = TOPIC_MAPPING.get(raw_topic)
                    if mapped:
                        new_topic, new_excel_mode = mapped
                        # Update settings.yaml
                        import yaml
                        settings_path = TOOL_DIR / "config" / "settings.yaml"
                        if settings_path.exists():
                            with open(settings_path, 'r', encoding='utf-8') as f:
                                settings = yaml.safe_load(f) or {}
                            old_topic = settings.get('topic', '')
                            old_mode = settings.get('excel_mode', '')
                            if old_topic != new_topic or old_mode != new_excel_mode:
                                settings['topic'] = new_topic
                                settings['excel_mode'] = new_excel_mode
                                with open(settings_path, 'w', encoding='utf-8') as f:
                                    yaml.dump(settings, f, default_flow_style=False, allow_unicode=True)
                                log(f"[IMPORT] Auto-config: topic={new_topic}, excel_mode={new_excel_mode} (từ _CLAIMED: '{raw_topic}')")
                            else:
                                log(f"[IMPORT] Topic already correct: {new_topic}/{new_excel_mode}")
                    else:
                        log(f"[IMPORT] Unknown topic in _CLAIMED: '{raw_topic}' - giữ nguyên settings", "WARN")

                # === BƯỚC 4b: Đọc character template từ _CLAIMED (dòng 6) ===
                # v1.0.448: Character template từ Google Sheet col L sheet THONG TIN
                if len(claimed_lines) >= 6 and claimed_lines[5].strip():
                    char_template = claimed_lines[5].strip()
                    import yaml
                    settings_path = TOOL_DIR / "config" / "settings.yaml"
                    if settings_path.exists():
                        with open(settings_path, 'r', encoding='utf-8') as f:
                            settings = yaml.safe_load(f) or {}
                        settings['character_template'] = char_template
                        with open(settings_path, 'w', encoding='utf-8') as f:
                            yaml.dump(settings, f, default_flow_style=False, allow_unicode=True)
                        log(f"[IMPORT] Character template from _CLAIMED line 6: {char_template[:80]}...")
        except Exception as e:
            log(f"[IMPORT] Auto-config topic error: {e}", "WARN")

        return local_dir
    except Exception as e:
        log(f"[IMPORT] Error copying {name}: {e}", "ERROR")
        return None


def is_project_complete(project_dir: Path, name: str) -> bool:
    """
    Check if project has completed images (ready to be moved to VISUAL).
    """
    img_dir = project_dir / "img"
    if not img_dir.exists():
        return False

    # Check for scene images
    scene_imgs = list(img_dir.glob("scene_*.png"))
    return len(scene_imgs) > 0


# ================================================================================
# EXCEL API FUNCTIONS
# ================================================================================

def load_config() -> dict:
    """Load config from settings.yaml."""
    cfg = {}
    cfg_file = TOOL_DIR / "config" / "settings.yaml"
    if cfg_file.exists():
        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    # Collect API keys
    deepseek_key = cfg.get('deepseek_api_key', '')
    if deepseek_key:
        cfg['deepseek_api_keys'] = [deepseek_key]

    return cfg


def has_api_keys(cfg: dict) -> bool:
    """Check if config has any API keys."""
    return bool(
        cfg.get('deepseek_api_keys') or
        cfg.get('groq_api_keys') or
        cfg.get('gemini_api_keys')
    )


def create_excel_with_api(
    project_dir: Path,
    name: str,
    log_callback: Callable = None,
    agent = None
) -> bool:
    """
    Tạo Excel từ SRT bằng Progressive API.

    Args:
        project_dir: Thư mục project
        name: Tên project (e.g., KA2-0001)
        log_callback: Callback để log
        agent: Agent Protocol instance để update status

    Returns:
        True nếu thành công
    """
    if log_callback is None:
        log_callback = lambda msg, level="INFO": log(msg, level)

    excel_path = project_dir / f"{name}_prompts.xlsx"
    srt_path = project_dir / f"{name}.srt"

    # Check SRT exists
    if not srt_path.exists():
        log_callback(f"No SRT file found: {srt_path}", "ERROR")
        return False

    log_callback(f"Creating Excel from SRT (Progressive API)...")

    # Load config
    cfg = load_config()

    if not has_api_keys(cfg):
        log_callback("No API keys configured! Check config/settings.yaml", "ERROR")
        return False

    # Step names for display
    step_names = {
        1: "Story Analysis",
        2: "Story Segments",
        3: "Characters",
        4: "Locations",
        5: "Director Plan",
        6: "Scene Planning",
        7: "Scene Prompts"
    }

    # Wrapper để parse step từ log và update agent
    def log_with_step_tracking(msg, level="INFO"):
        log_callback(msg, level)

        # Parse step number từ message: [STEP X/7]
        if agent and "[STEP" in msg:
            import re
            match = re.search(r'\[STEP\s*(\d+)/7\]', msg)
            if match:
                step_num = int(match.group(1))
                step_name = step_names.get(step_num, f"Step {step_num}")
                agent.update_status(
                    current_step=step_num,
                    step_name=step_name,
                    progress=int(step_num * 100 / 7)
                )

    # === Progressive API ===
    try:
        from modules.progressive_prompts import ProgressivePromptsGenerator

        gen = ProgressivePromptsGenerator(cfg)

        # Run all steps
        api_success = gen.run_all_steps(
            project_dir,
            name,
            log_callback=log_with_step_tracking
        )

        if api_success and excel_path.exists():
            log_callback(f"Excel created successfully!", "SUCCESS")
            return True
        else:
            log_callback("Progressive API incomplete", "WARN")

    except Exception as e:
        log_callback(f"API error: {e}", "ERROR")
        import traceback
        traceback.print_exc()

    # === Fallback ===
    log_callback("Trying fallback method...")

    try:
        cfg['fallback_only'] = True

        from modules.prompts_generator import PromptGenerator
        gen = PromptGenerator(cfg)

        if gen.generate_for_project(project_dir, name, fallback_only=True):
            log_callback("Fallback Excel created", "SUCCESS")
            return True
        else:
            log_callback("Failed to create fallback Excel", "ERROR")
            return False

    except Exception as e:
        log_callback(f"Fallback error: {e}", "ERROR")
        return False


def fix_excel_with_api(
    project_dir: Path,
    name: str,
    log_callback: Callable = None
) -> bool:
    """
    Fix/hoàn thiện Excel có [FALLBACK] prompts.

    Args:
        project_dir: Thư mục project
        name: Tên project
        log_callback: Callback để log

    Returns:
        True nếu thành công
    """
    if log_callback is None:
        log_callback = lambda msg, level="INFO": log(msg, level)

    excel_path = project_dir / f"{name}_prompts.xlsx"

    if not excel_path.exists():
        log_callback(f"No Excel found, creating new...", "WARN")
        return create_excel_with_api(project_dir, name, log_callback)

    log_callback(f"Fixing Excel with API...")

    # Backup first
    backup_path = excel_path.with_suffix('.xlsx.backup')
    shutil.copy2(excel_path, backup_path)
    log_callback(f"Backed up to {backup_path.name}")

    # Load config
    cfg = load_config()

    if not has_api_keys(cfg):
        log_callback("No API keys, keeping existing fallback prompts", "WARN")
        backup_path.unlink()
        return True

    try:
        from modules.progressive_prompts import ProgressivePromptsGenerator

        gen = ProgressivePromptsGenerator(cfg)

        # Run all steps (will skip completed steps)
        api_success = gen.run_all_steps(
            project_dir,
            name,
            log_callback=lambda msg, level="INFO": log_callback(msg, level)
        )

        if api_success:
            log_callback(f"Excel fixed successfully!", "SUCCESS")
            backup_path.unlink()
            return True
        else:
            log_callback("API incomplete, keeping partial results", "WARN")
            return True

    except Exception as e:
        log_callback(f"Fix error: {e}", "ERROR")
        # Restore backup
        shutil.copy2(backup_path, excel_path)
        log_callback("Restored from backup")
        return False


def has_excel_with_prompts(project_dir: Path, name: str) -> bool:
    """Check if project has Excel with prompts."""
    excel_path = project_dir / f"{name}_prompts.xlsx"
    if not excel_path.exists():
        return False

    try:
        from modules.excel_manager import PromptWorkbook
        wb = PromptWorkbook(str(excel_path))
        wb.load_or_create()  # PHẢI load trước khi dùng
        scenes = wb.get_scenes()
        return any(s.img_prompt for s in scenes)
    except:
        return False


def needs_api_completion(project_dir: Path, name: str) -> bool:
    """Check if Excel has [FALLBACK] prompts that need API completion."""
    excel_path = project_dir / f"{name}_prompts.xlsx"
    if not excel_path.exists():
        return False

    try:
        from modules.excel_manager import PromptWorkbook
        wb = PromptWorkbook(str(excel_path))
        wb.load_or_create()  # PHẢI load trước khi dùng
        scenes = wb.get_scenes()
        return any("[FALLBACK]" in (s.img_prompt or "") for s in scenes)
    except:
        return False


def get_excel_progress(project_dir: Path, name: str) -> dict:
    """
    Đọc tiến độ Excel từ processing_status sheet.

    Returns:
        {
            'total_progress': 57.1,
            'current_step': 'step_4',
            'incomplete_steps': ['step_4', 'step_5', ...],
            'incomplete_details': [...],
            'flow_project_url': 'https://...',
            'can_resume': True,
            'is_complete': False
        }
    """
    excel_path = project_dir / f"{name}_prompts.xlsx"
    if not excel_path.exists():
        return {
            'total_progress': 0.0,
            'current_step': None,
            'incomplete_steps': [],
            'incomplete_details': [],
            'flow_project_url': '',
            'can_resume': False,
            'is_complete': False
        }

    try:
        from modules.excel_manager import PromptWorkbook
        wb = PromptWorkbook(str(excel_path))
        wb.load_or_create()
        return wb.get_resume_info()
    except Exception as e:
        log(f"Error getting Excel progress for {name}: {e}", "WARN")
        # v1.0.58: Thêm read_error flag để tránh tạo mới khi file bị lock
        # Khi Chrome Workers đang ghi vào Excel, file có thể bị lock tạm thời
        return {
            'total_progress': 0.0,
            'current_step': None,
            'incomplete_steps': [],
            'incomplete_details': [],
            'flow_project_url': '',
            'can_resume': False,
            'is_complete': False,
            'read_error': True  # Flag để biết có lỗi đọc, KHÔNG tạo mới!
        }


# ================================================================================
# SCANNER
# ================================================================================

class ExcelAPIWorker:
    """Worker để quét và tạo Excel với Agent Protocol."""

    def __init__(self):
        global _agent

        self.auto_path = detect_auto_path()
        self.channel = get_channel_from_folder()

        if self.auto_path:
            self.master_projects = self.auto_path / "ve3-tool-simple" / "PROJECTS"
        else:
            self.master_projects = None

        self.local_projects = TOOL_DIR / "PROJECTS"

        # Khởi tạo Agent để giao tiếp với VM Manager
        if AGENT_ENABLED:
            _agent = AgentWorker("excel")
            _agent.start_status_updater(interval=5)
            _agent.update_status(state="idle")
            log("Agent Protocol enabled - connected to VM Manager")
        else:
            _agent = None

        # Statistics
        self.completed_count = 0
        self.failed_count = 0

    def _is_distributed_mode(self) -> bool:
        """Check xem distributed mode có được bật không."""
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

    def scan_projects_needing_excel(self) -> List[tuple]:
        """
        Scan for projects that need Excel creation or completion.

        v1.0.326: Hỗ trợ distributed mode - bỏ channel filter.

        Returns:
            List of (project_dir, name, status, progress) tuples
            status: "create_new", "resume", or "fix_fallback"
            progress: dict from get_excel_progress()
        """
        results = []
        distributed = self._is_distributed_mode()
        channel_filter = None if distributed else self.channel

        # Scan local projects
        if self.local_projects.exists():
            for item in self.local_projects.iterdir():
                if not item.is_dir():
                    continue

                name = item.name
                if not matches_channel(name, channel_filter):
                    continue

                srt_path = item / f"{name}.srt"
                if not srt_path.exists():
                    continue

                excel_path = item / f"{name}_prompts.xlsx"

                if not excel_path.exists():
                    # Case 1: CHƯA CÓ EXCEL - Tạo mới
                    results.append((item, name, "create_new", None))
                else:
                    # Case 2: ĐÃ CÓ EXCEL - Check progress
                    progress = get_excel_progress(item, name)

                    # v1.0.317: Nếu img/ đã có ảnh → KHÔNG sửa Excel
                    # Chrome đang dùng prompts hiện tại, sửa sẽ gây mâu thuẫn
                    _img_dir = item / "img"
                    _has_images = False
                    if _img_dir.exists():
                        _img_count = len(list(_img_dir.glob("*.png"))) + len(list(_img_dir.glob("*.jpg")))
                        _has_images = _img_count > 0

                    if progress['is_complete']:
                        # Hoàn thành 100% - Skip
                        pass
                    elif _has_images:
                        # v1.0.317: Đã có ảnh → KHÔNG sửa Excel (dù chưa hoàn hảo)
                        log(f"  [{name}] Excel chưa hoàn hảo nhưng đã có ảnh - KHÔNG sửa lại")
                        pass
                    elif progress['can_resume']:
                        # Đang làm dở, chưa có ảnh - Resume
                        results.append((item, name, "resume", progress))
                    elif needs_api_completion(item, name):
                        # Có [FALLBACK], chưa có ảnh - Fix
                        results.append((item, name, "fix_fallback", progress))
                    elif not has_excel_with_prompts(item, name):
                        # v1.0.58: CRITICAL FIX - Check read_error trước khi tạo mới!
                        # Khi Chrome Workers đang ghi vào Excel, file bị lock → read_error
                        # KHÔNG được tạo mới vì sẽ xóa mất progress đã làm!
                        if progress.get('read_error', False):
                            log(f"  [{name}] Excel read error (file locked?) - SKIP to protect data")
                            pass
                        elif progress['total_progress'] == 0:
                            log(f"  [{name}] Excel empty (0%) - Will create new")
                            results.append((item, name, "create_new", None))
                        else:
                            # Có progress nhưng không có prompts - có thể đang tạo
                            log(f"  [{name}] Excel {progress['total_progress']:.0f}% but no prompts - Skip (in progress?)")
                            pass

        # v1.0.361: Check xem local có project nào đang chờ Chrome xử lý không
        # Nếu có → KHÔNG lấy mã mới (xong hết mới lấy tiếp)
        has_ongoing_project = False
        if self.local_projects.exists():
            for item in self.local_projects.iterdir():
                if not item.is_dir():
                    continue
                _name = item.name
                _srt = item / f"{_name}.srt"
                if not _srt.exists():
                    continue
                # Skip nếu đã copy về master (hoàn tất)
                if (item / "_COPIED_TO_VISUAL").exists():
                    continue
                # Có Excel với prompts nhưng chưa copy về master → Chrome đang làm
                _excel = item / f"{_name}_prompts.xlsx"
                if _excel.exists():
                    _prog = get_excel_progress(item, _name)
                    if _prog.get('is_complete', False):
                        # Excel xong, chưa copy master → đang chờ Chrome
                        log(f"  [{_name}] Excel done, waiting for Chrome + copy to master")
                        has_ongoing_project = True
                        break

        # v1.0.355: Dùng TaskQueue.claim_next() - claim atomic, tránh race condition
        # v1.0.361: Chỉ lấy mã mới khi KHÔNG có project đang chờ Chrome
        # v1.0.668: Diagnostic logging
        if not self.master_projects:
            log(f"  [SCAN] Skip master: master_projects not set")
        elif not safe_path_exists(self.master_projects):
            log(f"  [SCAN] Skip master: path NOT accessible: {self.master_projects}")
            # v1.0.668: Thu auto-detect lai master path
            if self.auto_path and safe_path_exists(self.auto_path):
                # Thu cac path pho bien
                for sub in ["ve3-tool-simple/PROJECTS", "PROJECTS"]:
                    alt = self.auto_path / sub
                    if safe_path_exists(alt):
                        log(f"  [SCAN] Found alternative: {alt}")
                        self.master_projects = alt
                        break
                else:
                    # List cac folder trong auto_path de debug
                    try:
                        items = [f.name for f in self.auto_path.iterdir() if f.is_dir()]
                        log(f"  [SCAN] Folders in {self.auto_path}: {items[:10]}")
                    except Exception as e:
                        log(f"  [SCAN] Cannot list {self.auto_path}: {e}")
        elif results:
            log(f"  [SCAN] Skip master: local has {len(results)} projects to process")
        elif has_ongoing_project:
            log(f"  [SCAN] Skip master: has ongoing project waiting for Chrome")

        if self.master_projects and safe_path_exists(self.master_projects) and not results and not has_ongoing_project:
            try:
                from modules.robust_copy import TaskQueue
                vm_id = TOOL_DIR.parent.name
                auto_path = self.auto_path or self.master_projects.parent.parent
                tq = TaskQueue(
                    master_projects=str(self.master_projects),
                    vm_id=vm_id,
                    visual_path=str(auto_path / "visual"),
                    tool_dir=str(TOOL_DIR),
                    log=lambda msg, lvl="INFO": log(f"{msg}", lvl),
                )

                # Check claim hiện tại (resume nếu có)
                my_claims = tq.get_my_claims()
                claimed_name = None
                if my_claims:
                    # v1.0.359: Skip claim đã hoàn thành Excel
                    for c in my_claims:
                        local_check = self.local_projects / c
                        if not local_check.exists():
                            # Chưa copy về local → cần resume
                            claimed_name = c
                            break
                        # Check xem Excel đã hoàn thành chưa
                        _excel = local_check / f"{c}_prompts.xlsx"
                        if not _excel.exists():
                            # Chưa có Excel → cần tạo
                            claimed_name = c
                            break
                        _prog = get_excel_progress(local_check, c)
                        if not _prog.get('is_complete', False):
                            # Excel chưa xong → cần resume
                            claimed_name = c
                            break
                        else:
                            log(f"  [QUEUE] Skip completed claim: {c}")

                if claimed_name:
                    log(f"  [QUEUE] Resume claim: {claimed_name}")
                else:
                    # Claim mã mới (1 mã duy nhất)
                    claimed_name = tq.claim_next(preferred_channel=channel_filter)
                    if claimed_name:
                        log(f"  [QUEUE] New claim: {claimed_name}")

                if claimed_name:
                    master_dir = self.master_projects / claimed_name
                    local_check = self.local_projects / claimed_name

                    # Skip if local already has it
                    if local_check.exists():
                        srt = local_check / f"{claimed_name}.srt"
                        if srt.exists():
                            excel_path = local_check / f"{claimed_name}_prompts.xlsx"
                            if not excel_path.exists():
                                results.append((local_check, claimed_name, "create_new", None))
                            else:
                                progress = get_excel_progress(local_check, claimed_name)
                                if not progress['is_complete']:
                                    if progress['can_resume']:
                                        results.append((local_check, claimed_name, "resume", progress))
                                    elif needs_api_completion(local_check, claimed_name):
                                        results.append((local_check, claimed_name, "fix_fallback", progress))
                    elif master_dir.exists():
                        # Copy từ master (đã claim rồi qua claim_next, không cần claim lại)
                        local_dir = import_from_master(master_dir, claimed_name, self.local_projects, already_claimed=True)
                        if local_dir:
                            excel_path = local_dir / f"{claimed_name}_prompts.xlsx"
                            if not excel_path.exists():
                                results.append((local_dir, claimed_name, "create_new", None))
                            else:
                                progress = get_excel_progress(local_dir, claimed_name)
                                if not progress['is_complete']:
                                    if progress['can_resume']:
                                        results.append((local_dir, claimed_name, "resume", progress))
                else:
                    log(f"  [QUEUE] No available projects on master")
            except ImportError:
                log(f"  [QUEUE] TaskQueue not available, skip master scan", "WARN")
            except (OSError, PermissionError) as e:
                log(f"Error scanning master: {e}", "WARN")

        return results

    def process_project(self, project_dir: Path, name: str, status: str, progress: dict = None) -> bool:
        """
        Process a single project với Agent Protocol.

        Args:
            project_dir: Thư mục project
            name: Tên project
            status: "create_new", "resume", hoặc "fix_fallback"
            progress: Dict từ get_excel_progress() nếu status là "resume"

        Returns:
            True nếu thành công
        """
        global _agent

        log(f"")
        log(f"{'='*60}")
        if status == "resume" and progress:
            log(f"Processing: {name} (RESUME - {progress['total_progress']}% done)")
        else:
            log(f"Processing: {name} ({status})")
        log(f"{'='*60}")

        # Update agent status
        task_id = f"excel_{name}_{datetime.now().strftime('%H%M%S')}"
        start_time = time.time()

        if _agent:
            initial_progress = progress.get('total_progress', 0) if progress else 0
            _agent.update_status(
                state="working",
                current_project=name,
                current_task=task_id,
                progress=initial_progress
            )

        # Process
        success = False
        error_msg = ""
        try:
            if status == "create_new":
                # Tạo Excel mới từ đầu
                success = create_excel_with_api(project_dir, name, log, agent=_agent)
            elif status == "resume":
                # Resume từ bước chưa hoàn thành
                log(f"Resuming from step: {progress.get('current_step', 'unknown')}")
                log(f"Incomplete steps: {', '.join(progress.get('incomplete_steps', []))}")
                if progress.get('flow_project_url'):
                    log(f"Reusing Flow project: {progress['flow_project_url']}")
                success = create_excel_with_api(project_dir, name, log, agent=_agent)
            elif status == "fix_fallback":
                # Fix Excel có [FALLBACK] prompts
                success = fix_excel_with_api(project_dir, name, log)
        except Exception as e:
            error_msg = str(e)
            log(f"Exception: {e}", "ERROR")

        # Calculate duration
        duration = time.time() - start_time

        # Report result to Agent
        if _agent:
            if success:
                self.completed_count += 1
                _agent.report_success(
                    task_id=task_id,
                    project_code=name,
                    task_type="excel",
                    duration=duration,
                    details={
                        "status": status,
                        "initial_progress": progress.get('total_progress', 0) if progress else 0
                    }
                )
            else:
                self.failed_count += 1
                _agent.report_failure(
                    task_id=task_id,
                    project_code=name,
                    task_type="excel",
                    error=error_msg or f"Failed to process {status}",
                    duration=duration,
                    details={
                        "status": status,
                        "initial_progress": progress.get('total_progress', 0) if progress else 0
                    }
                )

            # Update status back to idle
            _agent.update_status(
                state="idle",
                current_project="",
                current_task="",
                progress=100 if success else 0
            )

        return success

    def run_once(self) -> int:
        """
        Scan và process một lần.

        Returns:
            Số projects đã xử lý
        """
        log(f"Scanning for projects needing Excel...")

        projects = self.scan_projects_needing_excel()

        if not projects:
            log(f"No projects need Excel creation")
            return 0

        log(f"Found {len(projects)} projects:")
        for project_dir, name, status, progress in projects:
            if status == "resume" and progress:
                log(f"  - {name}: {status} ({progress['total_progress']}% done)")
            else:
                log(f"  - {name}: {status}")

        processed = 0
        for project_dir, name, status, progress in projects:
            try:
                if self.process_project(project_dir, name, status, progress):
                    processed += 1
            except KeyboardInterrupt:
                log("Interrupted by user")
                break
            except Exception as e:
                log(f"Error processing {name}: {e}", "ERROR")

        log(f"")
        log(f"Processed {processed}/{len(projects)} projects", "SUCCESS")
        return processed

    def run_loop(self):
        """Run continuous scan loop với Agent Protocol."""
        global _agent

        log(f"")
        log(f"{'='*60}")
        log(f"  EXCEL API WORKER - Continuous Mode")
        log(f"{'='*60}")
        log(f"  Channel: {self.channel or 'ALL'}")
        log(f"  Scan interval: {SCAN_INTERVAL}s (lan 1), {SCAN_INTERVAL_LONG}s/{SCAN_INTERVAL_LONG//60}min (tu lan 2)")
        log(f"  Local: {self.local_projects}")
        log(f"  Master: {self.master_projects or 'Not connected'}")
        log(f"  Agent: {'Enabled' if _agent else 'Disabled'}")
        log(f"")
        log(f"  Mode: CONTINUOUS - Tự động import từ master")
        log(f"{'='*60}")

        cycle = 0
        try:
            while True:
                cycle += 1
                log(f"")
                log(f"[CYCLE {cycle}] Starting scan...")

                # v1.0.666: Re-detect auto_path moi cycle (SMB co the mat ket noi)
                if not self.auto_path or not safe_path_exists(self.auto_path):
                    self.auto_path = detect_auto_path()
                    if self.auto_path:
                        self.master_projects = self.auto_path / "ve3-tool-simple" / "PROJECTS"
                        log(f"[SMB] Reconnected: {self.auto_path}")

                try:
                    self.run_once()
                except Exception as e:
                    log(f"Scan error: {e}", "ERROR")

                # v1.0.265: Lần đầu 60s, từ lần 2 trở đi 30 phút
                # Giảm tần suất mở Excel → ít conflict với Chrome workers
                interval = SCAN_INTERVAL if cycle == 1 else SCAN_INTERVAL_LONG
                log(f"")
                log(f"Waiting {interval}s ({interval//60}min)... (Ctrl+C to stop)")

                try:
                    time.sleep(interval)
                except KeyboardInterrupt:
                    log("Stopped by user")
                    break

        finally:
            # Cleanup agent khi thoát
            if _agent:
                log("Closing agent connection...")
                _agent.close()


# ================================================================================
# MAIN
# ================================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Excel API Worker - Tạo Excel prompts từ SRT"
    )
    parser.add_argument(
        "project",
        nargs="?",
        default=None,
        help="Project code (e.g., KA2-0001)"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Fix Excel có [FALLBACK] prompts"
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Chỉ quét, không tạo"
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Chạy loop liên tục"
    )

    args = parser.parse_args()

    print(f"""
{'='*60}
  EXCEL API WORKER
{'='*60}
""")

    worker = ExcelAPIWorker()

    # Single project mode
    if args.project:
        # Find project directory
        project_dir = worker.local_projects / args.project

        if not project_dir.exists() and worker.master_projects:
            project_dir = worker.master_projects / args.project

        if not project_dir.exists():
            log(f"Project not found: {args.project}", "ERROR")
            sys.exit(1)

        if args.fix:
            success = fix_excel_with_api(project_dir, args.project, log)
        else:
            success = create_excel_with_api(project_dir, args.project, log)

        sys.exit(0 if success else 1)

    # Scan mode
    if args.scan:
        projects = worker.scan_projects_needing_excel()
        if not projects:
            log("No projects need Excel")
        else:
            log(f"Found {len(projects)} projects:")
            for project_dir, name, status, progress in projects:
                if status == "resume" and progress:
                    log(f"  - {name}: {status} ({progress['total_progress']}% done)")
                else:
                    log(f"  - {name}: {status}")
        sys.exit(0)

    # Loop mode
    if args.loop:
        worker.run_loop()
    else:
        # Run once
        worker.run_once()


if __name__ == "__main__":
    main()
