"""
Robust file copy + Distributed Task Queue module.

1. Copy qua mạng ổn định (robocopy / shutil fallback)
2. AUTO path detection với fallback
3. Distributed TaskQueue (claim/release projects giữa nhiều VM)

Sử dụng:
    from modules.robust_copy import robust_copy_file, robust_copy_tree
    from modules.robust_copy import get_working_auto_path, robust_copy_to_master
    from modules.robust_copy import TaskQueue
"""

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, Callable


def _log_default(msg: str, level: str = "INFO"):
    """Default log function."""
    print(f"  [{level}] {msg}")


def robust_copy_file(
    src: str,
    dst: str,
    max_retries: int = 3,
    retry_delay: int = 5,
    verify: bool = True,
    log: Callable = None,
) -> bool:
    """
    Copy 1 file với retry + verify.

    Args:
        src: Đường dẫn file nguồn
        dst: Đường dẫn file đích
        max_retries: Số lần retry tối đa
        retry_delay: Thời gian chờ giữa các retry (giây)
        verify: Kiểm tra file size sau copy
        log: Hàm log

    Returns:
        True nếu copy thành công
    """
    log = log or _log_default
    src_path = Path(src)
    dst_path = Path(dst)

    if not src_path.exists():
        log(f"[COPY] File nguồn không tồn tại: {src}", "ERROR")
        return False

    src_size = src_path.stat().st_size

    # Tạo thư mục đích nếu chưa có
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        try:
            # Copy file
            shutil.copy2(str(src_path), str(dst_path))

            # Verify file size
            if verify and dst_path.exists():
                dst_size = dst_path.stat().st_size
                if dst_size == src_size:
                    return True
                else:
                    log(f"[COPY] Size mismatch: src={src_size} dst={dst_size} ({src_path.name})", "WARN")
                    # Xóa file lỗi, retry
                    try:
                        dst_path.unlink()
                    except Exception:
                        pass
            elif not verify:
                return True

        except Exception as e:
            log(f"[COPY] Lỗi copy {src_path.name} (lần {attempt + 1}/{max_retries}): {e}", "WARN")

        if attempt < max_retries - 1:
            log(f"[COPY] Retry sau {retry_delay}s...", "INFO")
            time.sleep(retry_delay)

    log(f"[COPY] THẤT BẠI copy {src_path.name} sau {max_retries} lần!", "ERROR")
    return False


def robust_copy_tree(
    src: str,
    dst: str,
    max_retries: int = 3,
    retry_delay: int = 5,
    verify: bool = True,
    use_robocopy: bool = True,
    log: Callable = None,
) -> bool:
    """
    Copy thư mục với robocopy (ưu tiên) hoặc shutil fallback.

    Args:
        src: Thư mục nguồn
        dst: Thư mục đích
        max_retries: Số lần retry
        retry_delay: Thời gian chờ giữa retry (giây)
        verify: Kiểm tra file count + sizes sau copy
        use_robocopy: Thử robocopy trước (Windows)
        log: Hàm log

    Returns:
        True nếu copy thành công
    """
    log = log or _log_default
    src_path = Path(src)
    dst_path = Path(dst)

    if not src_path.exists():
        log(f"[COPY] Thư mục nguồn không tồn tại: {src}", "ERROR")
        return False

    # Đếm files nguồn
    src_files = list(src_path.rglob("*"))
    src_file_count = sum(1 for f in src_files if f.is_file())
    src_total_size = sum(f.stat().st_size for f in src_files if f.is_file())
    log(f"[COPY] {src_path.name}: {src_file_count} files, {src_total_size / 1024 / 1024:.1f} MB")

    # === PHƯƠNG ÁN 1: robocopy (Windows native) ===
    if use_robocopy and os.name == 'nt':
        result = _robocopy_tree(src_path, dst_path, max_retries, retry_delay, log)
        if result:
            if verify:
                ok = _verify_copy(src_path, dst_path, log)
                if ok:
                    return True
                log(f"[COPY] robocopy verify FAIL, thử shutil fallback...", "WARN")
            else:
                return True

    # === PHƯƠNG ÁN 2: shutil với retry từng file ===
    return _shutil_copy_tree(src_path, dst_path, max_retries, retry_delay, verify, log)


def _robocopy_tree(
    src: Path,
    dst: Path,
    max_retries: int,
    retry_delay: int,
    log: Callable,
) -> bool:
    """Copy bằng robocopy (Windows native)."""
    try:
        # robocopy src dst /E /R:retries /W:wait /NP /NFL /NDL
        # /E = copy subdirectories including empty
        # /R:n = number of retries on failed copies
        # /W:n = wait time between retries (seconds)
        # /NP = no progress percentage
        # /MT:4 = multi-threaded (4 threads)
        cmd = [
            'robocopy',
            str(src),
            str(dst),
            '/E',           # Copy tất cả subdirectories
            f'/R:{max_retries}',    # Retry mỗi file
            f'/W:{retry_delay}',    # Wait giữa retry
            '/NP',          # Không hiện progress %
            '/MT:4',        # 4 threads song song
            '/DCOPY:T',     # Copy directory timestamps
            '/COPY:DAT',    # Copy Data, Attributes, Timestamps
        ]

        log(f"[ROBOCOPY] {src.name} → {dst.name}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 phút max
        )

        # robocopy exit codes:
        # 0 = no files copied (already up to date)
        # 1 = files copied successfully
        # 2 = extra files/dirs found
        # 3 = 1+2
        # 4 = mismatched files
        # 8 = some files could not be copied
        # 16 = fatal error
        if result.returncode < 8:
            log(f"[ROBOCOPY] OK (exit code {result.returncode})")
            return True
        else:
            log(f"[ROBOCOPY] FAIL (exit code {result.returncode})", "WARN")
            if result.stderr:
                log(f"[ROBOCOPY] stderr: {result.stderr[:200]}", "WARN")
            return False

    except FileNotFoundError:
        log(f"[ROBOCOPY] robocopy không tồn tại trên máy này", "WARN")
        return False
    except subprocess.TimeoutExpired:
        log(f"[ROBOCOPY] Timeout 10 phút!", "ERROR")
        return False
    except Exception as e:
        log(f"[ROBOCOPY] Lỗi: {e}", "WARN")
        return False


