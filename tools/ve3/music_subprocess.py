#!/usr/bin/env python3
"""
Music worker subprocess - runs Suno music generation in an isolated process.

Usage:
    python music_subprocess.py <project_dir> <excel_path> --chrome <chrome_exe>

Outputs structured lines on stdout:
    @@LOG|LEVEL|message
    @@MUSIC_PROGRESS|done_count|total_count
    @@MUSIC_TRACK|music_id|status|detail
    @@RESULT|{"success": true, "done": N, "total": M}
"""

import sys
import os
import json
import time
import atexit
import subprocess as sp
import urllib.request
from pathlib import Path

# Setup paths
SCRIPT_DIR = Path(__file__).parent
SUITE_ROOT = SCRIPT_DIR.parents[1] if SCRIPT_DIR.parent.name.lower() == "tools" else SCRIPT_DIR
SUNO_DIR = SUITE_ROOT / "tools" / "suno"

sys.path.insert(0, str(SCRIPT_DIR))
if SUNO_DIR.exists():
    sys.path.insert(0, str(SUNO_DIR))

MUSIC_GLOBAL_LOCK = SUITE_ROOT / "tools" / "ve3" / ".music_global.lock"
SUNO_WINDOW_SIZE = "1600,1200"
SUNO_WINDOW_POSITION = "3200,40"


def log(msg, level="INFO"):
    msg_clean = str(msg).replace("\n", " ").replace("\r", "")
    sys.stdout.write(f"@@LOG|{level}|{msg_clean}\n")
    sys.stdout.flush()


def music_progress(done, total):
    sys.stdout.write(f"@@MUSIC_PROGRESS|{done}|{total}\n")
    sys.stdout.flush()


def music_track(music_id, status, detail=""):
    detail_clean = str(detail).replace("\n", " ")
    sys.stdout.write(f"@@MUSIC_TRACK|{music_id}|{status}|{detail_clean}\n")
    sys.stdout.flush()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _release_lock(lock_path: Path, owner_pid: int) -> None:
    try:
        if lock_path.exists():
            content = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
            current_owner = int(content.split("|", 1)[0]) if content else 0
            if current_owner == owner_pid:
                lock_path.unlink()
    except Exception:
        pass


def acquire_music_global_lock(lock_path: Path, project_code: str, wait_log_sec: int = 20):
    owner_pid = os.getpid()
    started = time.time()
    next_log_at = 0.0
    while True:
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"{owner_pid}|{project_code}|{int(started)}")
            atexit.register(_release_lock, lock_path, owner_pid)
            return owner_pid
        except FileExistsError:
            try:
                content = lock_path.read_text(encoding="utf-8", errors="ignore").strip()
                parts = content.split("|")
                lock_pid = int(parts[0]) if parts and parts[0].isdigit() else 0
                lock_code = parts[1] if len(parts) > 1 else "?"
            except Exception:
                lock_pid = 0
                lock_code = "?"

            if lock_pid and not _pid_alive(lock_pid):
                try:
                    lock_path.unlink()
                    log(f"[MUSIC] Xoa stale global music lock cua PID={lock_pid} ({lock_code})", "WARN")
                    continue
                except Exception:
                    pass

            now = time.time()
            if now >= next_log_at:
                waited = int(now - started)
                log(
                    f"[MUSIC] Dang cho global music lock ({waited}s). "
                    f"Project dang giu: {lock_code} PID={lock_pid or '?'}",
                    "INFO",
                )
                next_log_at = now + wait_log_sec
            time.sleep(2)


def kill_pid_tree(pid: int) -> None:
    if pid <= 0:
        return
    try:
        sp.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=sp.DEVNULL,
            stderr=sp.DEVNULL,
            check=False,
            timeout=20,
        )
    except Exception:
        pass


def is_suno_browser_ready(timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:9444/json/version", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def cleanup_existing_suno_chrome() -> None:
    """Close Suno Chrome instances on the dedicated 9444 profile/port."""
    try:
        ps = r"""
Get-CimInstance Win32_Process |
  Where-Object {
    ($_.Name -eq 'chrome.exe' -or $_.Name -eq 'GoogleChromePortable.exe') -and
    $_.CommandLine -and
    $_.CommandLine -like '*tools\suno\GoogleChromePortable*' -and
    $_.CommandLine -like '*remote-debugging-port=9444*'
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
"""
        sp.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            stdout=sp.DEVNULL,
            stderr=sp.DEVNULL,
            check=False,
            timeout=20,
        )
    except Exception:
        pass


def build_suno_chrome_args(chrome_exe: Path, window_size: str, window_position: str):
    return [
        str(chrome_exe),
        "--remote-debugging-port=9444",
        "--no-first-run",
        "--new-window",
        f"--window-size={window_size}",
        f"--window-position={window_position}",
        "https://suno.com/create",
    ]


