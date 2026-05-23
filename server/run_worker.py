#!/usr/bin/env python3
"""
VE3 Tool - Worker Mode (Image/Video Generation)
Chạy trên máy ảo, copy dữ liệu từ máy chủ qua RDP/VMware.

Workflow:
    1. Copy project từ MASTER\AUTO\ve3-tool-simple\PROJECTS\{code}
    2. Tạo ảnh (characters + scenes)
    3. Tạo video từ ảnh (VEO3)
    4. KHÔNG edit ra MP4 (máy chủ sẽ làm)
    5. Copy kết quả về MASTER\AUTO\VISUAL\{code}

Usage:
    python run_worker.py                     (quét và xử lý tự động)
    python run_worker.py AR47-0028           (chạy 1 project cụ thể)
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

# Add current directory to path
TOOL_DIR = Path(__file__).parent
sys.path.insert(0, str(TOOL_DIR))

# === AUTO-DETECT NETWORK PATH ===
# Các đường dẫn có thể có đến \AUTO (tùy máy chủ)
# Ưu tiên SMB share (Z:) trước vì ổn định hơn tsclient khi copy file lớn
POSSIBLE_AUTO_PATHS = [
    r"Z:\AUTO",                                    # SMB Share (ưu tiên - ổn định nhất)
    r"Y:\AUTO",                                    # Mapped drive
    r"\\tsclient\D\AUTO",                          # RDP từ Windows
    r"\\tsclient\C\AUTO",                          # RDP từ Windows (ổ C)
    r"\\vmware-host\Shared Folders\D\AUTO",        # VMware Workstation
    r"\\vmware-host\Shared Folders\AUTO",          # VMware Workstation (direct)
    r"\\VBOXSVR\AUTO",                             # VirtualBox
    r"D:\AUTO",                                    # Direct access (same machine)
]

def detect_auto_path() -> Path:
    """
    Auto-detect đường dẫn đến thư mục \\AUTO trên máy chủ.
    v1.0.324: Dùng robust_copy.get_working_auto_path() với đầy đủ fallback paths.
    """
    try:
        from modules.robust_copy import get_working_auto_path
        result = get_working_auto_path(log=lambda msg, lvl="INFO": print(f"  {msg}"))
        if result:
            print(f"  [v] Found AUTO at: {result}")
            return Path(result)
        return None
    except ImportError:
        pass
    # Fallback nếu module chưa có
    for path_str in POSSIBLE_AUTO_PATHS:
        try:
            path = Path(path_str)
            if path.exists():
                print(f"  [v] Found AUTO at: {path}")
                return path
        except Exception:
            continue
    return None

# Detect AUTO path lần đầu
AUTO_PATH = detect_auto_path()

if AUTO_PATH:
    MASTER_PROJECTS = AUTO_PATH / "ve3-tool-simple" / "PROJECTS"
    # v1.0.69: Fix path - thống nhất dùng "visual" (chữ thường) với vm_manager.py
    MASTER_VISUAL = AUTO_PATH / "visual"
else:
    # Fallback to default (will fail if not accessible)
    MASTER_PROJECTS = Path(r"\\tsclient\D\AUTO\ve3-tool-simple\PROJECTS")
    # v1.0.69: Fix path - thống nhất dùng "visual" (chữ thường)
    MASTER_VISUAL = Path(r"\\tsclient\D\AUTO\visual")

# Local PROJECTS folder (worker)
LOCAL_PROJECTS = TOOL_DIR / "PROJECTS"

# Scan interval (seconds)
SCAN_INTERVAL = 30


def get_channel_from_folder() -> str:
    """
    Auto-detect channel from parent folder name.
    Example: AR35-T1 -> AR35
             AR47-T2 -> AR47
    Returns None if cannot detect.
    """
    parent_name = TOOL_DIR.parent.name  # e.g., "AR35-T1"

    # Try to extract channel prefix (part before -T)
    if "-T" in parent_name:
        channel = parent_name.split("-T")[0]  # "AR35-T1" -> "AR35"
        return channel

    # Fallback: try splitting by last dash
    if "-" in parent_name:
        parts = parent_name.rsplit("-", 1)
        if parts[0]:
            return parts[0]

    return None


# Auto-detect channel for this worker
WORKER_CHANNEL = get_channel_from_folder()

# VM ID = full folder name (e.g., "AR8-T1")
VM_ID = TOOL_DIR.parent.name


def matches_channel(code: str, channel_filter: str = None) -> bool:
    """Check if project code matches this worker's channel.

    v1.0.359: Distributed mode → bỏ channel filter (Chrome xử lý mã từ bất kỳ channel nào).
    """
    # v1.0.359: Distributed mode → accept all channels
    if _is_distributed_mode():
        return True

    channel = channel_filter or WORKER_CHANNEL
    if channel is None:
        return True  # No filter if channel not detected

    # Project code format: AR35-0001
    # Check if starts with channel prefix
    return code.startswith(f"{channel}-")


def is_project_complete_on_master(code: str) -> bool:
    """Check if project already exists in VISUAL folder on master AND is FULLY complete.

    FIX v1.0.45: Kiểm tra expected vs actual images thay vì chỉ check ANY image.
    Bug cũ: Project có 1 ảnh trên master cũng bị skip, dù còn thiếu nhiều ảnh.
    """
    # SAFETY: Kiểm tra master có thực sự accessible không
    # Nếu không accessible, return False để không xóa local project
    try:
        if not MASTER_VISUAL.exists():
            return False
        # Thử list thư mục để verify thực sự accessible (không phải cache)
        _ = list(MASTER_VISUAL.iterdir())
    except (OSError, PermissionError):
        # Master không accessible - return False để giữ local an toàn
        return False

    visual_dir = MASTER_VISUAL / code
    if not visual_dir.exists():
        return False

    # Check expected vs actual images (giống is_local_pic_complete)
    img_dir = visual_dir / "img"
    if not img_dir.exists():
        return False

    try:
        img_files = list(img_dir.glob("*.png")) + list(img_dir.glob("*.jpg"))
        if len(img_files) == 0:
            return False

        # Đọc Excel để lấy expected count
        excel_path = visual_dir / f"{code}_prompts.xlsx"
        if excel_path.exists():
            try:
                from modules.excel_manager import PromptWorkbook
                wb = PromptWorkbook(str(excel_path))
                wb.load_or_create()
                scenes = wb.get_scenes()
                expected = len([s for s in scenes if s.img_prompt])

                # Nếu expected = 0, Excel invalid → không complete
                if expected == 0:
                    return False

                # Chỉ complete nếu actual >= expected
                is_complete = len(img_files) >= expected
                if not is_complete:
                    print(f"    [{code}] Master VISUAL: {len(img_files)}/{expected} images - NOT complete")
                return is_complete
            except Exception as e:
                # Lỗi đọc Excel → không complete
                return False
        else:
            # Không có Excel trên master → không verify được → không complete
            # Trừ khi có RẤT NHIỀU ảnh (>100) → assume complete
            if len(img_files) >= 100:
                return True
            return False
    except (OSError, PermissionError):
        return False


def is_excel_step7_completed(project_dir: Path, name: str) -> bool:
    """
    Check if Excel has Step 7 (scene_prompts) COMPLETED.
    Chrome Worker should only process projects with Step 7 done.
    """
    excel_path = project_dir / f"{name}_prompts.xlsx"
    if not excel_path.exists():
        return False

    try:
        from modules.excel_manager import PromptWorkbook
        wb = PromptWorkbook(str(excel_path))
        wb.load_or_create()

        # Check step 7 status
        step7_status = wb.get_step_status("step_7")
        if step7_status and step7_status.get("status") == "COMPLETED":
            return True

        # Fallback: check if scene_prompts step is completed
        all_steps = wb.get_all_step_status()
        for step in all_steps:
            if "prompt" in step.get("step_name", "").lower():
                if step.get("status") == "COMPLETED":
                    return True

        return False
    except Exception as e:
        # Fallback: if can't read status, check if has scenes with prompts
        return False


def has_excel_with_prompts(project_dir: Path, name: str) -> bool:
    """Check if project has Excel with prompts (ready for worker).

    IMPORTANT: Also checks that Step 7 is COMPLETED to avoid conflict
    with Excel Worker still writing to the file.
    """
    # Check flat structure
    excel_path = project_dir / f"{name}_prompts.xlsx"
    if not excel_path.exists():
        return False

    try:
        from modules.excel_manager import PromptWorkbook
        wb = PromptWorkbook(str(excel_path))
        wb.load_or_create()

        # Check Step 7 COMPLETED first
        step7_status = wb.get_step_status("step_7")
        if step7_status:
            # step_7 có trong Excel → check status
            status_val = step7_status.get("status")
            if status_val != "COMPLETED":
                # Excel Worker chưa hoàn thành - KHÔNG xử lý
                print(f"    [{name}] step_7 status={status_val}, waiting for Excel Worker")
                return False
        # step_7 không có (Excel cũ hoặc không có tracking) → fallback kiểm tra scenes

        stats = wb.get_stats()
        total_scenes = stats.get('total_scenes', 0)
        scenes_with_prompts = stats.get('scenes_with_prompts', 0)
        if total_scenes == 0 or scenes_with_prompts == 0:
            print(f"    [{name}] scenes={total_scenes} prompts={scenes_with_prompts} → no prompts")
            return False
        return True
    except Exception as e:
        print(f"    [{name}] has_excel_with_prompts error: {e}")
        return False


def needs_api_completion(project_dir: Path, name: str) -> bool:
    """
    Check if Excel has [FALLBACK] prompts that need API completion.
    Returns True if any prompt starts with [FALLBACK].
    """
    excel_path = project_dir / f"{name}_prompts.xlsx"
    if not excel_path.exists():
        return False

    try:
        from modules.excel_manager import PromptWorkbook
        wb = PromptWorkbook(str(excel_path))
        scenes = wb.get_scenes()

        for scene in scenes:
            img_prompt = scene.img_prompt or ""
            if img_prompt.startswith("[FALLBACK]"):
                return True
        return False
    except:
        return False


def create_excel_with_api(project_dir: Path, name: str) -> bool:
    """
    Tạo Excel từ SRT bằng Progressive API (từng step, lưu ngay).

    Flow mới (Progressive - mỗi step lưu vào Excel):
    1. Step 1: Phân tích story → Lưu Excel
    2. Step 2: Tạo characters → Lưu Excel
    3. Step 3: Tạo locations → Lưu Excel
    4. Step 4: Tạo director_plan → Lưu Excel
    5. Step 5: Tạo scene prompts → Lưu Excel

    Lợi ích:
    - Nếu fail giữa chừng: Không mất progress
    - Có thể resume từ step bị fail
    - API đọc context từ Excel → chất lượng tốt hơn

    Fallback: Nếu không có API keys → tạo fallback Excel

    Returns True nếu có Excel (API hoặc fallback).
    """
    import yaml

    excel_path = project_dir / f"{name}_prompts.xlsx"
    srt_path = project_dir / f"{name}.srt"

    # Check SRT exists
    if not srt_path.exists():
        print(f"  [FAIL] No SRT file found!")
        return False

    print(f"  [API] Creating Excel from SRT (Progressive API)...")

    # Load config
    cfg = {}
    cfg_file = TOOL_DIR / "config" / "settings.yaml"
    if cfg_file.exists():
        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    # Collect API keys
    deepseek_key = cfg.get('deepseek_api_key', '')
    groq_keys = cfg.get('groq_api_keys', [])
    gemini_keys = cfg.get('gemini_api_keys', [])

    if deepseek_key:
        cfg['deepseek_api_keys'] = [deepseek_key]

    has_api_keys = bool(groq_keys or gemini_keys or deepseek_key)

    # === PHƯƠNG ÁN 1: Progressive API (từng step, lưu ngay) ===
    if has_api_keys:
        print(f"  [NET] Using Progressive API (step-by-step, save immediately)...")

        try:
            from modules.progressive_prompts import ProgressivePromptsGenerator

            gen = ProgressivePromptsGenerator(cfg)

            # Chạy tất cả steps (mỗi step tự lưu vào Excel)
            api_success = gen.run_all_steps(project_dir, name, log_callback=print)

            if api_success and excel_path.exists():
                print(f"  [OK] Excel created with Progressive API")
                return True
            else:
                print(f"  [WARN] Progressive API incomplete, trying fallback...")

        except Exception as api_err:
            print(f"  [WARN] Progressive API error: {api_err}")
            import traceback
            traceback.print_exc()
    else:
        print(f"  [WARN] No API keys configured")

    # === PHƯƠNG ÁN 2: Fallback (không cần API) ===
    print(f"  [EXCEL] Creating fallback Excel from SRT...")

    try:
        cfg['fallback_only'] = True

        from modules.prompts_generator import PromptGenerator
        gen = PromptGenerator(cfg)

        if gen.generate_for_project(project_dir, name, fallback_only=True):
            print(f"  [OK] Fallback Excel created")
            return True
        else:
            print(f"  [FAIL] Failed to create fallback Excel")
            return False

    except Exception as e:
        print(f"  [FAIL] Fallback error: {e}")
        import traceback
        traceback.print_exc()
        return False


def complete_excel_with_api(project_dir: Path, name: str) -> bool:
    """
    Hoàn thiện Excel có sẵn bằng API (nếu có [FALLBACK] prompts).

    Flow:
    1. Backup Excel hiện tại
    2. Thử hoàn thiện bằng API
    3. Nếu API fail → khôi phục từ backup (giữ nguyên fallback prompts)

    Returns True nếu có Excel (API hoặc giữ nguyên fallback).
    """
    import yaml

    print(f"  [API] Completing Excel with API...")

    excel_path = project_dir / f"{name}_prompts.xlsx"
    original_excel_backup = None

    try:
        # === BƯỚC 1: Backup Excel trước khi làm gì ===
        if excel_path.exists():
            import shutil
            backup_path = excel_path.with_suffix('.xlsx.backup')
            shutil.copy2(excel_path, backup_path)
            original_excel_backup = backup_path
            print(f"  [EXCEL] Backed up Excel to {backup_path.name}")
        else:
            # Không có Excel → dùng create_excel_with_api
            return create_excel_with_api(project_dir, name)

        # Load config
        cfg = {}
        cfg_file = TOOL_DIR / "config" / "settings.yaml"
        if cfg_file.exists():
            with open(cfg_file, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

        # Collect API keys
        deepseek_key = cfg.get('deepseek_api_key', '')
        groq_keys = cfg.get('groq_api_keys', [])
        gemini_keys = cfg.get('gemini_api_keys', [])

        if deepseek_key:
            cfg['deepseek_api_keys'] = [deepseek_key]

        # === KIỂM TRA: Nếu không có API keys → giữ nguyên fallback ===
        if not groq_keys and not gemini_keys and not deepseek_key:
            print(f"  [WARN] No API keys, keeping existing fallback prompts")
            if original_excel_backup and original_excel_backup.exists():
                original_excel_backup.unlink()
            return True

        # Prefer DeepSeek for prompts
        cfg['preferred_provider'] = 'deepseek' if deepseek_key else ('groq' if groq_keys else 'gemini')
        cfg['use_v2_flow'] = True

        # v1.0.318: KHÔNG XÓA Excel nếu đã có ảnh hoặc step_7 COMPLETED
        _img_dir = project_dir / "img"
        if _img_dir.exists():
            _img_count = len(list(_img_dir.glob("*.png"))) + len(list(_img_dir.glob("*.jpg")))
            if _img_count > 0:
                print(f"  [PROTECT] Đã có {_img_count} ảnh - KHÔNG xóa Excel")
                if original_excel_backup and original_excel_backup.exists():
                    original_excel_backup.unlink()
                return True

        try:
            from modules.excel_manager import PromptWorkbook
            wb_chk = PromptWorkbook(str(excel_path))
            s7_chk = wb_chk.get_step_status("step_7")
            if s7_chk.get("status") == "COMPLETED":
                print(f"  [PROTECT] step_7=COMPLETED - KHÔNG xóa Excel")
                if original_excel_backup and original_excel_backup.exists():
                    original_excel_backup.unlink()
                return True
        except Exception:
            # Không đọc được → coi như protected
            print(f"  [PROTECT] Không đọc được step_7 - KHÔNG xóa Excel")
            if original_excel_backup and original_excel_backup.exists():
                original_excel_backup.unlink()
            return True

        # Xóa Excel để regenerate (chỉ khi step_7 chưa COMPLETED)
        if excel_path.exists():
            excel_path.unlink()
            print(f"  [SYNC] Regenerating with API...")

        # Generate prompts with API
        from modules.prompts_generator import PromptGenerator
        gen = PromptGenerator(cfg)

        api_success = False
        try:
            api_success = gen.generate_for_project(project_dir, name, overwrite=True)
        except Exception as api_err:
            print(f"  [FAIL] API error: {api_err}")
            api_success = False

        if api_success:
            print(f"  [OK] Excel completed with API prompts")
            if original_excel_backup and original_excel_backup.exists():
                original_excel_backup.unlink()
            return True
        else:
            print(f"  [WARN] API failed, restoring backup...")
            # Khôi phục từ backup
            if original_excel_backup and original_excel_backup.exists():
                import shutil
                shutil.copy2(original_excel_backup, excel_path)
                original_excel_backup.unlink()
                print(f"  [OK] Restored fallback Excel")
            return True  # Tiếp tục với fallback prompts

    except Exception as e:
        print(f"  [FAIL] Error: {e}")
        import traceback
        traceback.print_exc()
        # Khôi phục từ backup
        if original_excel_backup and original_excel_backup.exists():
            import shutil
            shutil.copy2(original_excel_backup, excel_path)
            original_excel_backup.unlink()
        return True  # Tiếp tục với fallback prompts


def delete_master_source(code: str):
    """Delete project from master PROJECTS after copying to local."""
    try:
        src = MASTER_PROJECTS / code
        if src.exists():
            shutil.rmtree(src)
            print(f"  [DEL] Deleted from master PROJECTS: {code}")
    except Exception as e:
        print(f"  [WARN] Cleanup master warning: {e}")


def delete_local_project(code: str):
    """
    Delete local project after copying to VISUAL.
    v1.0.276: Retry 3 lan + force-delete readonly files tren Windows.
    Neu van khong xoa duoc → tao marker _COPIED_TO_VISUAL de scan skip.
    """
    import time
    local_dir = LOCAL_PROJECTS / code
    if not local_dir.exists():
        return

    def _force_rmtree(path):
        """shutil.rmtree voi onerror xu ly readonly files tren Windows."""
        import os, stat
        def _onerror(func, fpath, exc_info):
            try:
                os.chmod(fpath, stat.S_IWRITE)
                func(fpath)
            except Exception:
                pass
        shutil.rmtree(path, onerror=_onerror)

    for attempt in range(3):
        try:
            _force_rmtree(local_dir)
            if not local_dir.exists():
                print(f"  [DEL] Deleted local project: {code}")
                return
        except Exception as e:
            if attempt < 2:
                print(f"  [WARN] Delete attempt {attempt+1}/3 failed: {e}, retrying...")
                time.sleep(2)
            else:
                print(f"  [WARN] Could not delete {code} after 3 attempts: {e}")

    # Neu van con → tao marker de scan_incomplete_local_projects() skip
    if local_dir.exists():
        try:
            (local_dir / "_COPIED_TO_VISUAL").touch()
            print(f"  [WARN] Created _COPIED_TO_VISUAL marker for {code} (delete failed)")
        except Exception as e:
            print(f"  [WARN] Cleanup local warning: {e}")


def copy_from_master(code: str) -> Path:
    """Copy project from master to local (or return local if already exists).

    v1.0.67: KHÔNG XÓA master source sau khi copy.
    v1.0.324: Re-detect AUTO path nếu source không accessible.
    """
    global AUTO_PATH, MASTER_PROJECTS, MASTER_VISUAL

    src = MASTER_PROJECTS / code
    dst = LOCAL_PROJECTS / code

    # Create local PROJECTS dir
    LOCAL_PROJECTS.mkdir(parents=True, exist_ok=True)

    # If local already exists, use it (even if master was deleted)
    if dst.exists():
        print(f"  [LOCAL] Using existing local: {code}")
        # Try to update SRT from master if available
        if src.exists():
            # FIX v1.0.61: KHÔNG copy Excel từ master nếu local đã có
            # Vì Chrome có thể đang dùng Excel local → copy sẽ corrupt
            excel_dst = dst / f"{code}_prompts.xlsx"
            if not excel_dst.exists():
                excel_src = src / f"{code}_prompts.xlsx"
                if excel_src.exists():
                    shutil.copy2(excel_src, excel_dst)
                    print(f"  [COPY] Copied Excel from master (local was missing)")

            # Copy SRT if local doesn't have it
            srt_src = src / f"{code}.srt"
            srt_dst = dst / f"{code}.srt"
            if srt_src.exists() and not srt_dst.exists():
                shutil.copy2(srt_src, srt_dst)
                print(f"  [COPY] Copied SRT from master")

            # v1.0.67: KHÔNG XÓA master source - dựa vào VISUAL để check done
            # delete_master_source(code)  # REMOVED
        return dst

    # Local doesn't exist, need to copy from master
    if not src.exists():
        # v1.0.324: Thử re-detect AUTO path (có thể SMB mất, cần fallback)
        print(f"  [WARN] Source not found: {src}, trying re-detect AUTO path...")
        try:
            from modules.robust_copy import get_working_auto_path
            new_path = get_working_auto_path(
                current_path=str(AUTO_PATH) if AUTO_PATH else None,
                log=lambda msg, lvl="INFO": print(f"  {msg}"),
            )
            if new_path and (not AUTO_PATH or str(AUTO_PATH) != new_path):
                print(f"  [AUTO] Path switched: {AUTO_PATH} → {new_path}")
                AUTO_PATH = Path(new_path)
                MASTER_PROJECTS = AUTO_PATH / "ve3-tool-simple" / "PROJECTS"
                MASTER_VISUAL = AUTO_PATH / "visual"
                src = MASTER_PROJECTS / code
        except ImportError:
            pass

        if not src.exists():
            print(f"  [FAIL] Source not found: {src}")
            return None

    # v1.0.323: Robust copy từ master
    print(f"  [COPY] Copying from master: {code}")
    try:
        from modules.robust_copy import robust_copy_tree
        _log = lambda msg, lvl="INFO": print(f"  {msg}")
        ok = robust_copy_tree(str(src), str(dst), max_retries=3, retry_delay=5, verify=True, log=_log)
        if not ok:
            print(f"  [FAIL] Robust copy thất bại!")
            return None
    except ImportError:
        shutil.copytree(src, dst)
    print(f"  [OK] Copied to: {dst}")

    # v1.0.348: Tạo _CLAIMED trên master nếu chưa có
    master_claimed = src / "_CLAIMED"
    if not master_claimed.exists():
        try:
            from modules.robust_copy import TaskQueue
            tq = TaskQueue(
                master_projects=str(MASTER_PROJECTS),
                vm_id=VM_ID,
                visual_path=str(MASTER_VISUAL),
                tool_dir=str(TOOL_DIR),
                log=lambda msg, lvl="INFO": print(f"  {msg}"),
            )
            if tq.claim(code):
                print(f"  [CLAIM] Claimed on master: {code} → {VM_ID}")
            else:
                # Fallback: ghi _CLAIMED trực tiếp
                import socket
                claim_content = f"{VM_ID}\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{socket.gethostname()}\n"
                master_claimed.write_text(claim_content, encoding='utf-8')
                print(f"  [CLAIM] Claimed (fallback): {code} → {VM_ID}")
        except Exception as e:
            print(f"  [WARN] Cannot create _CLAIMED: {e}")

    # Copy _CLAIMED về local
    try:
        local_claimed = dst / "_CLAIMED"
        if master_claimed.exists() and not local_claimed.exists():
            shutil.copy2(str(master_claimed), str(local_claimed))
    except Exception:
        pass

    # v1.0.67: KHÔNG XÓA master source - dựa vào VISUAL để check done
    # delete_master_source(code)  # REMOVED

    return dst


def copy_to_visual(code: str, local_dir: Path) -> bool:
    """Copy completed project to VISUAL folder on master.
    v1.0.324: Dùng robust_copy_to_master với fallback AUTO paths.
    """
    global AUTO_PATH, MASTER_PROJECTS, MASTER_VISUAL

    print(f"  [OUT] Copying to VISUAL: {code}")

    try:
        from modules.robust_copy import robust_copy_to_master
        _log = lambda msg, lvl="INFO": print(f"  {msg}")
        ok, used_path = robust_copy_to_master(
            str(local_dir),
            f"visual/{code}",
            current_auto_path=str(AUTO_PATH) if AUTO_PATH else None,
            max_retries=3, retry_delay=5,
            log=_log,
        )
        if ok:
            # Cập nhật AUTO_PATH nếu switch sang path khác
            if used_path and (not AUTO_PATH or str(AUTO_PATH) != used_path):
                print(f"  [AUTO] Path switched: {AUTO_PATH} → {used_path}")
                AUTO_PATH = Path(used_path)
                MASTER_PROJECTS = AUTO_PATH / "ve3-tool-simple" / "PROJECTS"
                MASTER_VISUAL = AUTO_PATH / "visual"
            print(f"  [OK] Copied to: {used_path}/visual/{code}")
            # v1.0.326: Release claim nếu distributed mode
            _release_claim(code)
            delete_local_project(code)
            return True
        else:
            print(f"  [FAIL] Copy THẤT BẠI - all paths tried!")
            return False
    except ImportError:
        # Fallback
        dst = MASTER_VISUAL / code
        try:
            MASTER_VISUAL.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(local_dir, dst)
            print(f"  [OK] Copied to: {dst}")
            _release_claim(code)
            delete_local_project(code)
            return True
        except Exception as e:
            print(f"  [FAIL] Copy failed: {e}")
            return False


def _release_claim(code: str):
    """Release claim khi project xong (distributed mode)."""
    if not _is_distributed_mode():
        return
    try:
        tq = _get_task_queue()
        if tq:
            tq.release(code)
            print(f"  [QUEUE] Released claim: {code}")
    except Exception as e:
        print(f"  [QUEUE] Release error: {e}")


def is_local_complete(project_dir: Path, name: str) -> bool:
    """
    Check if local project has ALL images AND videos created (if video enabled).

    Logic:
    1. Đọc Excel để biết tổng số scenes cần tạo
    2. Phải có ĐỦ ảnh cho tất cả scenes (không chỉ "có ảnh")
    3. Nếu video_count > 0: phải có đủ video tương ứng
    """
    img_dir = project_dir / "img"
    if not img_dir.exists():
        return False

    # Count images (png, jpg) - exclude videos
    img_files = list(img_dir.glob("*.png")) + list(img_dir.glob("*.jpg"))

    # Count videos
    video_files = list(img_dir.glob("*.mp4"))

    # Cần ít nhất 1 file ảnh
    if len(img_files) == 0:
        return False

    # ĐỌC EXCEL ĐỂ BIẾT TỔNG SỐ SCENES CẦN TẠO
    required_images = 0
    try:
        from modules.excel_manager import PromptWorkbook
        excel_path = project_dir / f"{name}_prompts.xlsx"
        if excel_path.exists():
            wb = PromptWorkbook(str(excel_path))
            scenes = wb.get_scenes()
            # Chỉ đếm scenes có img_prompt (cần tạo ảnh)
            required_images = sum(1 for s in scenes if s.img_prompt)
    except Exception as e:
        print(f"    [{name}] Warning reading Excel: {e}")

    # Nếu không đọc được Excel, dùng logic cũ (có ảnh là OK)
    if required_images == 0:
        required_images = len(img_files)  # Fallback

    # CHECK ĐỦ ẢNH CHƯA
    if len(img_files) < required_images:
        print(f"    [{name}] Images: {len(img_files)}/{required_images} - NOT complete")
        return False
    else:
        print(f"    [{name}] Images: {len(img_files)}/{required_images} - OK")

    # Check video_count from settings
    try:
        import yaml
        config_path = TOOL_DIR / "config" / "settings.yaml"
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

            video_count_setting = config.get('video_count', 0)

            # Parse video_count
            if video_count_setting == 'full' or video_count_setting == "full":
                required_videos = len(img_files)  # Tất cả ảnh cần có video
            elif video_count_setting and int(video_count_setting) > 0:
                required_videos = min(int(video_count_setting), len(img_files))
            else:
                required_videos = 0  # Video tắt

            # Nếu cần video, kiểm tra đủ số lượng
            if required_videos > 0:
                if len(video_files) < required_videos:
                    print(f"    [{name}] Videos: {len(video_files)}/{required_videos} - NOT complete")
                    return False
                else:
                    print(f"    [{name}] Videos: {len(video_files)}/{required_videos} - OK")
    except Exception as e:
        print(f"    [{name}] Warning checking video: {e}")

    return True


def process_project(code: str, callback=None) -> bool:
    """Process a single project (create images + video only, NO MP4 edit)."""

    def log(msg, level="INFO"):
        if callback:
            callback(msg, level)
        else:
            print(msg)

    log(f"\n{'='*60}")
    log(f"Processing: {code}")
    log(f"{'='*60}")

    # Step 1: Check if already done on master
    if is_project_complete_on_master(code):
        log(f"  [SKIP] Already in VISUAL folder, skip!")
        return True

    # Step 2: Copy from master
    local_dir = copy_from_master(code)
    if not local_dir:
        return False

    # Step 3: Check Excel - nếu không có thì tạo từ SRT (API trước, fallback sau)
    excel_path = local_dir / f"{code}_prompts.xlsx"
    srt_path = local_dir / f"{code}.srt"

    if not excel_path.exists():
        # Không có Excel - tạo mới từ SRT
        if srt_path.exists():
            log(f"  [EXCEL] No Excel found, creating from SRT (API first, fallback if fail)...")
            if not create_excel_with_api(local_dir, code):
                log(f"  [FAIL] Failed to create Excel, skip!")
                return False
        else:
            log(f"  [SKIP] No Excel and no SRT, skip!")
            return False
    elif not has_excel_with_prompts(local_dir, code):
        # v1.0.318: TUYỆT ĐỐI KHÔNG XÓA Excel nếu đã có ảnh hoặc step_7 COMPLETED
        _protected = False

        # CHECK 1: Đã có ảnh → KHÔNG BAO GIỜ xóa Excel
        _img_dir = local_dir / "img"
        if _img_dir.exists():
            _img_count = len(list(_img_dir.glob("*.png"))) + len(list(_img_dir.glob("*.jpg")))
            if _img_count > 0:
                _protected = True
                log(f"  [PROTECT] Đã có {_img_count} ảnh - KHÔNG xóa Excel (có thể bị lock tạm)")

        # CHECK 2: step_7 COMPLETED hoặc không đọc được (coi như protected)
        if not _protected:
            try:
                from modules.excel_manager import PromptWorkbook
                wb_check = PromptWorkbook(str(excel_path))
                s7 = wb_check.get_step_status("step_7")
                # s7 = {} khi step_7 row không tồn tại → có thể file bị lock
                # s7.get("status") == "COMPLETED" khi step_7 đã xong
                if s7.get("status") == "COMPLETED":
                    _protected = True
                    log(f"  [PROTECT] Excel step_7=COMPLETED - KHÔNG xóa")
            except Exception:
                # Không đọc được → coi như COMPLETED để bảo vệ
                _protected = True
                log(f"  [PROTECT] Không đọc được Excel - KHÔNG xóa (có thể bị lock)")

        if _protected:
            pass  # Không xóa, tiếp tục flow (sẽ retry cycle sau)
        else:
            # Excel exists but empty/corrupt AND không có ảnh AND step_7 chưa xong - recreate
            log(f"  [EXCEL] Excel empty/corrupt (step_7 chưa COMPLETED, 0 ảnh), recreating...")
            excel_path.unlink()
            if not create_excel_with_api(local_dir, code):
                log(f"  [FAIL] Failed to recreate Excel, skip!")
                return False
    elif needs_api_completion(local_dir, code):
        # Excel has [FALLBACK] prompts - try to complete with API
        log(f"  [EXCEL] Excel has [FALLBACK] prompts, trying API...")
        complete_excel_with_api(local_dir, code)
        # Continue even if API fails (fallback prompts will be used)

    # Step 4: Create images/videos
    try:
        from modules.smart_engine import SmartEngine

        # Pass worker settings for window layout
        engine = SmartEngine(worker_id=WORKER_ID, total_workers=TOTAL_WORKERS)

        # Find Excel path
        excel_path = local_dir / f"{code}_prompts.xlsx"

        log(f"  [EXCEL] Excel: {excel_path.name}")

        # Run engine - create images and videos only (no MP4 compose)
        result = engine.run(str(excel_path), callback=callback, skip_compose=True)

        if result.get('error'):
            log(f"  [FAIL] Error: {result.get('error')}", "ERROR")
            return False

    except Exception as e:
        log(f"  [FAIL] Exception: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        return False

    # Step 5: Copy to VISUAL on master
    if is_local_complete(local_dir, code):
        if copy_to_visual(code, local_dir):
            log(f"  [OK] Done! Project copied to VISUAL")
            return True
        else:
            log(f"  [WARN] Images created but copy failed", "WARN")
            return False
    else:
        log(f"  [WARN] No images created", "WARN")
        return False


def scan_incomplete_local_projects() -> list:
    """
    Scan local PROJECTS for projects that need processing.
    Bao gồm CẢ project chưa có ảnh VÀ project có ảnh nhưng chưa đủ.
    Engine sẽ chạy và hoàn thành project trước khi sync VISUAL.
    """
    need_processing = []

    if not LOCAL_PROJECTS.exists():
        return need_processing

    for item in LOCAL_PROJECTS.iterdir():
        if not item.is_dir():
            continue

        code = item.name

        # Skip if not matching channel
        if not matches_channel(code):
            continue

        # Skip if already in VISUAL (đã hoàn thành)
        if is_project_complete_on_master(code):
            continue

        # Check if has Excel with prompts OR has SRT (can create Excel)
        srt_path = item / f"{code}.srt"
        has_excel = has_excel_with_prompts(item, code)
        has_srt = srt_path.exists()

        if not has_excel and not has_srt:
            continue  # Không có gì để xử lý

        # Check trạng thái hiện tại
        if is_local_complete(item, code):
            # Đã có đủ ảnh/video - nhưng VẪN chạy engine để verify
            # Engine sẽ skip các ảnh đã tạo và chỉ tạo thiếu (nếu có)
            print(f"    - {code}: appears complete, will verify via engine")
            need_processing.append(code)
        elif has_excel:
            print(f"    - {code}: incomplete (has Excel) → will process")
            need_processing.append(code)
        elif has_srt:
            print(f"    - {code}: has SRT, no Excel → will create with API")
            need_processing.append(code)

    return sorted(need_processing)


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


def _is_distributed_mode() -> bool:
    """Check xem distributed mode có được bật không (settings.yaml)."""
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


def _get_task_queue():
    """Tạo TaskQueue instance cho distributed mode."""
    try:
        from modules.robust_copy import TaskQueue
        return TaskQueue(
            master_projects=str(MASTER_PROJECTS),
            vm_id=VM_ID,
            visual_path=str(MASTER_VISUAL),
            tool_dir=str(TOOL_DIR),
            log=lambda msg, lvl="INFO": print(f"  {msg}"),
        )
    except ImportError:
        return None


def scan_master_projects() -> list:
    """Scan master PROJECTS folder for pending projects.

    v1.0.326: Hỗ trợ distributed mode (claim-based).
    - distributed_mode=false (mặc định): Luồng cũ - filter theo channel
    - distributed_mode=true: Luồng mới - claim project, bỏ channel filter
    """
    pending = []

    # === DISTRIBUTED MODE: claim-based ===
    if _is_distributed_mode():
        tq = _get_task_queue()
        if tq:
            print(f"  [QUEUE] Distributed mode - VM: {VM_ID}")

            # Dọn claims quá hạn
            tq.cleanup_stale_claims()

            # Check claim hiện tại của mình
            my_claims = tq.get_my_claims()
            if my_claims:
                print(f"  [QUEUE] Đang claim: {my_claims}")
                return my_claims  # Tiếp tục làm mã đã claim

            # Claim mã mới (ưu tiên channel mình trước)
            code = tq.claim_next(preferred_channel=WORKER_CHANNEL)
            if code:
                return [code]

            return []

    # === LUỒNG CŨ: channel-based filter ===
    print(f"  [DEBUG] Checking: {MASTER_PROJECTS}")
    print(f"  [DEBUG] Worker channel: {WORKER_CHANNEL or 'ALL (no filter)'}")

    if not safe_path_exists(MASTER_PROJECTS):
        print(f"  [WARN] Master PROJECTS not accessible: {MASTER_PROJECTS}")
        return pending

    # List all folders - wrap in try-except for network safety
    try:
        all_folders = []
        for item in safe_iterdir(MASTER_PROJECTS):
            try:
                if item.is_dir():
                    all_folders.append(item)
            except (OSError, PermissionError):
                continue
        print(f"  [DEBUG] Found {len(all_folders)} folders in MASTER_PROJECTS")
    except (OSError, PermissionError) as e:
        print(f"  [WARN] Network error listing master: {e}")
        return pending

    for item in all_folders:
        try:
            code = item.name

            # Skip if not matching this worker's channel
            if not matches_channel(code):
                continue  # Silent skip - not our channel

            # Skip if already in VISUAL
            if is_project_complete_on_master(code):
                print(f"    - {code}: already in VISUAL [v]")
                continue

            # Check if has Excel or SRT
            excel_path = item / f"{code}_prompts.xlsx"
            srt_path = item / f"{code}.srt"

            # Wrap network path checks in try-except
            try:
                if has_excel_with_prompts(item, code):
                    print(f"    - {code}: ready (has prompts) [v]")
                    pending.append(code)
                elif srt_path.exists():
                    # Có SRT nhưng không có Excel - worker sẽ tự tạo
                    print(f"    - {code}: has SRT, no Excel → will create with API")
                    pending.append(code)
                elif excel_path.exists():
                    print(f"    - {code}: Excel exists but no prompts yet")
                else:
                    print(f"    - {code}: no Excel and no SRT")
            except (OSError, PermissionError) as e:
                print(f"  [WARN] Network error checking {code}: {e}")
                continue

        except (OSError, PermissionError) as e:
            # Network disconnected while iterating
            print(f"  [WARN] Network error scanning: {e}")
            break

    return sorted(pending)


def sync_local_to_visual() -> int:
    """
    Scan local PROJECTS và CLEANUP các project đã sync.
    KHÔNG copy sang VISUAL ở đây - để process_project() chạy engine trước rồi mới sync.

    Returns:
        Số lượng projects đã cleanup
    """
    print(f"[SYNC] Checking local projects to sync to VISUAL...")
    print(f"  [DEBUG] Checking local: {LOCAL_PROJECTS}")

    if not LOCAL_PROJECTS.exists():
        print(f"  [DEBUG] Local PROJECTS folder does not exist")
        return 0

    # SAFETY CHECK: Kiểm tra master VISUAL có thực sự accessible không
    # Nếu không accessible, KHÔNG xóa bất kỳ local project nào
    master_accessible = False
    try:
        if MASTER_VISUAL.exists():
            _ = list(MASTER_VISUAL.iterdir())  # Thử list để verify
            master_accessible = True
    except (OSError, PermissionError):
        pass

    if not master_accessible:
        print(f"  [WARN] Master VISUAL not accessible - skipping cleanup to protect local data")
        return 0

    # List all folders
    all_folders = [item for item in LOCAL_PROJECTS.iterdir() if item.is_dir()]
    print(f"  [DEBUG] Found {len(all_folders)} local project folders")

    cleaned = 0

    for item in all_folders:
        code = item.name

        # Skip if not matching this worker's channel
        if not matches_channel(code):
            continue  # Silent skip - not our channel

        # Nếu đã có trong VISUAL thì xóa local (cleanup)
        if is_project_complete_on_master(code):
            print(f"    - {code}: already in VISUAL, cleaning up local...")
            delete_local_project(code)
            cleaned += 1
            continue

        # KHÔNG copy sang VISUAL ở đây!
        # Để scan_incomplete_local_projects() và process_project() xử lý
        # Engine sẽ chạy và hoàn thành project trước khi sync
        if is_local_complete(item, code):
            print(f"    - {code}: has images, will process via engine")
        else:
            print(f"    - {code}: incomplete, will process via engine")

    if cleaned > 0:
        print(f"  Cleaned up {cleaned} projects already in VISUAL")
    else:
        print(f"  No local projects to sync")

    return cleaned


def run_scan_loop():
    """Run continuous scan loop."""
    global AUTO_PATH, MASTER_PROJECTS, MASTER_VISUAL

    print(f"\n{'='*60}")
    print(f"  VE3 TOOL - WORKER MODE (Image/Video)")
    print(f"{'='*60}")
    print(f"  Worker folder:   {TOOL_DIR.parent.name}")
    print(f"  Channel filter:  {WORKER_CHANNEL or 'ALL (no filter)'}")

    # Re-detect AUTO path nếu chưa có
    if not AUTO_PATH:
        print(f"\n  [SEARCH] Detecting network path to \\AUTO...")
        AUTO_PATH = detect_auto_path()
        if AUTO_PATH:
            MASTER_PROJECTS = AUTO_PATH / "ve3-tool-simple" / "PROJECTS"
            # v1.0.69: Fix path - thống nhất dùng "visual" (chữ thường)
            MASTER_VISUAL = AUTO_PATH / "visual"

    if AUTO_PATH:
        print(f"  [v] AUTO path:     {AUTO_PATH}")
    else:
        print(f"  [x] AUTO path:     NOT FOUND")

    print(f"  Master PROJECTS: {MASTER_PROJECTS}")
    print(f"  Master VISUAL:   {MASTER_VISUAL}")
    print(f"  Local PROJECTS:  {LOCAL_PROJECTS}")
    print(f"  Scan interval:   {SCAN_INTERVAL}s")
    print(f"{'='*60}")

    # Check network paths
    if not AUTO_PATH or not safe_path_exists(MASTER_PROJECTS):
        print(f"\n[FAIL] Cannot access master PROJECTS!")
        print(f"   Tried paths:")
        for p in POSSIBLE_AUTO_PATHS:
            print(f"     - {p}")
        print(f"\n   Make sure:")
        print(f"     - RDP/VMware is connected")
        print(f"     - Drive is shared (D: or Shared Folders)")
        print(f"     - \\AUTO folder exists")
        print(f"\n   Press Ctrl+C to exit and fix the connection.")
        print(f"   Will retry every {SCAN_INTERVAL}s...")

    # === SYNC: Copy local projects đã có ảnh sang VISUAL ===
    print(f"\n[SYNC] Checking local projects to sync to VISUAL...")
    synced = sync_local_to_visual()
    if synced > 0:
        print(f"  [OK] Synced {synced} projects to VISUAL")
    else:
        print(f"  No local projects to sync")

    cycle = 0

    while True:
        cycle += 1
        print(f"\n[CYCLE {cycle}] Scanning...")

        # === SYNC: Copy local projects đã có ảnh sang VISUAL ===
        synced = sync_local_to_visual()
        if synced > 0:
            print(f"  [OUT] Synced {synced} local projects to VISUAL")

        # Find incomplete local projects (đã copy về nhưng chưa xong)
        incomplete_local = scan_incomplete_local_projects()

        # Find pending projects from master
        pending_master = scan_master_projects()

        # Merge: incomplete local + pending master (loại bỏ duplicate)
        pending = list(dict.fromkeys(incomplete_local + pending_master))

        if not pending:
            print(f"  No pending projects")
            # Wait before next scan
            print(f"\n  Waiting {SCAN_INTERVAL}s... (Ctrl+C to stop)")
            try:
                time.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                print("\n\nStopped by user.")
                break
        else:
            print(f"  Found: {len(pending)} pending projects")
            for p in pending[:5]:
                print(f"    - {p}")
            if len(pending) > 5:
                print(f"    ... and {len(pending) - 5} more")

            # === XỬ LÝ TẤT CẢ PROJECTS LIÊN TỤC ===
            for code in pending:
                try:
                    success = process_project(code)

                    # Sync lại sau mỗi project
                    sync_local_to_visual()

                    if not success:
                        print(f"  [SKIP] Skipping {code}, moving to next...")
                        continue

                except KeyboardInterrupt:
                    print("\n\nStopped by user.")
                    return
                except Exception as e:
                    print(f"  [FAIL] Error processing {code}: {e}")
                    continue

            # Sau khi xử lý hết, đợi 1 chút rồi scan lại
            print(f"\n  [OK] Processed all pending projects!")
            print(f"  Waiting {SCAN_INTERVAL}s for new projects... (Ctrl+C to stop)")
            try:
                time.sleep(SCAN_INTERVAL)
            except KeyboardInterrupt:
                print("\n\nStopped by user.")
                break


def run_single_project(code: str):
    """Run a single project by name."""
    process_project(code)


def run_parallel_mode(project: str = None):
    """
    Run PIC and VIDEO workers in parallel.
    Main process finishes when PIC is done.
    VIDEO continues in background.
    """
    import threading
    import subprocess

    print(f"\n{'='*60}")
    print(f"  VE3 TOOL - PARALLEL MODE (PIC + VIDEO)")
    print(f"{'='*60}")
    print(f"  PIC worker:   Image generation (main)")
    print(f"  VIDEO worker: Video generation (background)")
    print(f"{'='*60}")

    # Start VIDEO worker in background process
    video_cmd = [sys.executable, str(TOOL_DIR / "run_worker_video.py")]
    if project:
        video_cmd.append(project)
    video_cmd.extend(["--worker-id", str(WORKER_ID), "--total-workers", str(TOTAL_WORKERS)])

    print(f"\n[PARALLEL] Starting VIDEO worker in background...")
    video_process = subprocess.Popen(
        video_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    # Thread to print VIDEO output
    def print_video_output():
        try:
            for line in video_process.stdout:
                print(f"[VIDEO] {line.rstrip()}")
        except:
            pass

    video_thread = threading.Thread(target=print_video_output, daemon=True)
    video_thread.start()

    # Run PIC worker in main thread
    print(f"\n[PARALLEL] Starting PIC worker (main)...")

    try:
        from run_worker_pic import run_scan_loop as pic_scan_loop, process_project_pic

        if project:
            process_project_pic(project)
        else:
            pic_scan_loop()

    except KeyboardInterrupt:
        print("\n\n[PARALLEL] Stopped by user.")

    finally:
        # Terminate VIDEO worker when PIC is done
        print(f"\n[PARALLEL] PIC worker finished. Stopping VIDEO worker...")
        try:
            video_process.terminate()
            video_process.wait(timeout=5)
        except:
            video_process.kill()

    print(f"[PARALLEL] Done!")


# Global worker settings (set from command line)
WORKER_ID = 0
TOTAL_WORKERS = 1


def main():
    global WORKER_ID, TOTAL_WORKERS

    import argparse
    parser = argparse.ArgumentParser(description='VE3 Worker - Image/Video Generation')
    parser.add_argument('project', nargs='?', default=None, help='Project code to process')
    parser.add_argument('--worker-id', type=int, default=0, help='Worker ID (0-based, for window layout)')
    parser.add_argument('--total-workers', type=int, default=1, help='Total number of workers (1=full, 2=split, ...)')
    parser.add_argument('--mode', choices=['all', 'pic', 'video', 'parallel'], default='all',
                        help='Mode: all (default), pic (image only), video (video only), parallel (pic+video separate)')
    args = parser.parse_args()

    # Set global worker settings
    WORKER_ID = args.worker_id
    TOTAL_WORKERS = args.total_workers

    if TOTAL_WORKERS > 1:
        pos_name = ["FULL", "LEFT", "RIGHT", "TOP-LEFT", "TOP-RIGHT", "BOTTOM"][min(WORKER_ID, 5)]
        print(f"[WORKER {WORKER_ID}/{TOTAL_WORKERS}] Window: {pos_name}")

    # Route to appropriate mode
    if args.mode == 'pic':
        from run_worker_pic import run_scan_loop as pic_loop, process_project_pic
        if args.project:
            process_project_pic(args.project)
        else:
            pic_loop()

    elif args.mode == 'video':
        from run_worker_video import run_scan_loop as video_loop, process_project_video
        if args.project:
            process_project_video(args.project)
        else:
            video_loop()

    elif args.mode == 'parallel':
        run_parallel_mode(args.project)

    else:  # 'all' - default behavior (combined)
        if args.project:
            run_single_project(args.project)
        else:
            run_scan_loop()


if __name__ == "__main__":
    main()