def _shutil_copy_tree(
    src: Path,
    dst: Path,
    max_retries: int,
    retry_delay: int,
    verify: bool,
    log: Callable,
) -> bool:
    """Copy bằng shutil - từng file với retry."""
    log(f"[SHUTIL] Copy {src.name} → {dst.name}")

    # Tạo thư mục đích
    dst.mkdir(parents=True, exist_ok=True)

    failed_files = []
    copied_count = 0
    total_files = 0

    for item in src.rglob("*"):
        if item.is_file():
            total_files += 1
            rel_path = item.relative_to(src)
            dest_file = dst / rel_path

            ok = robust_copy_file(
                str(item),
                str(dest_file),
                max_retries=max_retries,
                retry_delay=retry_delay,
                verify=verify,
                log=log,
            )

            if ok:
                copied_count += 1
            else:
                failed_files.append(str(rel_path))
        elif item.is_dir():
            # Tạo thư mục
            rel_path = item.relative_to(src)
            (dst / rel_path).mkdir(parents=True, exist_ok=True)

    if failed_files:
        log(f"[SHUTIL] FAIL: {len(failed_files)}/{total_files} files lỗi!", "ERROR")
        for f in failed_files[:5]:
            log(f"  - {f}", "ERROR")
        return False

    log(f"[SHUTIL] OK: {copied_count}/{total_files} files")
    return True


def _verify_copy(src: Path, dst: Path, log: Callable) -> bool:
    """Verify copy: so sánh file count + sizes."""
    src_files = {
        str(f.relative_to(src)): f.stat().st_size
        for f in src.rglob("*") if f.is_file()
    }
    dst_files = {
        str(f.relative_to(dst)): f.stat().st_size
        for f in dst.rglob("*") if f.is_file()
    }

    # Check missing files
    missing = set(src_files.keys()) - set(dst_files.keys())
    if missing:
        log(f"[VERIFY] THIẾU {len(missing)} files!", "ERROR")
        for f in list(missing)[:5]:
            log(f"  - {f}", "ERROR")
        return False

    # Check size mismatch
    size_mismatch = []
    for name, src_size in src_files.items():
        if name in dst_files and dst_files[name] != src_size:
            size_mismatch.append((name, src_size, dst_files[name]))

    if size_mismatch:
        log(f"[VERIFY] {len(size_mismatch)} files size SAI!", "ERROR")
        for name, ss, ds in size_mismatch[:5]:
            log(f"  - {name}: src={ss} dst={ds}", "ERROR")
        return False

    log(f"[VERIFY] OK: {len(src_files)} files verified")
    return True


POSSIBLE_AUTO_PATHS = [
    r"Z:\AUTO",
    r"Y:\AUTO",
    r"\\tsclient\D\AUTO",
    r"\\tsclient\C\AUTO",
    r"\\vmware-host\Shared Folders\D\AUTO",
    r"\\vmware-host\Shared Folders\AUTO",
    r"\\VBOXSVR\AUTO",
    r"D:\AUTO",
    r"C:\AUTO",
]

# =========================================================================
# SMB AUTO-RECONNECT
# =========================================================================

# Password chung cho tat ca SMB connections
_SMB_PASSWORD = "159753"
_SMB_USERNAME = "smbuser"
_SMB_SHARE = "D"
_SMB_DRIVE = "Z:"

# Danh sach IP may chu - thu lan luot neu khong co mapping cu
_MASTER_SERVERS = [
    "192.168.88.254",
    "192.168.88.14",
    "192.168.88.100",
]


