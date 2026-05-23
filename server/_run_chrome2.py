#!/usr/bin/env python3
"""
VE3 Tool - Worker PIC BASIC Mode - CHROME 2 with Agent Protocol
================================================================
Chrome Worker 2 - Xử lý scenes lẻ (1, 3, 5, ...)

Tích hợp Agent Protocol để:
- Báo cáo trạng thái cho VM Manager
- Ghi log chi tiết
- Báo cáo kết quả thành công/thất bại

Usage:
    python _run_chrome2.py                     (quét và xử lý tự động)
    python _run_chrome2.py AR47-0028           (chạy 1 project cụ thể)
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
from typing import Optional

# Add current directory to path
TOOL_DIR = Path(__file__).parent
sys.path.insert(0, str(TOOL_DIR))

# ================================================================================
# AGENT PROTOCOL
# ================================================================================

# Worker ID for this Chrome worker
WORKER_ID = "chrome_2"

# Agent Protocol - giao tiếp với VM Manager
try:
    from modules.agent_protocol import AgentWorker, AgentManager, ErrorType
    AGENT_ENABLED = True
except ImportError:
    AGENT_ENABLED = False
    AgentWorker = None
    AgentManager = None

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


def get_chrome2_path():
    """Get chrome_portable_2 from settings.yaml."""
    import yaml
    config_path = TOOL_DIR / "config" / "settings.yaml"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        chrome2 = config.get('chrome_portable_2', '')
        if chrome2:
            return chrome2

    # Auto-detect
    copy_chrome = TOOL_DIR / "GoogleChromePortable - Copy" / "GoogleChromePortable.exe"
    if copy_chrome.exists():
        return str(copy_chrome)
    return None


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


def is_chrome2_odd_scenes_complete(project_dir: Path, name: str) -> bool:
    """
    Check if Chrome 2's ODD scenes are complete.

    Chrome 2 chỉ xử lý scenes LẺ (1, 3, 5, ...).
    Nếu tất cả scenes lẻ đã có ảnh → Chrome 2 xong việc.
    Scenes chẵn là việc của Chrome 1.
    """
    img_dir = project_dir / "img"
    if not img_dir.exists():
        return False

    try:
        from modules.excel_manager import PromptWorkbook
        excel_path = project_dir / f"{name}_prompts.xlsx"
        if not excel_path.exists():
            return False

        wb = PromptWorkbook(str(excel_path))
        wb.load_or_create()
        scenes = wb.get_scenes()

        # Lấy tất cả scene IDs có prompt
        all_scene_ids = []
        for s in scenes:
            if s.img_prompt:
                try:
                    scene_num = int(s.scene_id)
                    all_scene_ids.append(scene_num)
                except (ValueError, TypeError):
                    pass

        if not all_scene_ids:
            return False

        # Chrome 2 (worker_id=1, total_workers=2) xử lý scenes theo modulo
        # scene_num % 2 == worker_id % 2 = 1 % 2 = 1
        # → Chrome 2 xử lý scenes có số lẻ (1, 3, 5, 7, 9, ...)
        odd_scenes = [s for s in all_scene_ids if s % 2 == 1]

        if not odd_scenes:
            print(f"    [{name}] Chrome2: No odd scenes to process")
            return True  # Không có scenes lẻ nào → xong việc

        # Kiểm tra xem tất cả scenes lẻ đã có ảnh chưa
        missing_odd = []
        for scene_num in odd_scenes:
            # Tìm file ảnh cho scene này
            img_file = img_dir / f"scene_{scene_num:03d}.png"
            img_file_alt = img_dir / f"{scene_num}.png"

            if not img_file.exists() and not img_file_alt.exists():
                missing_odd.append(scene_num)

        if missing_odd:
            print(f"    [{name}] Chrome2: {len(odd_scenes) - len(missing_odd)}/{len(odd_scenes)} odd scenes done")
            print(f"    [{name}] Chrome2: Missing odd scenes: {missing_odd[:10]}{'...' if len(missing_odd) > 10 else ''}")
            return False
        else:
            print(f"    [{name}] Chrome2: ALL {len(odd_scenes)} odd scenes COMPLETE!")
            return True

    except Exception as e:
        print(f"    [{name}] Chrome2 check error: {e}")
        return False


def process_project_pic_basic_chrome2(code: str, callback=None) -> bool:
    """Process a single project - CHROME 2."""

    def log(msg, level="INFO"):
        if callback:
            callback(msg, level)
        else:
            print(f"[Chrome2] {msg}")

    log(f"\n{'='*60}")
    log(f"[PIC BASIC CHROME2] Processing: {code}")
    log(f"{'='*60}")

    # Get Chrome 2 path
    chrome2_path = get_chrome2_path()
    if not chrome2_path:
        log(f"  ERROR: chrome_portable_2 not found!", "ERROR")
        return False
    log(f"  Chrome2: {chrome2_path}")

    # Step 1: Check if already done on master
    if is_project_complete_on_master(code):
        log(f"  Already in VISUAL folder, skip!")
        return True

    # Step 2: Copy from master
    local_dir = copy_from_master(code)
    if not local_dir:
        return False

    # Step 3: Check Excel - Chrome 2 KHÔNG tạo Excel, đợi Chrome 1
    excel_path = local_dir / f"{code}_prompts.xlsx"

    if not excel_path.exists():
        log(f"  Waiting for Chrome 1 to create Excel...")
        # Đợi tối đa 120s
        for i in range(24):
            time.sleep(5)
            if excel_path.exists():
                log(f"  Excel found!")
                break
            log(f"  Waiting... ({(i+1)*5}s)")

        if not excel_path.exists():
            log(f"  No Excel after 120s, skip!")
            return False

    # Step 3.5a: v1.0.362 - Account tracking chỉ từ _CLAIMED
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

    # Step 3.5b: VALIDATOR - Kiểm tra tất cả NV references trước khi tạo scenes
    log(f"  ============================================================")
    log(f"  STEP 3.5: REFERENCE VALIDATOR")
    log(f"  ============================================================")

    # v1.0.412: Skip validator khi Chrome mode (không dùng API)
    _generation_mode = 'api'
    try:
        import yaml as _yaml
        _cfg_path = Path("config/settings.yaml")
        if _cfg_path.exists():
            with open(_cfg_path, 'r', encoding='utf-8') as _f:
                _cfg = _yaml.safe_load(_f) or {}
                _generation_mode = _cfg.get('generation_mode', 'api')
    except:
        pass

    _skip_validator = (_generation_mode == 'chrome')
    if _skip_validator:
        log(f"  [SKIP] Chrome mode - không cần validator (không có media_id)")
        log(f"  Chrome 1 tạo references trực tiếp, Chrome 2 chỉ tạo scenes")

    try:
        if _skip_validator:
            raise Exception("SKIP_VALIDATOR")
        from modules.excel_manager import PromptWorkbook

        # BƯỚC 1: LẬP KẾ HOẠCH - Đọc Excel để biết có bao nhiêu references cần validate
        workbook = PromptWorkbook(str(excel_path))
        all_chars = workbook.get_characters()

        # Đếm references cần validate (cả NV và LOC, bỏ qua skip=True hoặc đã verified)
        refs_to_validate = []
        for char in all_chars:
            # Kiểm tra cả NV và LOC
            if not char.id.lower().startswith(('nv', 'loc')):
                continue

            # Bỏ qua references có skip=True
            skip = getattr(char, 'skip', False)
            if skip:
                log(f"  [SKIP] {char.id} - marked as skip")
                continue

            # Bỏ qua references đã verified rồi
            status = getattr(char, 'status', '')
            if status in ['verified', 'verified_fixed']:
                log(f"  [SKIP] {char.id} - already verified (status={status})")
                continue

            refs_to_validate.append(char.id)

        if not refs_to_validate:
            log(f"  [v] All references already validated, skip validator mode")
        else:
            nv_count = len([r for r in refs_to_validate if r.lower().startswith('nv')])
            loc_count = len([r for r in refs_to_validate if r.lower().startswith('loc')])
            log(f"  [PLAN] Cần validate {len(refs_to_validate)} references: {nv_count} NV + {loc_count} LOC")
            log(f"  {refs_to_validate}")

            # Lấy project URL từ Excel (config sheet)
            project_url = None
            try:
                import openpyxl
                wb_openpyxl = openpyxl.load_workbook(str(excel_path), read_only=True)
                if 'config' in wb_openpyxl.sheetnames:
                    config_sheet = wb_openpyxl['config']
                    for row in config_sheet.iter_rows(min_row=2):  # Skip header
                        if row[0].value == 'flow_project_url':
                            project_url = row[1].value
                            break
                wb_openpyxl.close()

                if project_url:
                    log(f"  [v] Project URL from Excel: {project_url[:60]}...")
                else:
                    log(f"  [!] No project URL in Excel yet - will wait for Chrome 1...")
            except Exception as e:
                log(f"  [WARN] Could not read project URL from Excel: {e}")

            # Track validated NVs
            validated_refs = set()

            # Setup Chrome API cho validator (tạo 1 lần, dùng chung)
            validator_api = None
            validator = None

            try:
                from modules.reference_validator import ReferenceValidator
                from modules.drission_flow_api import DrissionFlowAPI
                import yaml

                # Load config
                config_file = Path("config/settings.yaml")
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)

                # === LOCAL SERVER MODE: Skip Chrome validator, dung server ===
                local_server_enabled = config.get('local_server_enabled', False)
                local_server_url = config.get('local_server_url', '')
                if local_server_enabled and local_server_url:
                    log(f"  [VALIDATOR] LOCAL SERVER MODE - skip Chrome validator")
                    log(f"  [VALIDATOR] Server se validate references khi tao anh")
                    # Mark all refs as verified (server handles validation)
                    for ref_id in refs_to_validate:
                        workbook.update_character(ref_id, status="verified")
                    workbook.save()
                    validated_refs = set(refs_to_validate)
                    raise Exception("SKIP_VALIDATOR")

                # Nếu chưa có URL, đợi Chrome 1 tạo xong project TRƯỚC KHI mở Chrome validator
                if not project_url:
                    log(f"  [WAIT] No project URL yet - waiting for Chrome 1 to create Flow project...")
                    wait_url_interval = 15
                    waited_url = 0
                    max_wait_url = 1800  # 30 phút
                    while not project_url and waited_url < max_wait_url:
                        time.sleep(wait_url_interval)
                        waited_url += wait_url_interval
                        try:
                            _wb = openpyxl.load_workbook(str(excel_path), read_only=True)
                            if 'config' in _wb.sheetnames:
                                for _row in _wb['config'].iter_rows(min_row=2):
                                    if _row[0].value == 'flow_project_url':
                                        project_url = _row[1].value
                                        break
                            _wb.close()
                        except Exception:
                            pass
                        if project_url:
                            log(f"  [v] Got project URL after {waited_url}s: {project_url[:60]}...")
                        else:
                            log(f"  [WAIT] Still no project URL ({waited_url}s)...")

                if not project_url:
                    log(f"  [ERROR] Timeout waiting for project URL!", "error")
                    raise Exception("No project URL after timeout")

                # Setup Chrome 2 API (SAU KHI có URL)
                chrome_path = chrome2_path
                validator_api = DrissionFlowAPI(
                    chrome_portable=chrome_path,
                    worker_id=2,
                    total_workers=2,
                    headless=False,
                    webshare_enabled=False
                )
                # v1.0.392: Validator mode - skip cleanup+restart sau mỗi ảnh
                validator_api._validator_mode = True

                log(f"  [VALIDATOR] Starting Chrome with project URL...")
                if not validator_api.setup(project_url=project_url):
                    log(f"  [ERROR] Failed to start Chrome!", "error")
                    raise Exception("Chrome setup failed")

                # Create validator
                validator = ReferenceValidator(
                    drission_api=validator_api,
                    workbook=workbook,
                    config=config,
                    project_code=code
                )

                log(f"  [v] Validator ready!")

            except Exception as e:
                if "SKIP_VALIDATOR" not in str(e):
                    log(f"  [ERROR] Failed to setup validator: {e}", "error")
                    import traceback
                    traceback.print_exc()
                # Skip validation
                validated_refs = set(refs_to_validate)

            # BƯỚC 2: CHỜ + VALIDATE TỪNG NV CÓ MEDIA_ID
            log(f"  [VALIDATOR] Waiting for Chrome 1 to create references...")
            log(f"  [VALIDATOR] Will validate each NV as soon as it has media_id...")

            wait_interval = 10
            waited = 0

            while len(validated_refs) < len(refs_to_validate) and validator:
                # Re-load Excel để check media_id mới
                workbook = PromptWorkbook(str(excel_path))
                all_chars = workbook.get_characters()

                # Tìm NV nào có media_id mà chưa validate
                for char in all_chars:
                    if char.id not in refs_to_validate:
                        continue
                    if char.id in validated_refs:
                        continue

                    # Check status
                    status = getattr(char, 'status', '')
                    if status in ['verified', 'verified_fixed']:
                        # Đã validate rồi
                        validated_refs.add(char.id)
                        log(f"  [v] {char.id} already validated (status={status})")
                        continue

                    # Check media_id
                    if not char.media_id:
                        continue  # Chưa có media_id, chờ tiếp

                    # CÓ MEDIA_ID → VALIDATE NGAY!
                    log(f"  ")
                    log(f"  [VALIDATOR] Found {char.id} with media_id!")
                    log(f"  [VALIDATOR] Validating {char.id}... ({len(validated_refs)+1}/{len(refs_to_validate)})")

                    # Validate
                    try:
                        result = validator.validate_and_fix(char.id)
                        log(f"  [VALIDATOR] {char.id} result: {result}")
                    except Exception as e:
                        log(f"  [ERROR] Validation failed for {char.id}: {e}", "error")
                        import traceback
                        traceback.print_exc()

                    # Mark validated
                    validated_refs.add(char.id)

                # Check progress
                if len(validated_refs) >= len(refs_to_validate):
                    log(f"  ")
                    log(f"  [v] VALIDATOR COMPLETED!")
                    log(f"  [v] Validated {len(validated_refs)}/{len(refs_to_validate)} references")
                    break

                # Log progress
                if waited % 30 == 0:
                    log(f"  [WAIT] Validated: {len(validated_refs)}/{len(refs_to_validate)} - Waiting... ({waited}s)")

                time.sleep(wait_interval)
                waited += wait_interval

            # Close validator API
            if validator_api:
                try:
                    validator_api.close()
                    log(f"  [v] Validator Chrome closed")
                except:
                    pass

    except Exception as e:
        if "SKIP_VALIDATOR" not in str(e):
            log(f"  [ERROR] Validator error: {e}", "error")
            import traceback
            traceback.print_exc()
            # Cleanup
            if 'validator_api' in dir() and validator_api:
                try:
                    validator_api.close()
                except:
                    pass

    # Step 3.7: Generate thumbnails (sau khi validate references, trước khi tạo scenes)
    log(f"  ============================================================")
    log(f"  STEP 3.7: THUMBNAIL GENERATION")
    log(f"  ============================================================")

    try:
        from modules.excel_manager import PromptWorkbook
        from modules.smart_engine import SmartEngine

        # Đợi step_8 (thumbnail prompts) hoàn thành - tối đa 10 phút
        max_wait_step8 = 600
        waited_step8 = 0
        wait_interval_step8 = 15
        step8_done = False

        while waited_step8 < max_wait_step8:
            wb_check = PromptWorkbook(str(excel_path))
            wb_check.load_or_create()
            step8_status = wb_check.get_step_status("step_8")

            # step_8 không tồn tại trong Excel cũ → skip
            if not step8_status:
                log(f"  [INFO] step_8 not in Excel (old format), skip thumbnails")
                break

            status_val = step8_status.get("status", "")

            if status_val == "COMPLETED":
                log(f"  [v] step_8 COMPLETED - ready to generate thumbnails")
                step8_done = True
                break
            elif status_val in ("SKIPPED", "ERROR"):
                log(f"  [WARN] step_8 status={status_val}, skip thumbnail generation", "WARN")
                break

            log(f"  [WAIT] step_8 status={status_val or 'PENDING'} ({waited_step8}s/{max_wait_step8}s)...")
            time.sleep(wait_interval_step8)
            waited_step8 += wait_interval_step8

        if not step8_done:
            log(f"  [WARN] step_8 not completed after {max_wait_step8}s, skip thumbnails", "WARN")
        else:
            # v1.0.322: Đợi Chrome 1 tạo xong NV references (có media_id) trước khi thumbnail
            max_wait_nv = 1800  # tối đa 30 phút
            waited_nv = 0
            wait_interval_nv = 15
            nv_ready = False

            # v1.0.412: Chrome mode không có media_id → skip đợi media_id
            if _skip_validator:
                log(f"  [v] Chrome mode: skip đợi media_id → thumbnails dùng Chrome UI giống scenes")
                nv_ready = True

            while not nv_ready and waited_nv < max_wait_nv:
                try:
                    wb_nv = PromptWorkbook(str(excel_path))
                    wb_nv.load_or_create()
                    chars = wb_nv.get_characters()
                    # v1.0.539: Deduplicate theo ID + filter is_child
                    # Excel co the co 2 dong cung ID (vd: nvc tre va nvc gia)
                    # Chi can 1 dong co media_id la du cho ID do
                    nv_all = [c for c in chars if c.id.lower().startswith('nv') and not getattr(c, 'skip', False) and not getattr(c, 'is_child', False)]

                    # v1.0.322: Nếu chars rỗng → Excel chưa sẵn sàng → đợi tiếp
                    if not chars:
                        log(f"  [WAIT] get_characters() trả về rỗng (file lock?) - đợi... ({waited_nv}s)")
                        time.sleep(wait_interval_nv)
                        waited_nv += wait_interval_nv
                        continue

                    # Deduplicate: gom theo ID, chi can 1 co media_id la OK
                    nv_ids = {}  # id -> has_media_id
                    for c in nv_all:
                        cid = c.id.lower()
                        has_mid = bool(getattr(c, 'media_id', None))
                        # Neu da co True roi thi giu True
                        nv_ids[cid] = nv_ids.get(cid, False) or has_mid

                    unique_nv_count = len(nv_ids)
                    nv_with_media_count = sum(1 for v in nv_ids.values() if v)

                    # Không có NV characters (chỉ có locations) → không cần đợi
                    if unique_nv_count == 0:
                        log(f"  [v] Không có NV characters ({len(chars)} chars total) - không cần đợi media_id")
                        nv_ready = True
                        break

                    # v1.0.540: Chap nhan 80% media_id (khong doi 100%)
                    # Neu 1-2 ref bi fail/timeout, Chrome 2 van chay duoc voi ref da co
                    media_ratio = nv_with_media_count / unique_nv_count if unique_nv_count > 0 else 1.0
                    if media_ratio >= 0.8 or nv_with_media_count >= unique_nv_count:
                        log(f"  [v] NV references ready ({nv_with_media_count}/{unique_nv_count} unique IDs = {media_ratio:.0%} media_id)")
                        nv_ready = True
                        break

                    if waited_nv == 0:
                        nv_dir = local_dir / "nv"
                        nv_pngs = list(nv_dir.glob("nv*.png")) if nv_dir.exists() else []
                        log(f"  [INFO] NV unique IDs: {unique_nv_count}, media_id: {nv_with_media_count}, pngs: {len(nv_pngs)}")

                    log(f"  [WAIT] NV media_id: {nv_with_media_count}/{unique_nv_count} unique IDs - đợi Chrome 1... ({waited_nv}s)")
                except Exception as e:
                    log(f"  [WAIT] Đọc Excel lỗi: {e} - đợi... ({waited_nv}s)")
                time.sleep(wait_interval_nv)
                waited_nv += wait_interval_nv
            else:
                log(f"  [WARN] NV references chưa có media_id sau {max_wait_nv}s, vẫn tiếp tục thumbnail", "WARN")
                nv_ready = True  # Timeout → tiếp tục dù chưa sẵn sàng

            # Kiểm tra xem đã có thumbnails chưa (để skip nếu đã chạy rồi)
            thumb_dir = local_dir / "thumbnail"
            existing_thumbs = list(thumb_dir.glob("thumb_*.png")) if thumb_dir.exists() else []
            if len(existing_thumbs) >= 6:
                log(f"  [v] Thumbnails already exist ({len(existing_thumbs)} files), skip")
            else:
                log(f"  [THUMB] Generating 6 thumbnails (3 landscape + 3 portrait)...")
                engine_thumb = SmartEngine(
                    worker_id=1,
                    total_workers=2,
                    chrome_portable=chrome2_path
                )
                thumb_ok = engine_thumb.generate_thumbnails(excel_path, local_dir)
                if thumb_ok:
                    log(f"  [v] Thumbnails generated successfully!")
                else:
                    log(f"  [WARN] Thumbnail generation failed or incomplete", "WARN")

    except Exception as e:
        log(f"  [ERROR] Thumbnail generation error: {e}", "ERROR")
        import traceback
        traceback.print_exc()

    # Step 3.6: Đợi Chrome 1 bắt đầu tạo SCENE images
    # v1.0.86: Đợi không giới hạn - VMManager sẽ restart workers khi project xong
    img_dir = local_dir / "img"
    log(f"  [WAIT] Waiting for Chrome 1 to START creating scene images...")

    wait_interval = 10
    waited = 0

    while True:
        if img_dir.exists():
            # Tìm ảnh scene (số hoặc scene_X)
            scene_files = []
            for f in img_dir.glob("*.png"):
                name = f.stem
                if name.isdigit() or name.startswith("scene_"):
                    scene_files.append(f)
            for f in img_dir.glob("*.jpg"):
                name = f.stem
                if name.isdigit() or name.startswith("scene_"):
                    scene_files.append(f)

            if scene_files:
                log(f"  [v] Chrome 1 đã bắt đầu tạo scenes! Found {len(scene_files)} scene images")
                log(f"     → Chrome 2 bắt đầu tạo scenes lẻ...")
                time.sleep(3)
                break

        # Log mỗi 30 giây
        if waited % 30 == 0:
            log(f"  [WAIT] Waiting... ({waited}s)")

        time.sleep(wait_interval)
        waited += wait_interval

    # Step 4: Create images using SmartEngine
    try:
        from modules.smart_engine import SmartEngine

        # Chrome 2: worker_id=1, total_workers=2, dùng chrome_portable_2
        engine = SmartEngine(
            worker_id=1,
            total_workers=2,
            chrome_portable=chrome2_path
        )

        # v1.0.390: Đọc video_mode từ settings.yaml
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
        log(f"  Mode: CHROME 2 (scenes lẻ: 1,3,5,...) {'+ VIDEO' if not _skip_video else ''}")

        result = engine.run(
            str(excel_path),
            callback=callback,
            skip_compose=True,
            skip_video=_skip_video,
            skip_references=True
        )

        if result.get('error'):
            log(f"  Error: {result.get('error')}", "ERROR")
            return False

    except Exception as e:
        log(f"  Exception: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        return False

    # Step 5: Check completion
    if is_local_pic_complete(local_dir, code):
        log(f"  ALL images complete!")
    elif is_chrome2_odd_scenes_complete(local_dir, code):
        log(f"  Chrome2 ODD scenes + videos complete!")
    else:
        log(f"  Chrome2 has pending odd scenes", "WARN")
        return False

    return True


def get_chrome1_current_project() -> Optional[str]:
    """Đọc project mà Chrome 1 đang làm từ Agent Status.

    Chrome 2 PHẢI làm cùng project với Chrome 1, không được tự chọn.
    """
    if not AgentManager:
        return None

    try:
        manager = AgentManager()
        status = manager.get_worker_status("chrome_1")
        if status and status.current_project:
            return status.current_project
    except Exception as e:
        print(f"  [DEBUG] Cannot get Chrome 1 status: {e}")

    return None


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

        # Đã copy xong → xóa local để làm mã khác
        if (item / "_COPIED_TO_VISUAL").exists():
            log(f"    [{code}] _COPIED_TO_VISUAL → xóa local để làm mã khác")
            try:
                shutil.rmtree(item, ignore_errors=True)
            except:
                pass
            continue

        if is_project_complete_on_master(code):
            log(f"    [{code}] Đã có ở VISUAL → xóa local để làm mã khác")
            try:
                shutil.rmtree(item, ignore_errors=True)
            except:
                pass
            continue

        if is_local_pic_complete(item, code):
            continue

        # v1.0.293: Safety net - nếu có marker _IMAGES_DONE thì skip
        # (Excel có thể bị lock tạm thời khiến is_local_pic_complete trả về False)
        if (item / "_IMAGES_DONE").exists():
            continue

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
                my_claims = tq.get_my_claims()
                if my_claims:
                    print(f"  [QUEUE] Đang claim: {my_claims}")
                    return my_claims
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
                log(f"    [{code}] Đã có ở VISUAL → xóa local để làm mã khác")
                local_del = LOCAL_PROJECTS / code
                if local_del.exists():
                    try:
                        shutil.rmtree(local_del, ignore_errors=True)
                    except:
                        pass
                continue

            local_dir = LOCAL_PROJECTS / code
            if local_dir.exists() and is_local_pic_complete(local_dir, code):
                continue

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

            # v1.0.349: Chỉ lấy 1 mã
            if pending:
                break

        except (OSError, PermissionError) as e:
            print(f"  [WARN] Network error scanning: {e}")
            break

    return sorted(pending)


def run_scan_loop():
    """Run continuous scan loop for IMAGE generation - CHROME 2."""
    chrome2_path = get_chrome2_path()

    print(f"\n{'='*60}")
    print(f"  VE3 TOOL - WORKER PIC BASIC - CHROME 2")
    print(f"{'='*60}")
    print(f"  Worker folder:   {TOOL_DIR.parent.name}")
    print(f"  Channel filter:  {WORKER_CHANNEL or 'ALL'}")
    print(f"  Chrome2:         {chrome2_path or 'NOT FOUND'}")
    print(f"  Mode:            Scenes lẻ (1,3,5,...)")
    print(f"{'='*60}")

    if not chrome2_path:
        print("ERROR: chrome_portable_2 not configured!")
        return

    # Đợi Chrome 1 khởi động trước
    print("\n[Chrome2] Waiting 10s for Chrome 1 to start...")
    time.sleep(10)

    cycle = 0

    while True:
        cycle += 1
        print(f"\n[Chrome2 CYCLE {cycle}] Checking Chrome 1 status...")

        # ===== QUAN TRỌNG: Chrome 2 LUÔN follow Chrome 1 =====
        chrome1_project = get_chrome1_current_project()

        if chrome1_project:
            # Chrome 1 đang làm project → Chrome 2 ưu tiên làm cùng project đó
            target = chrome1_project
            print(f"  [Chrome2] Following Chrome 1: {target}")
        else:
            # Chrome 1 chưa làm gì → Chrome 2 đợi
            print(f"  [Chrome2] Chrome 1 is idle, waiting...")
            print(f"\n  Waiting {SCAN_INTERVAL}s... (Ctrl+C to stop)")
            try:
                time.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                print("\n\nStopped by user.")
                break
            continue  # Skip processing, wait for Chrome 1

        # Kiểm tra project có Excel với prompts không
        local_dir = LOCAL_PROJECTS / target

        # v1.0.98: Nếu local folder KHÔNG tồn tại → project đã completed & deleted
        # → Đợi Chrome 1 chuyển sang project mới
        if not local_dir.exists():
            print(f"  [Chrome2] {target} folder not found (project completed?)")
            print(f"  [Chrome2] Waiting for Chrome 1 to start new project...")
            print(f"\n  Waiting {SCAN_INTERVAL}s... (Ctrl+C to stop)")
            try:
                time.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                print("\n\nStopped by user.")
                break
            continue

        if not has_excel_with_prompts(local_dir, target):
            print(f"  [Chrome2] {target} not ready (no Excel/prompts), waiting...")
            print(f"\n  Waiting {SCAN_INTERVAL}s... (Ctrl+C to stop)")
            try:
                time.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                print("\n\nStopped by user.")
                break
            continue

        # Process project (following Chrome 1)
        try:
            success = process_project_pic_basic_chrome2(target)
            if not success:
                print(f"  [Chrome2] Project {target} incomplete, will retry...")
            else:
                print(f"  [Chrome2] Project {target} completed!")
        except KeyboardInterrupt:
            print("\n\nStopped by user.")
            return
        except Exception as e:
            print(f"  [Chrome2] Error processing {target}: {e}")

        print(f"\n  Waiting {SCAN_INTERVAL}s... (Ctrl+C to stop)")
        try:
            time.sleep(SCAN_INTERVAL)
        except KeyboardInterrupt:
            print("\n\nStopped by user.")
            break


def validate_references_if_needed(code: str, callback=None) -> bool:
    """
    VALIDATOR MODE - Chrome 2 validate references trước khi làm scenes.

    Kiểm tra xem project có references chưa validate không.
    Nếu có → Validate & fix bằng DeepSeek
    Nếu không → Return False (sẽ chạy normal mode)

    Returns:
        True nếu đã validate xong (hoặc không cần)
        False nếu có lỗi
    """
    def log(msg, level="INFO"):
        if callback:
            callback(msg, level)
        else:
            print(f"[Validator] {msg}")

    try:
        from modules.reference_validator import ReferenceValidator
        from modules.excel_manager import PromptWorkbook
        from modules.drission_flow_api import DrissionFlowAPI
        import yaml

        local_dir = LOCAL_PROJECTS / code
        excel_path = local_dir / f"{code}_prompts.xlsx"

        if not excel_path.exists():
            return True  # No Excel, skip validation

        # Load workbook
        workbook = PromptWorkbook(str(excel_path))

        # Get all references
        all_chars = workbook.get_characters()
        refs_to_validate = []

        for char in all_chars:
            # CHỈ VALIDATE NHI VẬT (nv) - BỎ QUA LOCATIONS (loc)
            if not char.id.lower().startswith('nv'):
                continue

            # Check if has media_id and not verified yet
            if char.media_id and char.status not in ['verified', 'verified_fixed']:
                refs_to_validate.append(char.id)

        if not refs_to_validate:
            log(f"No references need validation, skip validator mode")
            return True

        log(f"\n{'='*60}")
        log(f"VALIDATOR MODE - {len(refs_to_validate)} references to validate")
        log(f"{'='*60}")
        log(f"References: {refs_to_validate}")

        # Setup DrissionFlowAPI
        chrome2_path = get_chrome2_path()
        if not chrome2_path:
            log("ERROR: Chrome 2 path not found!", "ERROR")
            return False

        # Load config
        config_path = TOOL_DIR / "config" / "settings.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        api = DrissionFlowAPI(
            chrome_portable=chrome2_path,
            worker_id=1,
            total_workers=2,
            headless=False,
            webshare_enabled=False
        )

        # Setup Chrome
        log("Starting Chrome for validation...")
        if not api.setup(project_url="https://labs.google/fx/vi/tools/flow"):
            log("ERROR: Failed to start Chrome!", "ERROR")
            return False

        log("Chrome started, creating validator...")

        # Create validator
        validator = ReferenceValidator(
            drission_api=api,
            workbook=workbook,
            config=config
        )

        # Run validation
        stats = validator.validate_all_references(refs_to_validate)

        # Cleanup
        api.close()

        log(f"\n{'='*60}")
        log(f"VALIDATION COMPLETED")
        log(f"{'='*60}")
        log(f"Tested: {stats['tested']}")
        log(f"Verified: {stats['verified']}")
        log(f"Violated: {stats['violated']}")
        log(f"Fixed: {stats['fixed']}")
        log(f"Failed: {stats['failed']}")

        return True

    except Exception as e:
        log(f"ERROR in validator mode: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description='VE3 Worker PIC BASIC - Chrome 2')
    parser.add_argument('project', nargs='?', default=None, help='Project code')
    parser.add_argument('--validate-only', action='store_true', help='Only run validator mode')
    args = parser.parse_args()

    # Khởi tạo Agent Protocol
    init_agent()

    try:
        if args.project:
            # Single project mode

            # VALIDATOR MODE: Validate references trước
            if not args.validate_only:
                print(f"\n[Chrome2] Checking if validation needed for {args.project}...")
                validate_references_if_needed(args.project)
                print(f"[Chrome2] Validation check completed, proceeding to normal mode...")

            # Normal mode hoặc validate-only
            if args.validate_only:
                success = validate_references_if_needed(args.project)
            else:
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
        success = process_project_pic_basic_chrome2(code, callback=progress_callback)
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


def _do_pre_login_if_needed():
    """
    v1.0.123: Chrome 2 KHÔNG login.

    Chrome 1 đã login CẢ 2 Chrome tuần tự rồi.
    Chrome 2 chỉ cần đợi và bắt đầu tạo ảnh.
    """
    print("\n[PRE-LOGIN Chrome2] Chrome 1 handles login for both - skipping")
    print("[PRE-LOGIN Chrome2] Done!\n")


def run_scan_loop_with_agent():
    """Run scan loop với Agent Protocol."""
    global _agent

    print(f"\n{'='*60}")
    print(f"  CHROME WORKER 2 - PIC BASIC MODE")
    print(f"  Agent: {'Enabled' if _agent else 'Disabled'}")
    print(f"{'='*60}\n")

    # v1.0.121: PRE-LOGIN trước khi bắt đầu scan loop
    _do_pre_login_if_needed()

    cycle = 0

    try:
        while True:
            cycle += 1
            print(f"\n[CYCLE {cycle}] Checking Chrome 1 status...")

            # ===== QUAN TRỌNG: Chrome 2 LUÔN follow Chrome 1 =====
            chrome1_project = get_chrome1_current_project()

            if chrome1_project:
                target = chrome1_project
                print(f"  [Chrome2] Following Chrome 1: {target}")

                # Kiểm tra project có Excel với prompts không
                local_dir = LOCAL_PROJECTS / target
                if not has_excel_with_prompts(local_dir, target):
                    print(f"  [Chrome2] {target} not ready (no Excel/prompts), waiting...")
                    print(f"\nWaiting {SCAN_INTERVAL}s... (Ctrl+C to stop)")
                    time.sleep(SCAN_INTERVAL)
                    continue

                try:
                    success = process_project_with_agent(target)
                    if success:
                        print(f"  [Chrome2] Project {target} completed!")
                    else:
                        print(f"  [Chrome2] Project {target} incomplete, will retry...")
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    agent_log(f"Error processing {target}: {e}", "ERROR")
            else:
                print(f"  [Chrome2] Chrome 1 is idle, waiting...")

            print(f"\nWaiting {SCAN_INTERVAL}s... (Ctrl+C to stop)")
            time.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        print("\n\nStopped by user.")


if __name__ == "__main__":
    main()
