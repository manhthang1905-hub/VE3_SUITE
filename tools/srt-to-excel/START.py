#!/usr/bin/env python3
"""
SRT to Excel Tool - Launcher
=============================
Tự động cài thư viện và mở GUI.

Usage:
    python START.py
"""

import subprocess
import sys
import os

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))


def check_requirements():
    """Kiểm tra thư viện cần thiết."""
    required = {
        'yaml': 'pyyaml',
        'openpyxl': 'openpyxl',
        'PIL': 'pillow',
        'requests': 'requests',
    }
    missing = []
    for mod, pkg in required.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    return missing


def install_requirements():
    """Cài thư viện từ requirements.txt."""
    req_file = os.path.join(TOOL_DIR, "requirements.txt")
    print("=" * 52)
    print("  Dang cai thu vien...")
    print("=" * 52)
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", req_file],
        cwd=TOOL_DIR
    )
    return result.returncode == 0


def run_gui():
    """Chạy GUI chính."""
    gui_file = os.path.join(TOOL_DIR, "gui.py")
    if not os.path.exists(gui_file):
        print("[ERROR] Khong tim thay gui.py!")
        return
    print("\n  Dang mo GUI...\n")
    subprocess.run([sys.executable, gui_file], cwd=TOOL_DIR)


def main():
    print("""
  ==========================================
        SRT -> EXCEL TOOL
     Tao prompts tu file SRT
  ==========================================
""")
    missing = check_requirements()
    if missing:
        print(f"  [INFO] Thieu thu vien: {', '.join(missing)}")
        if not install_requirements():
            print("\n  [ERROR] Khong the cai thu vien!")
            print("  Thu chay: pip install -r requirements.txt")
            input("\n  Nhan Enter de thoat...")
            return
    else:
        print("  [OK] Du thu vien.\n")
    run_gui()


if __name__ == "__main__":
    main()