def _load_smb_config() -> dict:
    """
    Load SMB config tu settings.yaml.

    settings.yaml co the override:
        smb:
            username: "smbuser"
            password: "159753"
            drive_letter: "Z:"
            share_name: "D"
            servers:
                - "192.168.88.254"
                - "192.168.88.14"
    """
    cfg = {
        "username": _SMB_USERNAME,
        "password": _SMB_PASSWORD,
        "drive_letter": _SMB_DRIVE,
        "share_name": _SMB_SHARE,
        "servers": [],
    }
    try:
        import yaml
        settings_path = Path(__file__).parent.parent / "config" / "settings.yaml"
        if settings_path.exists():
            with open(settings_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            smb = data.get('smb', {})
            if smb:
                for k in ('username', 'password', 'drive_letter', 'share_name'):
                    if smb.get(k):
                        cfg[k] = smb[k]
                if smb.get('servers'):
                    cfg['servers'] = smb['servers']
    except Exception:
        pass
    return cfg


def _get_existing_smb_mappings(log: Callable = None) -> list:
    """
    Lay danh sach SMB drive da map bang 'net use'.
    Tra ve list cua (drive_letter, unc_path).
    VD: [("Z:", "\\\\192.168.88.14\\D"), ("Y:", "\\\\10.0.0.5\\Share")]
    """
    log = log or _log_default
    mappings = []
    try:
        result = subprocess.run(
            ['net', 'use'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            import re
            # net use output format: "Status  Local  Remote  Network"
            # VD: "Unavailable Z:  \\192.168.88.14\D  Microsoft Windows Network"
            #     "OK          Z:  \\192.168.88.14\D  Microsoft Windows Network"
            for line in (result.stdout or "").split('\n'):
                # Tim dong co drive letter va UNC path
                m = re.search(r'([A-Z]:)\s+(\\\\[^\s]+)', line)
                if m:
                    mappings.append((m.group(1), m.group(2)))
    except Exception:
        pass
    return mappings


def ensure_smb_connected(log: Callable = None) -> bool:
    """
    v1.0.667: Dam bao SMB share da ket noi.

    Moi VM ket noi toi may chu RIENG (IP khac nhau), nen:
    1. Detect drive letter + UNC path DA MAP tu truoc (net use)
    2. Neu mat ket noi → reconnect voi CUNG UNC path + password
    3. Luu credential vao Windows Credential Manager

    KHONG hardcode IP - dung thong tin mapping co san.

    Returns:
        True neu it nhat 1 SMB drive accessible
    """
    log = log or _log_default
    cfg = _load_smb_config()
    user = cfg["username"]
    pwd = cfg["password"]

    # Check nhanh - co drive nao co AUTO khong?
    for path in POSSIBLE_AUTO_PATHS:
        if path.startswith("\\\\") or ":" not in path[:3]:
            continue  # Chi check drive letter paths (Z:\, Y:\)
        try:
            if Path(path).exists():
                return True
        except Exception:
            pass

    # Khong co drive nao accessible → tim mapping cu de reconnect
    log(f"[SMB] SMB drive mat ket noi, dang reconnect...", "WARN")
    mappings = _get_existing_smb_mappings(log)

    # === CACH 1: Reconnect mapping cu (da biet drive + UNC path) ===
    if mappings:
        for drive, unc_path in mappings:
            auto_check = f"{drive}\\AUTO"
            try:
                if Path(auto_check).exists():
                    return True
            except Exception:
                pass

            # Drive mat ket noi → reconnect voi password
            log(f"[SMB] Reconnect {drive} → {unc_path}...", "INFO")
            if _reconnect_drive(drive, unc_path, user, pwd, log):
                return True

    # === CACH 2: Khong co mapping cu → thu 3 IP may chu ===
    log(f"[SMB] Khong co mapping cu, thu ket noi may chu...", "INFO")
    drive = cfg.get("drive_letter", _SMB_DRIVE)
    share = cfg.get("share_name", _SMB_SHARE)

    # Doc danh sach server tu settings.yaml hoac dung default
    servers = list(_MASTER_SERVERS)
    cfg_servers = cfg.get("servers", [])
    if cfg_servers:
        # Them server tu config vao dau danh sach (uu tien)
        for s in reversed(cfg_servers):
            if s not in servers:
                servers.insert(0, s)

    for server_ip in servers:
        unc_path = f"\\\\{server_ip}\\{share}"
        log(f"[SMB] Thu {drive} → {unc_path}...", "INFO")
        if _reconnect_drive(drive, unc_path, user, pwd, log):
            return True

    log(f"[SMB] Khong ket noi duoc may chu nao!", "ERROR")
    return False


def _reconnect_drive(drive: str, unc_path: str, user: str, pwd: str, log: Callable) -> bool:
    """Reconnect 1 drive toi UNC path voi credential."""
    # Extract server tu UNC path
    try:
        server = unc_path.strip("\\").split("\\")[0]
    except Exception:
        server = ""

    try:
        # Xoa mapping cu (stale)
        subprocess.run(
            ['net', 'use', drive, '/delete', '/y'],
            capture_output=True, text=True, timeout=10
        )
    except Exception:
        pass

    try:
        cmd = [
            'net', 'use', drive, unc_path,
            f'/user:{user}', pwd,
            '/persistent:yes'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            time.sleep(1)
            auto_check = f"{drive}\\AUTO"
            try:
                if Path(auto_check).exists():
                    log(f"[SMB] [v] {drive} → {unc_path} thanh cong!", "INFO")
                else:
                    log(f"[SMB] [v] {drive} → {unc_path} OK (AUTO chua thay)", "INFO")
            except Exception:
                pass

            # Luu credential de Windows nho sau restart
            if server:
                _save_smb_credential(server, user, pwd, log)
            return True
        else:
            stderr = (result.stderr or "").strip()
            log(f"[SMB] {drive} → {unc_path} THAT BAI: {stderr}", "WARN")
            return False
    except subprocess.TimeoutExpired:
        log(f"[SMB] {drive} → {unc_path} timeout!", "WARN")
        return False
    except Exception as e:
        log(f"[SMB] {drive} → {unc_path} error: {e}", "WARN")
        return False


def _save_smb_credential(server: str, username: str, password: str, log: Callable = None):
    """
    Luu credential vao Windows Credential Manager.
    Sau khi luu, Windows tu dong dung credential nay khi ket noi lai
    → khong bi hoi password sau restart.
    """
    log = log or _log_default
    try:
        cmd = ['cmdkey', f'/add:{server}', f'/user:{username}', f'/pass:{password}']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            log(f"[SMB] Credential saved for {server}", "INFO")
    except Exception:
        pass


def find_auto_path(log: Callable = None) -> Optional[str]:
    """
    Tìm AUTO path khả dụng - thử nhiều đường dẫn.

    Returns:
        Đường dẫn AUTO path nếu tìm thấy, None nếu không
    """
    log = log or _log_default

    # v1.0.666: Thu reconnect SMB truoc khi tim path
    ensure_smb_connected(log)

    for path in POSSIBLE_AUTO_PATHS:
        try:
            p = Path(path)
            if p.exists() and p.is_dir():
                log(f"[AUTO] Tìm thấy: {path}")
                return path
        except Exception:
            continue

    log(f"[AUTO] Không tìm thấy AUTO path nào!", "ERROR")
    return None


def get_working_auto_path(current_path: str = None, log: Callable = None) -> Optional[str]:
    """
    Lấy AUTO path đang hoạt động.
    Nếu current_path vẫn accessible → dùng tiếp.
    Nếu current_path mất kết nối → reconnect SMB → thử lại → fallback.

    Args:
        current_path: Path hiện tại đang dùng (có thể None)
        log: Hàm log

    Returns:
        Đường dẫn AUTO path khả dụng, None nếu không có
    """
    log = log or _log_default

    # Kiểm tra path hiện tại
    if current_path:
        try:
            p = Path(current_path)
            if p.exists() and p.is_dir():
                return current_path
        except Exception:
            pass

        # v1.0.666: Path mat ket noi → thu reconnect SMB truoc khi bo cuoc
        log(f"[AUTO] Path KHÔNG truy cập được: {current_path}, thu reconnect SMB...", "WARN")
        if ensure_smb_connected(log):
            # Reconnect OK → thu lai path cu
            try:
                p = Path(current_path)
                if p.exists() and p.is_dir():
                    log(f"[AUTO] [v] Reconnect SMB thanh cong, {current_path} OK!", "INFO")
                    return current_path
            except Exception:
                pass

        log(f"[AUTO] Tìm path dự phòng...", "INFO")

    # Tìm path khác (dam bao SMB da reconnect)
    ensure_smb_connected(log)

    for path in POSSIBLE_AUTO_PATHS:
        if path == current_path:
            continue  # Đã thử rồi
        try:
            p = Path(path)
            if p.exists() and p.is_dir():
                log(f"[AUTO] Tìm thấy path dự phòng: {path}")
                return path
        except Exception:
            continue

    log(f"[AUTO] Không tìm thấy AUTO path nào!", "ERROR")
    return None


def robust_copy_to_master(
    src: str,
    relative_dest: str,
    current_auto_path: str = None,
    max_retries: int = 3,
    retry_delay: int = 5,
    log: Callable = None,
) -> tuple:
    """
    Copy thư mục sang master với fallback path.

    Nếu copy qua path hiện tại fail → tự tìm path khác và retry.

    Args:
        src: Thư mục nguồn (local)
        relative_dest: Đường dẫn tương đối trên master (e.g., "visual/AR3-0005")
        current_auto_path: AUTO path hiện tại
        max_retries: Số lần retry
        retry_delay: Delay giữa retry
        log: Hàm log

    Returns:
        (success: bool, used_auto_path: str or None)
    """
    log = log or _log_default

    # Thử path hiện tại trước
    paths_to_try = []
    if current_auto_path:
        paths_to_try.append(current_auto_path)
    # Thêm tất cả paths khác
    for p in POSSIBLE_AUTO_PATHS:
        if p not in paths_to_try:
            paths_to_try.append(p)

    for auto_path in paths_to_try:
        try:
            ap = Path(auto_path)
            if not ap.exists():
                continue
        except Exception:
            continue

        dst = str(Path(auto_path) / relative_dest)
        log(f"[COPY] Thử copy qua: {auto_path}")

        ok = robust_copy_tree(
            src, dst,
            max_retries=max_retries,
            retry_delay=retry_delay,
            verify=True,
            log=log,
        )

        if ok:
            return (True, auto_path)
        else:
            log(f"[COPY] FAIL qua {auto_path}, thử path khác...", "WARN")

    log(f"[COPY] TẤT CẢ paths đều FAIL!", "ERROR")
    return (False, None)


# ============================================================================
# DISTRIBUTED TASK QUEUE
# Quản lý claim/release projects giữa nhiều VM.
#
# Cơ chế:
# - Mỗi project trên master có thể được "claim" bởi 1 VM duy nhất
# - Claim = tạo file _CLAIMED trong thư mục project
# - Race condition: ghi → đợi → đọc lại → verify
# - Timeout: VM chết → _CLAIMED quá hạn → tự giải phóng
# - Account: Đọc từ Google Sheet 1 lần khi claim → cache trong _CLAIMED
#
# _CLAIMED format:
#     Line 1: VM_ID (e.g., AR8-T1)
#     Line 2: Timestamp (e.g., 2026-03-21 10:30:00)
#     Line 3: Hostname
#     Line 4: Account (e.g., email@gmail.com|password|totp_secret) - optional
#
# Sử dụng:
#     from modules.robust_copy import TaskQueue
#
#     tq = TaskQueue(master_projects_path, vm_id="AR8-T1")
#     project = tq.claim_next()  # Lấy 1 project chưa ai claim
#     if project:
#         account = tq.get_account(project)  # Lấy account từ _CLAIMED cache
#         tq.release(project)    # Xong → giải phóng
# ============================================================================

import random
import socket
from datetime import datetime
from typing import List

CLAIMED_FILE = "_CLAIMED"
CLAIM_TIMEOUT_HOURS = 12  # VM chết → giải phóng sau 12 giờ


class TaskQueue:
    """Distributed task queue dùng file-based claiming."""

    def __init__(
        self,
        master_projects: str,
        vm_id: str,
        visual_path: str = None,
        tool_dir: str = None,
        timeout_hours: float = CLAIM_TIMEOUT_HOURS,
        log: Callable = None,
    ):
        self.master_projects = Path(master_projects)
        self.vm_id = vm_id
        self.visual_path = Path(visual_path) if visual_path else None
        self.tool_dir = Path(tool_dir) if tool_dir else None
        self.timeout_hours = timeout_hours
        self.log = log or _log_default
        self.hostname = socket.gethostname()
        self._sheet_cache = None
        self._thongtin_cache = None

    def scan_available(self) -> List[str]:
        """Scan tất cả projects chưa được claim."""
        available = []
        if not self.master_projects.exists():
            self.log(f"[QUEUE] master_projects NOT exists: {self.master_projects}")
            return available
        try:
            scanned = 0
            for item in self.master_projects.iterdir():
                if not item.is_dir():
                    continue
                scanned += 1
                code = item.name
                claimed_file = item / CLAIMED_FILE
                srt_files = list(item.glob("*.srt"))
                if not srt_files:
                    self.log(f"[QUEUE] {code}: skip - no SRT files")
                    continue
                if self._is_in_visual(code):
                    self.log(f"[QUEUE] {code}: skip - already in visual")
                    continue
                if claimed_file.exists():
                    # v1.0.429: Có _CLAIMED = skip, KHÔNG xóa bất kể thời gian
                    continue
                # v1.0.387: Skip nếu có VM khác đang claim (lock file _CLAIMING_*)
                # Cleanup stale _CLAIMING_ files (>5 phút = VM crash mid-claim)
                has_claiming = False
                try:
                    for f in item.iterdir():
                        if f.name.startswith("_CLAIMING_"):
                            try:
                                age_sec = time.time() - f.stat().st_mtime
                                if age_sec > 300:  # 5 phút
                                    f.unlink()
                                    self.log(f"[QUEUE] {code}: xóa stale {f.name} ({age_sec:.0f}s)")
                                    continue
                            except Exception:
                                pass
                            has_claiming = True
                            break
                except Exception:
                    pass
                if has_claiming:
                    continue
                available.append(code)
            self.log(f"[QUEUE] Scanned {scanned} dirs, {len(available)} available")
        except Exception as e:
            self.log(f"[QUEUE] Lỗi scan: {e}", "ERROR")
        return sorted(available)

    def claim_next(self, preferred_channel: str = None) -> Optional[str]:
        """Claim project tiếp theo chưa ai lấy."""
        available = self.scan_available()
        if not available:
            self.log(f"[QUEUE] Không có project nào available")
            return None
        if preferred_channel:
            own_channel = [c for c in available if c.startswith(preferred_channel)]
            other_channel = [c for c in available if not c.startswith(preferred_channel)]
            ordered = own_channel + other_channel
        else:
            ordered = available
        for code in ordered:
            if self.claim(code):
                return code
        self.log(f"[QUEUE] Không claim được project nào (tất cả đã bị lấy)")
        return None

    def claim(self, code: str) -> bool:
        """
        Claim 1 project cụ thể.

        v1.0.387: Atomic claim - dùng O_CREAT|O_EXCL để tránh race condition.
        Flow:
          1. Xóa _CLAIMED cũ nếu hết hạn
          2. Tạo _CLAIMING_{VM_ID} bằng O_CREAT|O_EXCL (atomic, không ghi đè được)
          3. Ghi nội dung claim
          4. Rename _CLAIMING_{VM_ID} → _CLAIMED
          5. Đợi 3s NFS sync → đọc lại verify
        """
        project_dir = self.master_projects / code
        claimed_file = project_dir / CLAIMED_FILE
        claiming_file = project_dir / f"_CLAIMING_{self.vm_id}"
        if not project_dir.exists():
            return False

        # Bước 1: Check _CLAIMED hiện có
        # v1.0.429: Có _CLAIMED = KHÔNG claim, bất kể thời gian
        if claimed_file.exists():
            try:
                first_line = claimed_file.read_text(encoding='utf-8').split('\n')[0].strip()
                if first_line == self.vm_id:
                    return True  # Đã claim rồi
            except Exception:
                pass
            return False

        try:
            # v1.0.669: Timeout + retry cho Google Sheet calls
            # KHONG dung 'with' vi shutdown(wait=True) se doi thread treo xong
            import concurrent.futures
            account_str = ""
            topic_str = ""
            character_str = ""

            def _call_with_timeout_retry(func, args, timeout_s=15, max_retries=3, label=""):
                """Goi func voi timeout va retry. KHONG block neu timeout."""
                for attempt in range(max_retries):
                    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                    try:
                        fut = executor.submit(func, *args)
                        result = fut.result(timeout=timeout_s)
                        executor.shutdown(wait=False)
                        if attempt > 0:
                            self.log(f"[QUEUE] {label} OK sau {attempt + 1} lan thu")
                        return result
                    except concurrent.futures.TimeoutError:
                        executor.shutdown(wait=False)  # Bo thread treo, khong doi
                        self.log(f"[QUEUE] {label} timeout ({timeout_s}s) lan {attempt + 1}/{max_retries}", "WARN")
                        self._sheet_cache = None
                        self._thongtin_cache = None
                    except Exception as e:
                        executor.shutdown(wait=False)
                        self.log(f"[QUEUE] {label} error lan {attempt + 1}: {e}", "WARN")
                        self._sheet_cache = None
                        self._thongtin_cache = None
                return ""

            try:
                self.log(f"[QUEUE] {code}: loading account/topic from Google Sheet...")
                account_str = _call_with_timeout_retry(
                    self._get_account_from_sheet, (code,), timeout_s=15, label=f"{code} account")
                topic_str = _call_with_timeout_retry(
                    self._get_topic_from_sheet, (code,), timeout_s=10, label=f"{code} topic")
                character_str = _call_with_timeout_retry(
                    self._get_character_from_sheet, (code,), timeout_s=10, label=f"{code} character")
            except Exception as e:
                self.log(f"[QUEUE] {code}: Google Sheet error: {e} - claim without metadata", "WARN")
            claim_content = self._make_claim_content(account=account_str, topic=topic_str, character=character_str)

            # Bước 2: Tạo file _CLAIMING_{VM_ID} bằng O_CREAT|O_EXCL (atomic)
            # Nếu file đã tồn tại → FileExistsError → VM khác đang claim
            try:
                fd = os.open(str(claiming_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, claim_content.encode('utf-8'))
                os.close(fd)
            except FileExistsError:
                self.log(f"[QUEUE] {code}: VM khác đang claim (lock file exists)", "INFO")
                return False

            # Bước 3: Check xem có VM khác cũng đang _CLAIMING không
            time.sleep(1)  # Đợi NFS sync các _CLAIMING files
            other_claimers = []
            try:
                for f in project_dir.iterdir():
                    if f.name.startswith("_CLAIMING_") and f.name != f"_CLAIMING_{self.vm_id}":
                        other_claimers.append(f.name)
            except Exception:
                pass

            if other_claimers:
                # Có VM khác cũng đang claim → dùng tên VM sort để quyết định ai thắng
                all_claimers = sorted([self.vm_id] + [c.replace("_CLAIMING_", "") for c in other_claimers])
                winner = all_claimers[0]  # VM có tên nhỏ nhất thắng
                if winner != self.vm_id:
                    self.log(f"[QUEUE] {code}: thua {winner} (sort order)", "INFO")
                    try:
                        claiming_file.unlink()
                    except Exception:
                        pass
                    return False

            # Bước 4: Rename → _CLAIMED (ta là người duy nhất hoặc thắng sort)
            try:
                # Xóa _CLAIMED cũ (nếu xuất hiện do race)
                if claimed_file.exists():
                    try:
                        first_line = claimed_file.read_text(encoding='utf-8').split('\n')[0].strip()
                    except Exception:
                        first_line = ""
                    if first_line and first_line != self.vm_id:
                        self.log(f"[QUEUE] {code}: _CLAIMED xuất hiện bởi {first_line} → thua", "INFO")
                        try:
                            claiming_file.unlink()
                        except Exception:
                            pass
                        return False

                # Ghi trực tiếp vào _CLAIMED (rename trên NFS không reliable bằng write)
                claimed_file.write_text(claim_content, encoding='utf-8')
            except Exception as e:
                self.log(f"[QUEUE] {code}: lỗi ghi _CLAIMED: {e}", "ERROR")
                try:
                    claiming_file.unlink()
                except Exception:
                    pass
                return False

            # Cleanup _CLAIMING file
            try:
                claiming_file.unlink()
            except Exception:
                pass

            # Bước 5: Đợi NFS sync → đọc lại verify
            time.sleep(3)
            try:
                read_back = claimed_file.read_text(encoding='utf-8').strip()
                first_line = read_back.split('\n')[0].strip()
            except Exception:
                self.log(f"[QUEUE] {code}: không đọc lại được _CLAIMED", "WARN")
                return False

            if first_line == self.vm_id:
                self.log(f"[QUEUE] CLAIMED: {code} → {self.vm_id}")
                if account_str:
                    email = account_str.split('|')[0] if '|' in account_str else account_str
                    self.log(f"[QUEUE] Account: {email}")
                return True
            else:
                self.log(f"[QUEUE] {code}: đã bị {first_line} claim trước (verify fail)", "INFO")
                return False
        except Exception as e:
            self.log(f"[QUEUE] Lỗi claim {code}: {e}", "ERROR")
            # Cleanup
            try:
                claiming_file.unlink()
            except Exception:
                pass
            return False

    def release(self, code: str) -> bool:
        """Giải phóng claim sau khi xong việc."""
        claimed_file = self.master_projects / code / CLAIMED_FILE
        return self._remove_claimed(claimed_file)

    def is_claimed_by_me(self, code: str) -> bool:
        """Check xem project có được claim bởi VM này không."""
        claimed_file = self.master_projects / code / CLAIMED_FILE
        if not claimed_file.exists():
            return False
        try:
            content = claimed_file.read_text(encoding='utf-8').strip()
            first_line = content.split('\n')[0].strip()
            return first_line == self.vm_id
        except Exception:
            return False

    def get_my_claims(self) -> List[str]:
        """Lấy danh sách projects mà VM này đang claim."""
        claims = []
        if not self.master_projects.exists():
            return claims
        try:
            for item in self.master_projects.iterdir():
                if item.is_dir() and self.is_claimed_by_me(item.name):
                    claims.append(item.name)
        except Exception:
            pass
        return sorted(claims)

    def cleanup_stale_claims(self) -> List[str]:
        """v1.0.429: Không tự động xóa claim nữa - an toàn tuyệt đối."""
        return []

    def get_status(self) -> dict:
        """Lấy trạng thái tổng quan của queue."""
        status = {"total": 0, "available": 0, "claimed": {}, "expired": 0}
        if not self.master_projects.exists():
            return status
        try:
            for item in self.master_projects.iterdir():
                if not item.is_dir():
                    continue
                srt_files = list(item.glob("*.srt"))
                if not srt_files:
                    continue
                if self._is_in_visual(item.name):
                    continue
                status["total"] += 1
                claimed_file = item / CLAIMED_FILE
                if not claimed_file.exists():
                    status["available"] += 1
                else:
                    vm_id = self._read_claim_vm_id(claimed_file)
                    if vm_id:
                        if vm_id not in status["claimed"]:
                            status["claimed"][vm_id] = []
                        status["claimed"][vm_id].append(item.name)
        except Exception:
            pass
        return status

    # --- Private methods ---

    def _make_claim_content(self, account: str = "", topic: str = "", character: str = "") -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"{self.vm_id}\n{timestamp}\n{self.hostname}\n{account}\n{topic}\n{character}\n"

    def _is_claim_expired(self, claimed_file: Path) -> bool:
        try:
            content = claimed_file.read_text(encoding='utf-8').strip()
            lines = content.split('\n')
            if len(lines) < 2:
                return True
            timestamp_str = lines[1].strip()
            claim_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            elapsed_hours = (datetime.now() - claim_time).total_seconds() / 3600
            return elapsed_hours > self.timeout_hours
        except Exception as e:
            # v1.0.428: KHÔNG coi là expired khi đọc file lỗi (NFS glitch)
            # Trước đây return True → VM khác xóa claim hợp lệ và cướp project
            self.log(f"[QUEUE] Lỗi đọc _CLAIMED: {e} → giữ nguyên (không expired)", "WARN")
            return False

    def _read_claim_vm_id(self, claimed_file: Path) -> Optional[str]:
        try:
            content = claimed_file.read_text(encoding='utf-8').strip()
            first_line = content.split('\n')[0].strip()
            return first_line if first_line else None
        except Exception:
            return None

    def _is_in_visual(self, code: str) -> bool:
        if not self.visual_path:
            return False
        try:
            visual_dir = self.visual_path / code
            return visual_dir.exists() and any(visual_dir.iterdir())
        except Exception:
            return False

    def _remove_claimed(self, claimed_file: Path) -> bool:
        try:
            if claimed_file.exists():
                os.unlink(str(claimed_file))
            return True
        except Exception as e:
            self.log(f"[QUEUE] Không xóa được _CLAIMED: {e}", "WARN")
            return False

    # --- Account methods ---

    def get_account(self, code: str) -> Optional[dict]:
        """Lấy account từ _CLAIMED cache."""
        claimed_file = self.master_projects / code / CLAIMED_FILE
        account_str = self._read_account_from_claimed(claimed_file)
        if not account_str and self.tool_dir:
            local_claimed = self.tool_dir / "PROJECTS" / code / CLAIMED_FILE
            account_str = self._read_account_from_claimed(local_claimed)
        if account_str:
            return self._parse_account_string(account_str)
        return None

    def _read_account_from_claimed(self, claimed_file: Path) -> Optional[str]:
        try:
            if not claimed_file.exists():
                return None
            content = claimed_file.read_text(encoding='utf-8').strip()
            lines = content.split('\n')
            if len(lines) >= 4:
                account_str = lines[3].strip()
                return account_str if account_str else None
        except Exception:
            pass
        return None

    def _parse_account_string(self, account_str: str) -> Optional[dict]:
        if not account_str:
            return None
        parts = account_str.split('|')
        if len(parts) >= 2:
            return {
                "id": parts[0].strip(),
                "password": parts[1].strip(),
                "totp_secret": parts[2].strip() if len(parts) >= 3 else "",
            }
        return None

    def _get_account_from_sheet(self, code: str) -> str:
        """Đọc account từ Google Sheet NGUON. Col G = code, Col R = account."""
        if not self.tool_dir:
            return ""
        try:
            if self._sheet_cache is None:
                self._sheet_cache = self._load_nguon_sheet()
            if not self._sheet_cache:
                return ""
            code_upper = code.upper()
            for row in self._sheet_cache:
                if len(row) > 17:
                    cell_g = str(row[6]).strip().upper()
                    if cell_g == code_upper:
                        account = str(row[17]).strip()
                        if account:
                            self.log(f"[QUEUE] Found account for {code} from sheet")
                            return account
            self.log(f"[QUEUE] No account found for {code} in sheet NGUON", "WARN")
            return ""
        except Exception as e:
            self.log(f"[QUEUE] Lỗi đọc sheet: {e}", "WARN")
            return ""

    def _get_character_from_sheet(self, code: str) -> str:
        """Đọc character template từ Google Sheet THONG TIN. Col G = code, Col L = character prompt.

        Col L chứa portrait prompt của nhân vật chính (nếu có).
        Nếu có giá trị → dùng thay cho nhân vật mặc định trong topic prompts.
        """
        if not self.tool_dir:
            return ""
        try:
            if self._thongtin_cache is None:
                self._thongtin_cache = self._load_thongtin_sheet()
            if not self._thongtin_cache:
                return ""
            code_upper = code.upper()
            for row in self._thongtin_cache:
                if len(row) > 11:  # Cần ít nhất 12 cột (A-L)
                    cell_g = str(row[6]).strip().upper()  # Col G = code
                    if cell_g == code_upper:
                        char_prompt = str(row[11]).strip()  # Col L = index 11
                        if char_prompt:
                            self.log(f"[QUEUE] Found character template for {code} from sheet THONG TIN")
                            return char_prompt
            self.log(f"[QUEUE] No character template for {code} in sheet THONG TIN col L", "INFO")
            return ""
        except Exception as e:
            self.log(f"[QUEUE] Lỗi đọc character từ sheet THONG TIN: {e}", "WARN")
            return ""

    def _load_thongtin_sheet(self) -> list:
        """Load sheet THONG TIN từ Google Sheets (1 lần)."""
        try:
            import json as _json
            config_file = self.tool_dir / "config" / "config.json"
            if not config_file.exists():
                return []
            cfg = _json.loads(config_file.read_text(encoding='utf-8'))
            sa_path = (
                cfg.get("SERVICE_ACCOUNT_JSON") or
                cfg.get("CREDENTIAL_PATH") or
                "creds.json"
            )
            spreadsheet_name = cfg.get("SPREADSHEET_NAME")
            if not spreadsheet_name:
                return []
            sa_file = Path(sa_path)
            if not sa_file.exists():
                sa_file = self.tool_dir / "config" / sa_path
            if not sa_file.exists():
                return []
            import gspread
            from google.oauth2.service_account import Credentials
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/drive.readonly",
            ]
            creds = Credentials.from_service_account_file(str(sa_file), scopes=scopes)
            gc = gspread.authorize(creds)
            ws = gc.open(spreadsheet_name).worksheet("THONG TIN")
            data = ws.get_all_values()
            self.log(f"[QUEUE] Loaded sheet THONG TIN: {len(data)} rows")
            return data
        except Exception as e:
            self.log(f"[QUEUE] Lỗi load sheet THONG TIN: {e}", "WARN")
            return []

    def _get_topic_from_sheet(self, code: str) -> str:
        """Đọc topic từ Google Sheet NGUON. Col G = code, Col S = topic.

        Col S chứa chủ đề: truyện, tâm lý, tài chính, tài chính vn
        """
        if not self.tool_dir:
            return ""
        try:
            if self._sheet_cache is None:
                self._sheet_cache = self._load_nguon_sheet()
            if not self._sheet_cache:
                return ""
            code_upper = code.upper()
            for row in self._sheet_cache:
                if len(row) > 18:  # Cần ít nhất 19 cột (A-S)
                    cell_g = str(row[6]).strip().upper()
                    if cell_g == code_upper:
                        topic = str(row[18]).strip()  # Col S = index 18
                        if topic:
                            self.log(f"[QUEUE] Found topic for {code}: {topic}")
                            return topic
            self.log(f"[QUEUE] No topic found for {code} in sheet NGUON col S", "WARN")
            return ""
        except Exception as e:
            self.log(f"[QUEUE] Lỗi đọc topic từ sheet: {e}", "WARN")
            return ""

    def _load_nguon_sheet(self) -> list:
        """Load toàn bộ sheet NGUON từ Google Sheets (1 lần)."""
        try:
            import json as _json
            config_file = self.tool_dir / "config" / "config.json"
            if not config_file.exists():
                self.log(f"[QUEUE] config.json not found", "WARN")
                return []
            cfg = _json.loads(config_file.read_text(encoding='utf-8'))
            sa_path = (
                cfg.get("SERVICE_ACCOUNT_JSON") or
                cfg.get("CREDENTIAL_PATH") or
                "creds.json"
            )
            spreadsheet_name = cfg.get("SPREADSHEET_NAME")
            if not spreadsheet_name:
                self.log(f"[QUEUE] Missing SPREADSHEET_NAME in config", "WARN")
                return []
            sa_file = Path(sa_path)
            if not sa_file.exists():
                sa_file = self.tool_dir / "config" / sa_path
            if not sa_file.exists():
                self.log(f"[QUEUE] Creds file not found: {sa_path}", "WARN")
                return []
            import gspread
            from google.oauth2.service_account import Credentials
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/drive.readonly",
            ]
            creds = Credentials.from_service_account_file(str(sa_file), scopes=scopes)
            gc = gspread.authorize(creds)
            ws = gc.open(spreadsheet_name).worksheet("NGUON")
            data = ws.get_all_values()
            self.log(f"[QUEUE] Loaded sheet NGUON: {len(data)} rows")
            return data
        except Exception as e:
            self.log(f"[QUEUE] Lỗi load sheet NGUON: {e}", "WARN")
            return []


# ============================================================================
# DELETE UTILITIES
# ============================================================================

def robust_delete_tree(
    path: str,
    max_retries: int = 3,
    retry_delay: int = 3,
    log: Callable = None,
) -> bool:
    """
    Xóa thư mục với retry + force delete readonly files.

    Returns:
        True nếu xóa thành công
    """
    log = log or _log_default
    dir_path = Path(path)

    if not dir_path.exists():
        return True

    def _on_rm_error(func, path, exc_info):
        """Handle readonly files on Windows."""
        import stat
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass

    for attempt in range(max_retries):
        try:
            shutil.rmtree(str(dir_path), onerror=_on_rm_error)
            if not dir_path.exists():
                return True
        except Exception as e:
            log(f"[DELETE] Lỗi xóa {dir_path.name} (lần {attempt + 1}/{max_retries}): {e}", "WARN")

        if attempt < max_retries - 1:
            time.sleep(retry_delay)

    log(f"[DELETE] THẤT BẠI xóa {dir_path.name} sau {max_retries} lần!", "WARN")
    return False
