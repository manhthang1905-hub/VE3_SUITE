#!/usr/bin/env python3
"""
VE3 Tool - Worker PIC BASIC Mode with Agent Protocol
=====================================================
Chrome Worker 1 - Xử lý scenes chẵn (2, 4, 6, ...)

Tích hợp Agent Protocol để:
- Báo cáo trạng thái cho VM Manager
- Ghi log chi tiết
- Báo cáo kết quả thành công/thất bại

Usage:
    python _run_chrome1.py                     (quét và xử lý tự động)
    python _run_chrome1.py AR47-0028           (chạy 1 project cụ thể)
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
from pathlib import Path
from datetime import datetime

# Add current directory to path
TOOL_DIR = Path(__file__).parent
sys.path.insert(0, str(TOOL_DIR))


# ================================================================================
# CHROME DATA MANAGEMENT (v1.0.107)
# ================================================================================

def clear_chrome_data_for_new_account():
    """
    v1.0.107: Xóa Chrome data để đăng nhập tài khoản mới.

    Gọi khi bắt đầu project MỚI (không phải resume) để:
    - Logout khỏi tài khoản cũ
    - Login tài khoản mới khi Chrome khởi động lại
    """
    import shutil

    # Chrome 1 data path (Chrome 1 là leader)
    chrome1_data = TOOL_DIR / "GoogleChromePortable" / "Data" / "profile"
    # Chrome 2 data path
    chrome2_data = TOOL_DIR / "GoogleChromePortable - Copy" / "Data" / "profile"

    cleared = []
    for data_path in [chrome1_data, chrome2_data]:
        if data_path.exists():
            # Xóa tất cả trừ "First Run" file
            first_run = data_path / "First Run"

            for item in data_path.iterdir():
                if item.name == "First Run":
                    continue
                try:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink()
                except Exception as e:
                    print(f"  Cannot delete {item.name}: {e}")

            # Tạo lại First Run nếu đã bị xóa
            if not first_run.exists():
                first_run.touch()

            cleared.append(data_path.parent.parent.name)

    if cleared:
        print(f"  [CHROME] Cleared data for: {', '.join(cleared)}")

    return len(cleared) > 0


# ================================================================================
# AGENT PROTOCOL
# ================================================================================

# Worker ID for this Chrome worker
WORKER_ID = "chrome_1"

# Agent Protocol - giao tiếp với VM Manager
try:
    from modules.agent_protocol import AgentWorker, ErrorType
    AGENT_ENABLED = True
except ImportError:
    AGENT_ENABLED = False
    AgentWorker = None

# Central Logger - để log hiển thị trong GUI
try:
    from modules.central_logger import get_logger
    _logger = get_logger(WORKER_ID)
except ImportError:
    class FakeLogger:
        def info(self, msg): print(f"[{WORKER_ID}] {msg}")
        def warn(self, msg): print(f"[{WORKER_ID}] WARN: {msg}")
        def error(self, msg): print(f"[{WORKER_ID}] ERROR: {msg}")
    _logger = FakeLogger()

# Global agent instance
_agent = None


# Override print CHÍNH XÁC - tránh recursion với central_logger
import builtins
_original_print = builtins.print

# Flag để tránh recursion khi central_logger gọi print
_in_logger = False

def _logger_print(*args, **kwargs):
    """Override print() to log to central_logger (tránh recursion)."""
    global _in_logger

    # Nếu đang trong logger, dùng print gốc
    if _in_logger:
        _original_print(*args, **kwargs)
        return

    try:
        _in_logger = True
        msg = ' '.join(str(arg) for arg in args)

        # Remove timestamp prefix if present (avoid duplication)
        if msg.startswith('[') and ']' in msg[:12]:
            msg = msg.split(']', 1)[-1].strip()

        if msg.strip():
            _logger.info(msg)
    finally:
        _in_logger = False

builtins.print = _logger_print


def log(msg: str, level: str = "INFO"):
    """Log to console + central logger + agent."""
    global _agent

    # Log to central logger (cho GUI)
    if level == "ERROR":
        _logger.error(msg)
    elif level == "WARN":
        _logger.warn(msg)
    else:
        _logger.info(msg)

    # Gửi đến Agent nếu có
    if _agent:
        if level == "ERROR":
            _agent.log_error(msg)
        else:
            _agent.log(msg, level)


def init_agent():
    """Khởi tạo Agent Protocol."""
    global _agent
    if AGENT_ENABLED and _agent is None:
        _agent = AgentWorker(WORKER_ID)
        _agent.start_status_updater(interval=5)
        _agent.update_status(state="idle")
        print(f"[{WORKER_ID}] Agent Protocol enabled")
    return _agent


def close_agent():
    """Đóng Agent Protocol."""
    global _agent
    if _agent:
        _agent.close()
        _agent = None


def safe_str(s) -> str:
    """Convert any value to a safe ASCII-friendly string."""
    try:
        text = str(s)
        # Replace non-ASCII characters with '?'
        return text.encode('ascii', errors='replace').decode('ascii')
    except:
        return "[encoding error]"


def agent_log(msg: str, level: str = "INFO"):
    """Log và gửi đến Agent + Central Logger (cho GUI)."""
    global _agent
    safe_msg = safe_str(msg)

    # Print to console
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {safe_msg}")

    # Send to central logger for GUI display
    if CENTRAL_LOGGER and central_log:
        central_log(WORKER_ID, safe_msg, level)

    # Send to agent
    if _agent:
        if level == "ERROR":
            _agent.log_error(safe_msg)
        else:
            _agent.log(safe_msg, level)

# Import từ run_worker (dùng chung logic)
from run_worker import (
    detect_auto_path,
    POSSIBLE_AUTO_PATHS,
    get_channel_from_folder,
    matches_channel,
    is_project_complete_on_master,
    has_excel_with_prompts,
    needs_api_completion,
    copy_from_master,
    copy_to_visual,
    delete_local_project,
    SCAN_INTERVAL,
)


def safe_path_exists(path: Path) -> bool:
    """
    Safely check if a path exists, handling network disconnection errors.
    Returns False if path doesn't exist OR if network is disconnected.
    """
    try:
        return path.exists()
    except (OSError, PermissionError) as e:
        # WinError 1167: The device is not connected
        # WinError 53: The network path was not found
        # WinError 64: The specified network name is no longer available
        print(f"  [WARN] Network error checking path: {e}")
        return False


def safe_iterdir(path: Path) -> list:
    """
    Safely iterate over a directory, handling network disconnection errors.
    Returns empty list if path doesn't exist OR if network is disconnected.
    """
    try:
        if not path.exists():
            return []
        return list(path.iterdir())
    except (OSError, PermissionError) as e:
        print(f"  [WARN] Network error listing directory: {e}")
        return []

# Detect paths
AUTO_PATH = detect_auto_path()
if AUTO_PATH:
    MASTER_PROJECTS = AUTO_PATH / "ve3-tool-simple" / "PROJECTS"
    MASTER_VISUAL = AUTO_PATH / "VISUAL"
else:
    MASTER_PROJECTS = Path(r"\\tsclient\D\AUTO\ve3-tool-simple\PROJECTS")
    MASTER_VISUAL = Path(r"\\tsclient\D\AUTO\VISUAL")

LOCAL_PROJECTS = TOOL_DIR / "PROJECTS"
WORKER_CHANNEL = get_channel_from_folder()

# v1.0.280: Registry trung tâm - nằm NGOÀI project dir, không bao giờ bị xóa theo project
ACCOUNT_REGISTRY = TOOL_DIR / "config" / ".project_accounts.json"


def _registry_save(project_code: str, channel: str, index: int, email: str):
    """Lưu account vào registry trung tâm (config/.project_accounts.json).
    File này nằm ngoài PROJECTS/ nên không bao giờ bị xóa cùng project.
    """
    import json, os
    try:
        registry = {}
        if ACCOUNT_REGISTRY.exists():
            try:
                registry = json.loads(ACCOUNT_REGISTRY.read_text(encoding="utf-8"))
            except Exception:
                registry = {}
        registry[project_code] = {"channel": channel, "index": index, "email": email}
        ACCOUNT_REGISTRY.parent.mkdir(exist_ok=True)
        # Atomic write: ghi temp rồi rename để tránh corrupt khi crash
        tmp = ACCOUNT_REGISTRY.parent / f".project_accounts_tmp_{os.getpid()}.json"
        tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(ACCOUNT_REGISTRY))
    except Exception as e:
        print(f"[Registry] Warn save: {e}")


def _registry_get(project_code: str) -> dict:
    """Đọc account từ registry trung tâm. Trả về {} nếu không có."""
    import json
    try:
        if ACCOUNT_REGISTRY.exists():
            registry = json.loads(ACCOUNT_REGISTRY.read_text(encoding="utf-8"))
            return registry.get(project_code, {})
    except Exception:
        pass
    return {}


def get_project_completion_percent(project_dir: Path, name: str) -> tuple:
    """
    Get project completion percentage.
    v1.0.104: Ưu tiên mã gần xong nhất.

    Returns:
        (percent, current, expected) - percent hoàn thành, số ảnh hiện tại, số ảnh cần
    """
    img_dir = project_dir / "img"
    if not img_dir.exists():
        return (0, 0, 0)

    img_files = list(img_dir.glob("*.png")) + list(img_dir.glob("*.jpg"))
    current = len(img_files)

    if current == 0:
        return (0, 0, 0)

    try:
        from modules.excel_manager import PromptWorkbook
        excel_path = project_dir / f"{name}_prompts.xlsx"
        if excel_path.exists():
            wb = PromptWorkbook(str(excel_path))
            wb.load_or_create()
            scenes = wb.get_scenes()
            expected = len([s for s in scenes if s.img_prompt])

            if expected == 0:
                return (0, current, 0)

            percent = int(current / expected * 100)
            return (percent, current, expected)
    except Exception as e:
        pass

    return (0, current, 0)


def is_local_pic_complete(project_dir: Path, name: str) -> bool:
    """Check if local project has ALL images created (both Chrome 1 and 2)."""
    percent, current, expected = get_project_completion_percent(project_dir, name)

    if expected == 0:
        if current > 0:
            print(f"    [{name}] Images: {current}/? - Excel invalid, treating as incomplete")
        return False

    if current >= expected:
        print(f"    [{name}] Images: {current}/{expected} - COMPLETE")
        return True
    else:
        print(f"    [{name}] Images: {current}/{expected} ({percent}%) - incomplete")
        return False


def wait_for_all_images(project_dir: Path, name: str, timeout: int = 86400) -> bool:
    """
    Đợi tất cả ảnh hoàn thành (Chrome 1 + Chrome 2).

    v1.0.287: Smart wait - chờ đến khi Chrome 2 THỰC SỰ không có tiến triển:
    - Tổng timeout: 24 giờ (tránh đợi vô tận khi có lỗi hệ thống)
    - No-progress timeout: 30 phút không có ảnh mới → Chrome 2 bị stuck → give up
    - Trước đây: timeout cứng 10 phút → Chrome 1 copy giữa chừng khi Chrome 2 vẫn đang chạy
    """
    import time
    NO_PROGRESS_TIMEOUT = 1800  # 30 phút không có ảnh mới = stuck
    start = time.time()
    last_count = -1
    last_progress_time = time.time()

    while time.time() - start < timeout:
        if is_local_pic_complete(project_dir, name):
            return True

        _, current, _ = get_project_completion_percent(project_dir, name)
        if current != last_count:
            last_count = current
            last_progress_time = time.time()  # Có ảnh mới → reset timer
        else:
            no_progress = int(time.time() - last_progress_time)
            if no_progress >= NO_PROGRESS_TIMEOUT:
                print(f"    [WARN] Chrome 2 stuck {no_progress}s không có ảnh mới → give up")
                return False

        elapsed = int(time.time() - start)
        no_prog = int(time.time() - last_progress_time)
        print(f"    Đợi Chrome 2... {current} ảnh, elapsed={elapsed}s, no_progress={no_prog}s")
        time.sleep(30)
    return False


def create_videos_for_project(project_dir: Path, code: str, callback=None) -> bool:
    """Tạo video cho project đã có ảnh."""
    def log(msg, level="INFO"):
        if callback:
            callback(msg, level)
        else:
            print(msg)

    try:
        from modules.smart_engine import SmartEngine

        excel_path = project_dir / f"{code}_prompts.xlsx"
        if not excel_path.exists():
            log(f"  No Excel found for video creation!", "ERROR")
            return False

        log(f"\n[VIDEO] Creating videos for {code}...")
        engine = SmartEngine(worker_id=0, total_workers=2)  # total_workers=2 tranh xoa Excel

        # Run với skip_video=False để tạo video
        # SmartEngine sẽ tự động skip ảnh đã tồn tại
        result = engine.run(
            str(excel_path),
            callback=callback,
            skip_compose=True,
            skip_video=False  # Tạo video
        )

        if result.get('error'):
            log(f"  Video error: {result.get('error')}", "ERROR")
            return False

        log(f"  [OK] Videos created!")
        return True

    except Exception as e:
        log(f"  Video exception: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        return False


def process_project_pic_basic(code: str, callback=None) -> bool:
    """Process a single project - BASIC mode (no IP rotation).

    NOTE: Chrome workers CHỈ tạo ảnh/video.
    Excel được tạo bởi Excel Worker (run_excel_api.py).
    """

    def log(msg, level="INFO"):
        if callback:
            callback(msg, level)
        else:
            print(msg)

    log(f"\n{'='*60}")
    log(f"[PIC BASIC] Processing: {code}")
    log(f"{'='*60}")

    # Step 1: Check if already done on master
    if is_project_complete_on_master(code):
        log(f"  Already in VISUAL folder, skip!")
        return True

    # Step 2: Copy from master
    local_dir = copy_from_master(code)
    if not local_dir:
        return False

    # Step 3: Check Excel - CHỈ XỬ LÝ NẾU ĐÃ CÓ EXCEL VỚI PROMPTS
    # Excel được tạo bởi Excel Worker, Chrome chỉ tạo ảnh
    excel_path = local_dir / f"{code}_prompts.xlsx"

    if not excel_path.exists():
        log(f"  No Excel found - waiting for Excel Worker to create it")
        return False

    if not has_excel_with_prompts(local_dir, code):
        log(f"  Excel exists but no prompts - waiting for Excel Worker to complete")
        return False

    # Step 3.5: v1.0.362 - Account tracking chỉ từ _CLAIMED
    try:
        claimed_path = local_dir / "_CLAIMED"
        if claimed_path.exists():
            claimed_lines = claimed_path.read_text(encoding='utf-8').strip().split('\n')
            if len(claimed_lines) >= 4 and claimed_lines[3].strip():
                parts = claimed_lines[3].strip().split('|')
                if len(parts) >= 2:
                    log(f"  [Account] _CLAIMED: {parts[0].strip()}")
    except Exception as e:
        log(f"  Account tracking error (non-critical): {e}", "WARN")

    # Step 4: Create images using SmartEngine (same as worker_pic)
    # Basic mode just means we created Excel with segment-based approach
    # Image generation uses the same SmartEngine
    try:
        from modules.smart_engine import SmartEngine

        # Chrome 1: worker_id=0, total_workers=2 (để chia scenes với Chrome 2)
        engine = SmartEngine(
            worker_id=0,
            total_workers=2  # Chia scenes chẵn/lẻ với Chrome 2
        )

        # v1.0.390: Đọc video_mode từ settings.yaml
        # basic/small = chỉ tạo ảnh, full = tạo ảnh + video
        import yaml
        _skip_video = True  # Default: basic mode
        try:
            settings_path = TOOL_DIR / "config" / "settings.yaml"
            if settings_path.exists():
                with open(settings_path, 'r', encoding='utf-8') as f:
                    _cfg = yaml.safe_load(f) or {}
                _video_mode = _cfg.get('video_mode', 'basic')
                _skip_video = _video_mode not in ('full',)
                log(f"  video_mode={_video_mode} → skip_video={_skip_video}")
        except Exception as e:
            log(f"  [WARN] Cannot read video_mode: {e}")

        log(f"  Excel: {excel_path.name}")
        log(f"  Mode: CHROME 1 (scenes chẵn: 2,4,6,... + nv/loc) {'+ VIDEO' if not _skip_video else ''}")

        result = engine.run(str(excel_path), callback=callback, skip_compose=True, skip_video=_skip_video)

        if result.get('error'):
            log(f"  Error: {result.get('error')}", "ERROR")
            return False

    except Exception as e:
        log(f"  Exception: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        return False

    # Step 5: Tạo marker _IMAGES_DONE nếu tất cả ảnh xong
    log(f"\n[STEP 5] Checking images...")
    if is_local_pic_complete(local_dir, code):
        try:
            (local_dir / "_IMAGES_DONE").touch()
            log(f"  [MARKER] Created _IMAGES_DONE marker")
        except Exception as e:
            log(f"  [WARN] Cannot create _IMAGES_DONE marker: {e}")

    # Step 6: Done
    log(f"\n[STEP 6] Done - GUI manager will handle copy to VISUAL + restart")
    return True


def cleanup_copied_projects():
    """
    v1.0.282: Xóa các folder đã có marker _COPIED_TO_VISUAL mà xóa lần trước chưa được.
    Gọi đầu mỗi cycle khi Chrome đang idle (không giữ lock Excel).
    """
    if not LOCAL_PROJECTS.exists():
        return
    for item in LOCAL_PROJECTS.iterdir():
        if not item.is_dir():
            continue
        if not (item / "_COPIED_TO_VISUAL").exists():
            continue
        try:
            import stat
            def _onerror(func, fpath, exc_info):
                try:
                    os.chmod(fpath, stat.S_IWRITE)
                    func(fpath)
                except Exception:
                    pass
            shutil.rmtree(item, onerror=_onerror)
            if not item.exists():
                print(f"  [DEL] Deferred delete success: {item.name}")
        except Exception:
            pass  # Sẽ thử lại cycle sau


def scan_incomplete_local_projects() -> list:
    """
    Scan local PROJECTS for incomplete projects.
    v1.0.104: Ưu tiên mã gần xong nhất (sort by completion % descending).
    """
    incomplete = []  # List of (code, percent, current, expected)

    if not LOCAL_PROJECTS.exists():
        return []

    for item in LOCAL_PROJECTS.iterdir():
        if not item.is_dir():
            continue

        code = item.name

        if not matches_channel(code):
            continue

        # v1.0.276: Skip neu co marker _COPIED_TO_VISUAL (xoa local that bai nhung da copy xong)
        if (item / "_COPIED_TO_VISUAL").exists():
            print(f"    [{code}] _COPIED_TO_VISUAL → xóa local để làm mã khác")
            try:
                shutil.rmtree(item, ignore_errors=True)
            except:
                pass
            continue

        if is_project_complete_on_master(code):
            print(f"    [{code}] Đã có ở VISUAL → xóa local để làm mã khác")
            try:
                shutil.rmtree(item, ignore_errors=True)
            except:
                pass
            continue

        if is_local_pic_complete(item, code):
            continue

        # v1.0.293: Safety net - nếu có marker _IMAGES_DONE thì skip
        # (Excel có thể bị lock tạm thời khiến is_local_pic_complete trả về False)
        # v1.0.303: Nếu _IMAGES_DONE tồn tại nhưng ảnh chưa đủ → marker cũ (stale) → xóa
        if (item / "_IMAGES_DONE").exists():
            print(f"    [{code}] _IMAGES_DONE marker found but images incomplete → removing stale marker")
            try:
                (item / "_IMAGES_DONE").unlink()
            except Exception as e:
                print(f"    [{code}] Cannot remove stale marker: {e}")
                continue  # Không xóa được → skip để tránh xử lý lại

        # Chrome Worker CHỈ xử lý projects có Excel với prompts (Step 7 done)
        # Projects chỉ có SRT → đợi Excel Worker hoàn thành trước
        if has_excel_with_prompts(item, code):
            # Get completion percentage for sorting
            percent, current, expected = get_project_completion_percent(item, code)
            print(f"    - {code}: {current}/{expected} ({percent}%) incomplete")
            incomplete.append((code, percent, current, expected))
        else:
            # Log để debug nhưng KHÔNG thêm vào list
            srt_path = item / f"{code}.srt"
            if srt_path.exists():
                print(f"    - {code}: has SRT, waiting for Excel Worker (Step 7)")

    # v1.0.104: Sort by completion % descending (highest first)
    # Ưu tiên mã gần xong để giải quyết backlog
    incomplete.sort(key=lambda x: x[1], reverse=True)

    # Log sorted order
    if incomplete:
        print(f"  [PRIORITY] Thứ tự ưu tiên (gần xong nhất trước):")
        for i, (code, percent, current, expected) in enumerate(incomplete[:5]):  # Show top 5
            print(f"    {i+1}. {code}: {percent}% ({current}/{expected})")

    # Return only codes
    return [x[0] for x in incomplete]


def scan_master_projects() -> list:
    """Scan master PROJECTS folder for pending projects.

    v1.0.349: Distributed mode - dùng TaskQueue, check _CLAIMED.
    Chỉ trả về 1 project (đã claim hoặc claim mới).
    """
    # === DISTRIBUTED MODE: claim-based (chỉ lấy 1 mã) ===
    try:
        from run_worker import _is_distributed_mode, VM_ID
        if _is_distributed_mode():
            try:
                from modules.robust_copy import TaskQueue
                tq = TaskQueue(
                    master_projects=str(MASTER_PROJECTS),
                    vm_id=VM_ID,
                    visual_path=str(MASTER_VISUAL),
                    tool_dir=str(TOOL_DIR),
                    log=lambda msg, lvl="INFO": print(f"  {msg}"),
                )
                # Check claim hiện tại
                my_claims = tq.get_my_claims()
                if my_claims:
                    print(f"  [QUEUE] Đang claim: {my_claims}")
                    return my_claims
                # Claim mã mới (1 mã duy nhất)
                code = tq.claim_next(preferred_channel=WORKER_CHANNEL)
                if code:
                    return [code]
                return []
            except Exception as e:
                print(f"  [WARN] TaskQueue error: {e}")
    except ImportError:
        pass

    # === FALLBACK: scan thủ công (check _CLAIMED) ===
    pending = []

    if not safe_path_exists(MASTER_PROJECTS):
        return pending

    for item in safe_iterdir(MASTER_PROJECTS):
        try:
            if not item.is_dir():
                continue

            code = item.name

            if not matches_channel(code):
                continue

            # v1.0.349: Skip if claimed by another VM
            claimed_file = item / "_CLAIMED"
            if claimed_file.exists():
                try:
                    claim_vm = claimed_file.read_text(encoding='utf-8').split('\n')[0].strip()
                    my_vm_id = TOOL_DIR.parent.name
                    if claim_vm != my_vm_id:
                        continue
                except Exception:
                    pass

            if is_project_complete_on_master(code):
                continue

            # v1.0.292: Skip nếu local đã có đủ ảnh
            local_dir = LOCAL_PROJECTS / code
            if local_dir.exists() and is_local_pic_complete(local_dir, code):
                continue

            # v1.0.293: Skip nếu local có marker _IMAGES_DONE
            if local_dir.exists() and (local_dir / "_IMAGES_DONE").exists():
                continue

            excel_path = item / f"{code}_prompts.xlsx"
            srt_path = item / f"{code}.srt"

            try:
                if has_excel_with_prompts(item, code):
                    print(f"    - {code}: ready (has prompts)")
                    pending.append(code)
                elif srt_path.exists():
                    print(f"    - {code}: has SRT")
                    pending.append(code)
            except (OSError, PermissionError) as e:
                print(f"  [WARN] Network error checking {code}: {e}")
                continue

            # v1.0.349: Chỉ lấy 1 mã (giống distributed mode)
            if pending:
                break

        except (OSError, PermissionError) as e:
            print(f"  [WARN] Network error scanning: {e}")
            break

    return sorted(pending)


def run_scan_loop():
    """Run continuous scan loop for IMAGE generation (BASIC mode)."""
    print(f"\n{'='*60}")
    print(f"  VE3 TOOL - WORKER PIC BASIC")
    print(f"{'='*60}")
    print(f"  Worker folder:   {TOOL_DIR.parent.name}")
    print(f"  Channel filter:  {WORKER_CHANNEL or 'ALL'}")
    print(f"  Mode:            BASIC (no IP rotation)")
    print(f"  Duration:        Segment-based (no 8s limit)")
    print(f"  Timeout:         5 hours per project")
    print(f"{'='*60}")

    cycle = 0

    # Track current project để không nhảy sang project khác
    current_project = None
    project_start_time = None
    PROJECT_TIMEOUT = 6 * 3600  # v1.0.66: 6 tiếng (thống nhất với vm_manager.py)

    while True:
        cycle += 1
        print(f"\n[BASIC CYCLE {cycle}] Scanning...")

        # v1.0.353: Chrome CHỈ scan local - Excel worker lo import từ master
        pending = scan_incomplete_local_projects()

        if not pending:
            print(f"  No pending projects")
            current_project = None  # Reset khi không còn project
            # Reset agent status để Chrome 2 biết
            if _agent:
                _agent.update_status(state="idle", current_project="")
            print(f"\n  Waiting {SCAN_INTERVAL}s... (Ctrl+C to stop)")
            try:
                time.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                print("\n\nStopped by user.")
                break
        else:
            print(f"  Found: {len(pending)} pending projects")

            # CHỈ XỬ LÝ 1 PROJECT - ưu tiên project đang làm dở
            if current_project and current_project in pending:
                target = current_project
                print(f"  Continuing: {target}")
            else:
                target = pending[0]
                current_project = target
                project_start_time = time.time()  # FIX v1.0.63: Track thời gian bắt đầu
                print(f"  Starting: {target}")
                # v1.0.289: Pre-login cho mỗi project MỚI (không chỉ lúc startup)
                # Đảm bảo Chrome đúng account + handle rotate/restore trước khi làm
                _do_pre_login_if_needed(target)

            # v1.0.291: Bỏ timeout 6 tiếng ở đây - GUI manager đã xử lý
            if project_start_time:
                elapsed = time.time() - project_start_time
                elapsed_hours = elapsed / 3600
                print(f"  [TIME] Project running: {elapsed_hours:.1f}h")

            # === UPDATE AGENT STATUS để Chrome 2 biết project đang làm ===
            if _agent:
                _agent.update_status(
                    state="working",
                    current_project=target
                )

            try:
                success = process_project_pic_basic(target)
                if not success:
                    print(f"  Project {target} incomplete, will retry...")
                else:
                    print(f"  Project {target} completed!")
                    current_project = None  # Move to next project
                    project_start_time = None
            except KeyboardInterrupt:
                print("\n\nStopped by user.")
                # Reset agent status
                if _agent:
                    _agent.update_status(state="idle", current_project="")
                return
            except Exception as e:
                print(f"  Error processing {target}: {safe_str(e)}")

            # === RESET AGENT STATUS khi xong project ===
            if _agent and success:
                _agent.update_status(
                    state="idle",
                    current_project=""
                )

            print(f"\n  Waiting {SCAN_INTERVAL}s... (Ctrl+C to stop)")
            try:
                time.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                print("\n\nStopped by user.")
                break


def main():
    import argparse
    parser = argparse.ArgumentParser(description='VE3 Worker PIC BASIC - Chrome 1')
    parser.add_argument('project', nargs='?', default=None, help='Project code')
    args = parser.parse_args()

    # Khởi tạo Agent Protocol
    init_agent()

    try:
        if args.project:
            # Single project mode
            success = process_project_with_agent(args.project)
            sys.exit(0 if success else 1)
        else:
            # Loop mode
            run_scan_loop_with_agent()
    finally:
        # Cleanup
        close_agent()


def process_project_with_agent(code: str) -> bool:
    """Process project với Agent Protocol."""
    global _agent

    task_id = f"image_{code}_{datetime.now().strftime('%H%M%S')}"
    start_time = time.time()

    # Get total scenes for progress tracking
    total_scenes = 0
    current_scene = [0]  # Use list to allow modification in callback

    try:
        from modules.excel_manager import PromptWorkbook
        local_dir = LOCAL_PROJECTS / code
        excel_path = local_dir / f"{code}_prompts.xlsx"
        if excel_path.exists():
            wb = PromptWorkbook(str(excel_path))
            total_scenes = len(wb.get_scenes())
    except:
        pass

    # Update agent status
    if _agent:
        _agent.update_status(
            state="working",
            current_project=code,
            current_task=task_id,
            current_scene=0,
            total_scenes=total_scenes,
            progress=0
        )

    # Callback to track progress
    def progress_callback(msg, level="INFO"):
        """Callback that also updates agent with scene progress."""
        print(msg)

        # v1.0.65: Parse progress from log format [34/445] ID: 123
        if _agent:
            import re
            # Format: [current/total] ID: scene_id
            match = re.search(r'\[(\d+)/(\d+)\]\s*ID:\s*(\S+)', msg)
            if match:
                current_idx = int(match.group(1))  # 34
                total_idx = int(match.group(2))    # 445
                scene_id = match.group(3)          # 123

                current_scene[0] = current_idx
                progress = int((current_idx / total_idx * 100) if total_idx > 0 else 0)
                _agent.update_status(
                    current_scene=current_idx,
                    total_scenes=total_idx,
                    step_name=f"scene_{scene_id}",  # Show actual scene ID
                    progress=progress
                )

    # Process
    try:
        success = process_project_pic_basic(code, callback=progress_callback)
        duration = time.time() - start_time

        # Report result
        if _agent:
            if success:
                _agent.report_success(
                    task_id=task_id,
                    project_code=code,
                    task_type="image",
                    duration=duration
                )
            else:
                _agent.report_failure(
                    task_id=task_id,
                    project_code=code,
                    task_type="image",
                    error="Processing failed",
                    duration=duration
                )
            _agent.update_status(
                state="idle",
                current_project="",
                current_task="",
                current_scene=0,
                total_scenes=0,
                progress=0
            )

        return success

    except Exception as e:
        duration = time.time() - start_time
        if _agent:
            _agent.report_failure(
                task_id=task_id,
                project_code=code,
                task_type="image",
                error=str(e),
                duration=duration
            )
            _agent.update_status(
                state="error",
                current_project="",
                current_task="",
                current_scene=0,
                total_scenes=0,
                progress=0
            )
        raise


def _do_pre_login_if_needed(project_code: str = None):
    """
    v1.0.289: PRE-LOGIN cho từng project mới (không chỉ lúc startup).

    Flow:
    1. Nếu có project_code → dùng luôn (gọi từ scan loop)
    2. Không có → tự scan (gọi lúc startup)
    3. Kiểm tra registry/ảnh → rotate hoặc restore account
    4. Clear Chrome data + login cả 2 Chrome
    """
    print("\n[PRE-LOGIN] Checking if login needed...")

    # v1.0.521: Skip login khi mode "server" (khong can Chrome local)
    try:
        import yaml
        _settings_path = TOOL_DIR / "config" / "settings.yaml"
        if _settings_path.exists():
            with open(_settings_path, 'r', encoding='utf-8') as f:
                _gen_mode = (yaml.safe_load(f) or {}).get('generation_mode', 'api')
            if _gen_mode == 'server':
                print("[PRE-LOGIN] Mode=SERVER - skip Chrome login (dung server, khong can Chrome local)")
                return
    except:
        pass

    if project_code:
        code = project_code
    else:
        # Startup: tự scan
        pending = scan_incomplete_local_projects()
        if not pending:
            print("[PRE-LOGIN] No pending projects - skip login")
            return
        code = pending[0]

    project_dir = LOCAL_PROJECTS / code
    excel_path = project_dir / f"{code}_prompts.xlsx"

    print(f"[PRE-LOGIN] Project: {code}")

    try:
        from google_login import (
            detect_machine_code,
            login_google_chrome,
        )

        machine_code = detect_machine_code()
        print(f"[PRE-LOGIN] Machine code: {machine_code}")

        # v1.0.352: Đọc _CLAIMED từ LOCAL hoặc MASTER (vì pre-login chạy trước copy_from_master)
        _claimed_path = project_dir / "_CLAIMED"
        _claimed_account_dict = None

        # Nếu local chưa có _CLAIMED → đọc từ master
        if not _claimed_path.exists():
            try:
                _master_claimed = MASTER_PROJECTS / code / "_CLAIMED"
                if _master_claimed.exists():
                    # Copy về local luôn
                    import shutil
                    project_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(_master_claimed), str(_claimed_path))
                    print(f"[PRE-LOGIN] Copied _CLAIMED from master to local")
            except Exception as e:
                print(f"[PRE-LOGIN] Cannot copy _CLAIMED from master: {e}")

        if _claimed_path.exists():
            try:
                _claimed_lines = _claimed_path.read_text(encoding='utf-8').strip().split('\n')
                if len(_claimed_lines) >= 4 and _claimed_lines[3].strip():
                    _claimed_str = _claimed_lines[3].strip()  # email|pass|totp
                    _parts = _claimed_str.split('|')
                    if len(_parts) >= 2:
                        _claimed_account_dict = {
                            'id': _parts[0].strip(),
                            'password': _parts[1].strip(),
                            'totp_secret': _parts[2].strip() if len(_parts) >= 3 else '',
                            'index': 0,
                        }
                        print(f"[PRE-LOGIN] _CLAIMED account: {_claimed_account_dict['id']}")
            except Exception as e:
                print(f"[PRE-LOGIN] Error reading _CLAIMED: {e}")

        if _claimed_account_dict:
            # v1.0.362: CHỈ dùng account từ _CLAIMED
            current_account = _claimed_account_dict
            print(f"[PRE-LOGIN] Dung account tu _CLAIMED: {current_account['id']}")
        else:
            print("[PRE-LOGIN] Khong tim thay _CLAIMED - skip login")
            return

        print(f"[PRE-LOGIN] Account: {current_account['id']}")

        # Xóa Chrome data
        print("[PRE-LOGIN] Clearing Chrome data...")
        clear_chrome_data_for_new_account()

        # Login CẢ 2 Chrome TUẦN TỰ
        chrome1_exe = str(TOOL_DIR / "GoogleChromePortable" / "GoogleChromePortable.exe")
        # v1.0.571: Lay proxy arg de login cung dung proxy
        from google_login import get_proxy_arg_from_settings
        _proxy_arg = get_proxy_arg_from_settings()
        if _proxy_arg:
            print(f"[PRE-LOGIN] Proxy: {_proxy_arg}")

        # v1.0.659: PHAI login thanh cong moi tiep tuc - khong bo qua
        # Mang IPv6 khong on dinh → co the fail nhieu lan → cu retry cho den khi duoc
        max_login_retries = 10  # Toi da 10 lan moi Chrome

        print("[PRE-LOGIN] Logging into Chrome 1...")
        chrome1_ok = False
        for _login_try in range(max_login_retries):
            result = login_google_chrome(current_account, chrome_portable=chrome1_exe, worker_id=0, proxy_arg=_proxy_arg)
            if result:
                chrome1_ok = True
                print("[PRE-LOGIN] Chrome 1 login OK!")
                break
            else:
                print(f"[PRE-LOGIN] Chrome 1 login FAILED ({_login_try+1}/{max_login_retries}), doi 10s roi thu lai...")
                time.sleep(10)

        if not chrome1_ok:
            print("[PRE-LOGIN] Chrome 1 login FAILED sau 10 lan! Kiem tra tai khoan/mang.")
            return  # Dung - khong the tiep tuc khi chua login

        chrome2_exe = str(TOOL_DIR / "GoogleChromePortable - Copy" / "GoogleChromePortable.exe")
        print("[PRE-LOGIN] Logging into Chrome 2...")
        chrome2_ok = False
        for _login_try in range(max_login_retries):
            result = login_google_chrome(current_account, chrome_portable=chrome2_exe, worker_id=1, proxy_arg=_proxy_arg)
            if result:
                chrome2_ok = True
                print("[PRE-LOGIN] Chrome 2 login OK!")
                break
            else:
                print(f"[PRE-LOGIN] Chrome 2 login FAILED ({_login_try+1}/{max_login_retries}), doi 10s roi thu lai...")
                time.sleep(10)

        if not chrome2_ok:
            print("[PRE-LOGIN] Chrome 2 login FAILED sau 10 lan! Tiep tuc voi Chrome 1.")

    except Exception as e:
        print(f"[PRE-LOGIN] Error (non-critical): {e}")

    print("[PRE-LOGIN] Done!\n")


def run_scan_loop_with_agent():
    """Run scan loop với Agent Protocol."""
    global _agent

    print(f"\n{'='*60}")
    print(f"  CHROME WORKER 1 - PIC BASIC MODE")
    print(f"  Agent: {'Enabled' if _agent else 'Disabled'}")
    print(f"{'='*60}\n")

    # v1.0.291: Bỏ startup pre-login - scan loop tự gọi _do_pre_login_if_needed(target)
    # cho mỗi project mới → tránh double pre-login

    cycle = 0
    current_project = None  # Track project đang làm

    try:
        while True:
            cycle += 1
            print(f"\n[CYCLE {cycle}] Scanning...")

            # v1.0.282: Dọn dẹp folder đã copy sang VISUAL nhưng chưa xóa được
            cleanup_copied_projects()

            # v1.0.353: Chrome CHỈ scan local - Excel worker lo import từ master
            projects = scan_incomplete_local_projects()

            if projects:
                # CHỈ XỬ LÝ 1 PROJECT - ưu tiên project đang làm dở
                if current_project and current_project in projects:
                    target = current_project
                    print(f"  Continuing: {target}")
                else:
                    target = projects[0]
                    current_project = target
                    print(f"  Starting: {target}")
                    # v1.0.292: Pre-login cho project mới
                    _do_pre_login_if_needed(target)

                try:
                    success = process_project_with_agent(target)
                    if success:
                        print(f"  Project {target} completed!")
                        current_project = None  # Move to next project
                    else:
                        print(f"  Project {target} incomplete, will retry...")
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    agent_log(f"Error processing {target}: {e}", "ERROR")
            else:
                current_project = None  # Reset khi không còn project

            print(f"\nWaiting {SCAN_INTERVAL}s... (Ctrl+C to stop)")
            time.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        print("\n\nStopped by user.")


if __name__ == "__main__":
    main()
