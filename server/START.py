#!/usr/bin/env python3
"""
VE3 Tool - Khoi dong
====================
Chay file nay de:
1. Tu dong cai thu vien can thiet
2. Setup SMB share (ket noi Z: den may chu)
3. Mo GUI

Usage:
    python START.py
"""

import subprocess
import sys
import os

# Thu muc chua tool
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))

# ================================================================================
# SMB SHARE CONFIG - Thay doi neu can
# ================================================================================
SMB_SERVER_IP = "192.168.88.14"
SMB_SHARE_NAME = "D"
SMB_USERNAME = "smbuser"
SMB_PASSWORD = "159753"
SMB_DRIVE_LETTER = "Z:"

def install_requirements():
    """Cai thu vien tu requirements.txt"""
    req_file = os.path.join(TOOL_DIR, "requirements.txt")

    if not os.path.exists(req_file):
        print("[ERROR] Khong tim thay requirements.txt!")
        return False

    print("=" * 50)
    print("DANG CAI THU VIEN...")
    print("=" * 50)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", req_file],
            cwd=TOOL_DIR
        )

        if result.returncode == 0:
            print("\n[OK] Cai thu vien thanh cong!")
            return True
        else:
            print("\n[ERROR] Loi khi cai thu vien!")
            return False

    except Exception as e:
        print(f"\n[ERROR] {e}")
        return False

def check_requirements():
    """Kiem tra xem da cai du thu vien chua"""
    required = ['yaml', 'openpyxl', 'PIL', 'requests']
    missing = []

    for mod in required:
        try:
            if mod == 'yaml':
                import yaml
            elif mod == 'openpyxl':
                import openpyxl
            elif mod == 'PIL':
                from PIL import Image
            elif mod == 'requests':
                import requests
        except ImportError:
            missing.append(mod)

    return len(missing) == 0


def setup_smb_share():
    """
    Setup SMB share - ket noi Z: den may chu.
    Giup copy du lieu on dinh hon so voi tsclient.
    """
    print("\n" + "=" * 50)
    print("SETUP SMB SHARE - KET NOI O MANG")
    print("=" * 50)

    # Kiem tra xem Z: da ket noi chua
    auto_path = f"{SMB_DRIVE_LETTER}\\AUTO"
    if os.path.exists(auto_path):
        print(f"[OK] {SMB_DRIVE_LETTER} da ket noi - {auto_path} san sang")
        return True

    print(f"[INFO] Dang ket noi {SMB_DRIVE_LETTER} den \\\\{SMB_SERVER_IP}\\{SMB_SHARE_NAME}...")

    try:
        # Xoa mapping cu neu co
        subprocess.run(
            ['net', 'use', SMB_DRIVE_LETTER, '/delete', '/y'],
            capture_output=True, text=True
        )

        # Tao mapping moi
        cmd = [
            'net', 'use', SMB_DRIVE_LETTER,
            f'\\\\{SMB_SERVER_IP}\\{SMB_SHARE_NAME}',
            f'/user:{SMB_USERNAME}', SMB_PASSWORD,
            '/persistent:yes'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            # Kiem tra lai
            if os.path.exists(auto_path):
                print(f"[OK] Da ket noi {SMB_DRIVE_LETTER} thanh cong!")
                print(f"[OK] {auto_path} san sang su dung")
                return True
            else:
                print(f"[WARN] Da ket noi {SMB_DRIVE_LETTER} nhung khong tim thay thu muc AUTO")
                print(f"       Kiem tra lai may chu {SMB_SERVER_IP}")
                return True  # Van tiep tuc chay
        else:
            error = result.stderr or result.stdout or "Loi khong xac dinh"
            print(f"[WARN] Khong the ket noi {SMB_DRIVE_LETTER}: {error.strip()}")
            print(f"       Co the may chu {SMB_SERVER_IP} chua san sang")
            print(f"       Tool van hoat dong voi tsclient hoac duong dan khac")
            return False

    except Exception as e:
        print(f"[WARN] Loi setup SMB: {e}")
        print(f"       Tool van hoat dong voi tsclient hoac duong dan khac")
        return False

def run_gui():
    """Chay GUI"""
    gui_file = os.path.join(TOOL_DIR, "vm_manager_gui.py")

    if not os.path.exists(gui_file):
        print("[ERROR] Khong tim thay vm_manager_gui.py!")
        return

    print("\n" + "=" * 50)
    print("DANG MO GUI...")
    print("=" * 50)

    # Chay GUI
    subprocess.run([sys.executable, gui_file], cwd=TOOL_DIR)

def main():
    print("""
    ╔═══════════════════════════════════════╗
    ║         VE3 TOOL - KHOI DONG          ║
    ╚═══════════════════════════════════════╝
    """)

    # Buoc 1: Kiem tra va cai thu vien
    if not check_requirements():
        print("[INFO] Chua cai du thu vien, dang cai...")
        if not install_requirements():
            print("\n[ERROR] Khong the cai thu vien!")
            print("Thu chay thu cong: pip install -r requirements.txt")
            input("\nNhan Enter de thoat...")
            return
    else:
        print("[OK] Da cai du thu vien")

    # Buoc 2: Setup SMB share (ket noi Z: den may chu)
    # Khong can thiet phai thanh cong - tool van chay duoc voi tsclient
    setup_smb_share()

    # Buoc 3: Chay GUI
    run_gui()

if __name__ == "__main__":
    main()
