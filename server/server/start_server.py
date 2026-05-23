"""
Server Launcher - Khoi dong Main Server + N Chrome Workers.

Moi Chrome chay trong 1 CMD rieng → setup song song.

Usage:
    python server/start_server.py           # Auto-detect so Chrome
    python server/start_server.py --workers 3  # Chi dinh so workers
"""
import sys
import os
import time
import subprocess
import argparse
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

TOOL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(TOOL_DIR))


def count_available_chromes() -> int:
    """Dem so Chrome Portable co san."""
    from server.chrome_pool import CHROME_FOLDERS
    count = 0
    for folder in CHROME_FOLDERS:
        chrome_exe = TOOL_DIR / folder / "GoogleChromePortable.exe"
        if chrome_exe.exists():
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description="Server Launcher")
    parser.add_argument("--workers", type=int, default=0, help="So workers (0 = auto-detect)")
    parser.add_argument("--port", type=int, default=5000, help="Server port")
    args = parser.parse_args()

    num_workers = args.workers or count_available_chromes()

    print("=" * 60)
    print("  SERVER LAUNCHER - Multi-Process Architecture")
    print("=" * 60)
    print()
    print(f"  Chrome Portables: {num_workers}")
    print(f"  Server port: {args.port}")
    print()

    if num_workers == 0:
        print("[ERROR] Khong tim thay Chrome Portable nao!")
        return

    python_exe = sys.executable

    # 1. Khoi dong Main Server trong CMD rieng
    print("[1/2] Khoi dong Main Server...")
    server_cmd = f'start "SERVER - Main" cmd /k "{python_exe}" -u server/app.py --port {args.port}'
    subprocess.Popen(server_cmd, shell=True, cwd=str(TOOL_DIR))
    print(f"  → Main Server started (port {args.port})")

    # Doi server khoi dong
    print("  Doi server san sang (3s)...")
    time.sleep(3)

    # 2. Khoi dong N Workers trong N CMD rieng (SONG SONG)
    print(f"[2/2] Khoi dong {num_workers} Chrome Workers...")
    for i in range(num_workers):
        worker_cmd = f'start "WORKER-{i}" cmd /k "{python_exe}" -u server/worker.py --index {i}'
        subprocess.Popen(worker_cmd, shell=True, cwd=str(TOOL_DIR))
        print(f"  → Worker-{i} started")
        time.sleep(0.5)  # Gian cach nho de tranh trung port

    print()
    print("=" * 60)
    print(f"  Da khoi dong: 1 Server + {num_workers} Workers")
    print(f"  Moi Worker setup Chrome SONG SONG trong CMD rieng")
    print(f"  Server: http://0.0.0.0:{args.port}")
    print(f"  Server SAN SANG nhan request NGAY (khong doi Chrome)")
    print("=" * 60)


if __name__ == '__main__':
    main()