def launch_or_reuse_suno_browser(chrome_exe: Path, window_size: str, window_position: str):
    if is_suno_browser_ready():
        log("[MUSIC] Reuse existing Suno browser on port 9444", "INFO")
        return None
    cleanup_existing_suno_chrome()
    proc = sp.Popen(
        build_suno_chrome_args(chrome_exe, window_size, window_position),
        cwd=str(chrome_exe.parent),
        stdout=sp.DEVNULL,
        stderr=sp.DEVNULL,
    )
    for _ in range(16):
        time.sleep(0.5)
        if is_suno_browser_ready():
            return proc
    return proc


def restart_suno_browser(chrome_exe: Path, window_size: str, window_position: str):
    cleanup_existing_suno_chrome()
    time.sleep(2)
    return launch_or_reuse_suno_browser(chrome_exe, window_size, window_position)


def get_music_tracks(wb):
    """Get music tracks from workbook (compatible with different versions)."""
    if hasattr(wb, "get_music_tracks"):
        return wb.get_music_tracks()

    sheet_name = getattr(wb, "MUSIC_SHEET", "music")
    workbook = getattr(wb, "workbook", None)
    if not workbook or sheet_name not in workbook.sheetnames:
        return []

    ws = workbook[sheet_name]
    headers = [cell.value for cell in ws[1]]
    columns = [
        "music_id", "start_time", "duration", "title", "suno_prompt",
        "style_tags", "mood", "scene_range", "suno_url", "status",
    ]
    header_map = {str(h): i for i, h in enumerate(headers) if h}
    if not header_map:
        return []

    tracks = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        music_id_idx = header_map.get("music_id")
        if music_id_idx is None or music_id_idx >= len(row) or row[music_id_idx] is None:
            continue
        track = {}
        for name in columns:
            idx = header_map.get(name)
            val = row[idx] if idx is not None and idx < len(row) else None
            track[name] = str(val) if val is not None else ""
        tracks.append(track)
    return tracks


