"""
Process killer - thay thế taskkill.exe để tránh lỗi 0xc0000142.

v1.0.386: taskkill.exe hay crash hiện dialog box block tool.
Dùng Python native (os.kill/ctypes/psutil) thay thế.
"""

import os
import signal
import sys


def kill_pid(pid):
    """Kill 1 process bằng PID - không dùng taskkill.exe.

    Windows: os.kill(SIGTERM) gọi TerminateProcess (force kill).
    Fallback: ctypes TerminateProcess nếu os.kill không đủ quyền.
    """
    try:
        pid_int = int(pid)
        os.kill(pid_int, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        # Process không tồn tại hoặc access denied
        if sys.platform == "win32":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                PROCESS_TERMINATE = 0x0001
                handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
                if handle:
                    kernel32.TerminateProcess(handle, 1)
                    kernel32.CloseHandle(handle)
            except Exception:
                pass
    except (ValueError, TypeError):
        pass  # PID không hợp lệ


def kill_pid_tree(pid):
    """Kill process VÀ tất cả child processes.

    QUAN TRỌNG: Phải kill cả tree, nếu chỉ kill parent → child thành orphan.
    """
    try:
        import psutil
        parent = psutil.Process(int(pid))
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            parent.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    except ImportError:
        # Không có psutil → dùng taskkill /T /F nhưng suppress dialog
        if sys.platform == "win32":
            import subprocess
            try:
                subprocess.run(
                    f'taskkill /PID {int(pid)} /T /F',
                    shell=True, capture_output=True, timeout=10,
                    creationflags=0x08000000  # CREATE_NO_WINDOW - suppress dialog
                )
            except Exception:
                kill_pid(pid)  # Last resort
        else:
            kill_pid(pid)
    except Exception:
        kill_pid(pid)


def kill_all_by_name(process_name):
    """Kill tất cả processes theo tên (ví dụ 'chrome.exe')."""
    try:
        import psutil
        for proc in psutil.process_iter(['pid', 'name']):
            if proc.info['name'] and process_name.lower() in proc.info['name'].lower():
                try:
                    proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
    except ImportError:
        # Fallback: dùng taskkill nhưng suppress dialog
        if sys.platform == "win32":
            import subprocess
            try:
                subprocess.run(
                    ['taskkill', '/F', '/IM', process_name],
                    capture_output=True, timeout=10,
                    creationflags=0x08000000  # CREATE_NO_WINDOW
                )
            except Exception:
                pass