def update_music_track(wb, music_id, **kwargs):
    """Update music track status in workbook."""
    if hasattr(wb, "update_music_track"):
        return wb.update_music_track(music_id, **kwargs)

    sheet_name = getattr(wb, "MUSIC_SHEET", "music")
    workbook = getattr(wb, "workbook", None)
    if not workbook or sheet_name not in workbook.sheetnames:
        return False

    ws = workbook[sheet_name]
    headers = [cell.value for cell in ws[1]]
    header_map = {str(h): i + 1 for i, h in enumerate(headers) if h}
    if "music_id" not in header_map:
        return False

    for row_idx in range(2, ws.max_row + 1):
        cell_val = ws.cell(row=row_idx, column=header_map["music_id"]).value
        if cell_val is not None and str(cell_val) == str(music_id):
            for key, value in kwargs.items():
                col_idx = header_map.get(key)
                if col_idx:
                    ws.cell(row=row_idx, column=col_idx, value=value)
            if hasattr(wb, "safe_save"):
                wb.safe_save()
            elif hasattr(wb, "save"):
                wb.save()
            return True
    return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Music Worker Subprocess")
    parser.add_argument("project_dir", help="Project directory")
    parser.add_argument("excel_path", help="Path to Excel file")
    parser.add_argument("--chrome", required=True, help="Path to Chrome portable exe")
    parser.add_argument("--window-size", default=SUNO_WINDOW_SIZE, help="Chrome window size WxH")
    parser.add_argument("--window-position", default=SUNO_WINDOW_POSITION, help="Chrome window position X,Y")
    args = parser.parse_args()

    project_dir = Path(args.project_dir)
    excel_path = Path(args.excel_path)
    chrome_exe = Path(args.chrome)
    window_size = str(args.window_size or SUNO_WINDOW_SIZE).strip() or SUNO_WINDOW_SIZE
    window_position = str(args.window_position or SUNO_WINDOW_POSITION).strip() or SUNO_WINDOW_POSITION
    lock_owner_pid = None
    chrome_proc = None

    result = {"success": False, "done": 0, "total": 0}

    try:
        lock_owner_pid = acquire_music_global_lock(MUSIC_GLOBAL_LOCK, project_dir.name)
        log(f"[MUSIC] Da giu global music lock cho {project_dir.name}", "SUCCESS")

        from modules.excel_manager import PromptWorkbook

        wb = PromptWorkbook(str(excel_path))
        wb.load_or_create()

        tracks = get_music_tracks(wb)
        pending = []
        for track in tracks:
            music_id = str(track.get("music_id", "")).strip()
            prompt = str(track.get("suno_prompt", "")).strip()
            if not music_id or not prompt:
                continue
            out_mp3 = project_dir / "music" / f"{music_id}.mp3"
            if not out_mp3.exists():
                pending.append(track)

        result["total"] = len(pending)

        if not pending:
            log(f"[MUSIC] {project_dir.name}: da du mp3, khong can tao nhac")
            result["success"] = True
            sys.stdout.write(f"@@RESULT|{json.dumps(result)}\n")
            sys.stdout.flush()
            sys.exit(0)

        log(f"[MUSIC] {project_dir.name}: bat dau tao nhac ({len(pending)} tracks)")
        music_progress(0, len(pending))

        # Launch Chrome
        if chrome_exe.exists():
            log(f"[MUSIC] Mo/ket noi Suno browser: {chrome_exe}")
            chrome_proc = launch_or_reuse_suno_browser(chrome_exe, window_size, window_position)
            time.sleep(3)
        else:
            log(f"[MUSIC] Khong tim thay Chrome: {chrome_exe}", "ERROR")
            sys.stdout.write(f"@@RESULT|{json.dumps(result)}\n")
            sys.stdout.flush()
            sys.exit(1)

        # Connect and generate
        from token_manager import TokenManager
        from suno_browser_worker import BrowserSunoWorker

        log("[MUSIC] Ket noi browser Suno...")

        with TokenManager(auto_launch=False) as tm:
            if not getattr(tm, "_page", None):
                raise RuntimeError("Could not connect to Suno browser session on port 9444")
            log("[MUSIC] Da ket noi browser Suno", "SUCCESS")
            worker = BrowserSunoWorker(tm._page)
            done = 0

            for idx, track in enumerate(pending, start=1):
                music_id = str(track.get("music_id", "")).strip()
                title = (track.get("title") or f"Track {music_id}").strip()
                prompt = (track.get("suno_prompt") or "").strip()
                status = (track.get("status") or "").strip().lower()
                out_mp3 = project_dir / "music" / f"{music_id}.mp3"
                out_mp3.parent.mkdir(parents=True, exist_ok=True)

                if out_mp3.exists():
                    done += 1
                    if status != "done":
                        update_music_track(wb, music_id, status="done")
                    log(f"[MUSIC] {project_dir.name}: skip {music_id}, da co mp3")
                    music_progress(done, len(pending))
                    music_track(music_id, "done", "already exists")
                    continue

                update_music_track(wb, music_id, status="generating")
                log(f"[MUSIC {idx}/{len(pending)}] {project_dir.name}: {music_id} - {title}")
                music_track(music_id, "generating")

                ok = False
                result_text = ""
                for attempt in range(1, 4):
                    try:
                        ok, result_text = worker.generate_and_download(
                            prompt=prompt,
                            output_path=out_mp3,
                            timeout=420,
                            pick="best",
                        )
                    except Exception as e:
                        ok = False
                        result_text = str(e)

                    if ok:
                        break

                    result_text = str(result_text or "")
                    if attempt < 3:
                        log(
                            f"[MUSIC] {project_dir.name}: track {music_id} fail ({result_text[:80]}), "
                            f"restart browser & retry (lan {attempt}/3)",
                            "WARN",
                        )
                        try:
                            try:
                                tm.stop()
                            except Exception:
                                pass
                            if chrome_proc and chrome_proc.poll() is None:
                                kill_pid_tree(chrome_proc.pid)
                                time.sleep(3)
                            time.sleep(2)

                            if chrome_exe.exists():
                                chrome_proc = restart_suno_browser(chrome_exe, window_size, window_position)
                                time.sleep(3)

                            if not tm.start() or not getattr(tm, "_page", None):
                                result_text = f"{result_text} | reconnect failed"
                                break
                            worker = BrowserSunoWorker(tm._page)
                            time.sleep(2)
                            log(f"[MUSIC] Browser restarted, retry track {music_id}...")
                        except Exception as e:
                            result_text = f"{result_text} | restart failed: {e}"
                            break
                        continue
                    break

                if ok:
                    done += 1
                    update_music_track(wb, music_id, status="done", suno_url=result_text)
                    log(f"[MUSIC OK] {project_dir.name}: {music_id} -> {out_mp3.name}", "SUCCESS")
                    music_track(music_id, "done", str(result_text))
                else:
                    update_music_track(wb, music_id, status="error")
                    log(f"[MUSIC FAIL] {project_dir.name}: {music_id}: {result_text}", "ERROR")
                    music_track(music_id, "error", str(result_text))

                music_progress(done, len(pending))
                time.sleep(8)

        result["done"] = done
        result["success"] = True
        log(f"[MUSIC] {project_dir.name}: hoan tat ({done}/{len(pending)})", "SUCCESS")

    except Exception as e:
        import traceback
        log(f"[MUSIC] {project_dir.name}: error {e}", "ERROR")
        log(f"[MUSIC] {traceback.format_exc()}", "ERROR")
    finally:
        if chrome_proc and chrome_proc.poll() is None:
            kill_pid_tree(chrome_proc.pid)
        if lock_owner_pid is not None:
            _release_lock(MUSIC_GLOBAL_LOCK, lock_owner_pid)

    sys.stdout.write(f"@@RESULT|{json.dumps(result)}\n")
    sys.stdout.flush()
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
