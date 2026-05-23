"""
Server GUI - Giao dien cai dat va quan ly Chrome Server.

Chay: python server/server_gui.py
  hoac: START_SERVER.bat

Flow:
1. Hien giao dien cai dat (IPv6, so Chrome)
2. Bam START → khoi dong Flask + Chrome workers
3. Hien trang thai workers, queue, logs
"""
import sys
import os
import json
import threading
import time
import subprocess
import ctypes
import tkinter as tk
from tkinter import ttk
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

TOOL_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(TOOL_DIR))

# Colors (dark theme)
BG = '#0f172a'
BG2 = '#1e293b'
FG = '#e2e8f0'
FG2 = '#94a3b8'
BLUE = '#38bdf8'
GREEN = '#22c55e'
ORANGE = '#f97316'
RED = '#ef4444'
YELLOW = '#eab308'
BORDER = '#334155'


class ServerGUI(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("Chrome Server")
        self.geometry("700x850")
        self.configure(bg=BG)
        self.resizable(True, True)
        self._apply_gui_layout()

        self._server_started = False
        self._logs = []
        self._workers_info = []
        self._settings_file = TOOL_DIR / "config" / "server_gui.json"
        self._is_closing = False

        # Dong GUI -> cleanup Chrome/CMD do tool tao
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_setup_page()
        self._load_settings()

    def _apply_gui_layout(self):
        """Dat GUI ben trai man hinh, danh vung ben phai cho Chrome."""
        try:
            sw = int(self.winfo_screenwidth())
            sh = int(self.winfo_screenheight())
            # Lay work area de tranh bi taskbar che.
            work_left, work_top, work_right, work_bottom = 0, 0, sw, sh
            try:
                class _RECT(ctypes.Structure):
                    _fields_ = [
                        ("left", ctypes.c_long),
                        ("top", ctypes.c_long),
                        ("right", ctypes.c_long),
                        ("bottom", ctypes.c_long),
                    ]
                rect = _RECT()
                SPI_GETWORKAREA = 0x0030
                ok = ctypes.windll.user32.SystemParametersInfoW(
                    SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
                )
                if ok:
                    work_left, work_top, work_right, work_bottom = (
                        int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
                    )
            except Exception:
                pass

            work_w = max(800, work_right - work_left)
            work_h = max(700, work_bottom - work_top)
            # Tru them mot khoang an toan de tranh bi che taskbar/khung cua so.
            safe_bottom_gap = 18
            gui_h = max(700, work_h - safe_bottom_gap)
            if sw >= 3800:
                reserve_left = int(os.getenv("CHROME_LAYOUT_LEFT_RESERVED", "1920"))
            elif sw >= 2500:
                reserve_left = int(os.getenv("CHROME_LAYOUT_LEFT_RESERVED", "960"))
            else:
                reserve_left = int(os.getenv("CHROME_LAYOUT_LEFT_RESERVED", "760"))
            reserve_left = max(560, min(reserve_left, work_w - 300))
            self.geometry(f"{reserve_left}x{gui_h}+{work_left}+{work_top}")
            self.minsize(700, 850)
        except Exception:
            pass

    # ============================================================
    # Shutdown / Cleanup
    # ============================================================
    def _close_tool_cmd_windows(self):
        """Dong cac cua so CMD do tool tao (khong dong CMD khac)."""
        if os.name != 'nt':
            return
        try:
            user32 = ctypes.windll.user32
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            WM_CLOSE = 0x0010
            titles = ("WORKER-", "SERVER - Main", "ChromeWorker-")

            def _enum_cb(hwnd, lparam):
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = (buff.value or "").strip()
                if title and any(key in title for key in titles):
                    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                return True

            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        except Exception:
            pass

    def _kill_portable_chrome_processes(self):
        """Kill Chrome Portable processes con sot (khong kill toan bo Chrome ca may)."""
        try:
            from modules.process_killer import kill_pid, kill_all_by_name
        except Exception:
            return

        # Kill launcher GoogleChromePortable.exe (chi tool nay dung)
        try:
            kill_all_by_name("GoogleChromePortable.exe")
        except Exception:
            pass

    def _kill_tool_python_processes(self):
        """Kill python process do tool nay tao (worker/server), tranh sot CMD."""
        if os.name != 'nt':
            return
        try:
            from modules.process_killer import kill_pid
        except Exception:
            return

        try:
            proc = subprocess.run(
                ['wmic', 'process', 'where', 'name="python.exe" or name="pythonw.exe"', 'get', 'ProcessId,CommandLine', '/FORMAT:CSV'],
                capture_output=True,
                text=True,
                timeout=8,
            )
            out = proc.stdout or ""
            markers = (
                "server/worker.py",
                "server\\worker.py",
                "server/app.py",
                "server\\app.py",
                "START_SERVER.bat",
            )
            for line in out.splitlines():
                s = line.strip()
                if not s:
                    continue
                cmd_lower = s.lower()
                if not any(m.lower() in cmd_lower for m in markers):
                    continue
                parts = s.rsplit(',', 1)
                if len(parts) != 2:
                    continue
                try:
                    pid = int(parts[1].strip())
                    kill_pid(pid)
                except Exception:
                    continue
        except Exception:
            pass

    def _kill_tool_cmd_processes(self):
        """Kill cmd.exe do tool tao (launcher/worker wrappers)."""
        if os.name != 'nt':
            return
        try:
            from modules.process_killer import kill_pid
        except Exception:
            return

        try:
            proc = subprocess.run(
                ['wmic', 'process', 'where', 'name="cmd.exe"', 'get', 'ProcessId,CommandLine', '/FORMAT:CSV'],
                capture_output=True,
                text=True,
                timeout=8,
            )
            out = proc.stdout or ""
            markers = (
                "start_server.bat",
                "server\\worker.py",
                "server/worker.py",
                "run_worker.py",
                "server\\start_server.py",
                "server/start_server.py",
            )
            for line in out.splitlines():
                s = line.strip()
                if not s:
                    continue
                cmd_lower = s.lower()
                if not any(m in cmd_lower for m in markers):
                    continue
                parts = s.rsplit(',', 1)
                if len(parts) != 2:
                    continue
                try:
                    pid = int(parts[1].strip())
                    kill_pid(pid)
                except Exception:
                    continue
        except Exception:
            pass

        if os.name != 'nt':
            return

        # v1.0.684: Kill ALL chrome.exe co GoogleChromePortable trong command line
        # Truoc: kill_pid() tung PID → chi kill parent, child thanh orphan
        # Sau: WMIC terminate tat ca cung luc + taskkill /F /T tree kill
        try:
            # Cach 1: WMIC terminate tat ca matching processes cung luc (nhanh nhat)
            subprocess.run(
                ['wmic', 'process', 'where',
                 "name='chrome.exe' and CommandLine like '%GoogleChromePortable%'",
                 'call', 'terminate'],
                capture_output=True, text=True, timeout=10,
                creationflags=0x08000000  # CREATE_NO_WINDOW
            )
        except Exception:
            pass

        # Cach 2: Backup - tim PID va kill tree (phong khi WMIC terminate khong du)
        try:
            proc = subprocess.run(
                ['wmic', 'process', 'where',
                 "name='chrome.exe' and CommandLine like '%GoogleChromePortable%'",
                 'get', 'ProcessId', '/FORMAT:CSV'],
                capture_output=True, text=True, timeout=8,
                creationflags=0x08000000
            )
            for line in (proc.stdout or "").splitlines():
                s = line.strip()
                if not s or not any(c.isdigit() for c in s):
                    continue
                parts = s.rsplit(',', 1)
                if len(parts) != 2:
                    continue
                try:
                    pid = int(parts[1].strip())
                    # taskkill /F /T = force kill + kill ca tree (children)
                    subprocess.run(
                        ['taskkill', '/F', '/T', '/PID', str(pid)],
                        capture_output=True, timeout=5,
                        creationflags=0x08000000
                    )
                except Exception:
                    continue
        except Exception:
            pass

    def _on_close(self):
        """Khi dong GUI: stop workers + dong Chrome/CMD.
        v1.0.685: Kill Chrome TRUOC, cleanup sau, os._exit(0) LUON chay.
        """
        if self._is_closing:
            return
        self._is_closing = True

        print("[GUI] _on_close v1.0.685: bat dau shutdown...")

        # === BUOC 1: FORCE KILL CHROME NGAY LAP TUC (khong doi page.quit) ===
        if os.name == 'nt':
            print("[GUI] Force kill ALL GoogleChromePortable chrome.exe...")
            try:
                subprocess.run(
                    ['wmic', 'process', 'where',
                     "name='chrome.exe' and CommandLine like '%GoogleChromePortable%'",
                     'call', 'terminate'],
                    capture_output=True, text=True, timeout=10,
                    creationflags=0x08000000
                )
            except Exception as e:
                print(f"[GUI] WMIC terminate error: {e}")

            # Backup: taskkill tree kill
            try:
                proc = subprocess.run(
                    ['wmic', 'process', 'where',
                     "name='chrome.exe' and CommandLine like '%GoogleChromePortable%'",
                     'get', 'ProcessId', '/FORMAT:CSV'],
                    capture_output=True, text=True, timeout=8,
                    creationflags=0x08000000
                )
                for line in (proc.stdout or "").splitlines():
                    s = line.strip()
                    if not s or not any(c.isdigit() for c in s):
                        continue
                    parts = s.rsplit(',', 1)
                    if len(parts) == 2:
                        try:
                            pid = int(parts[1].strip())
                            subprocess.run(
                                ['taskkill', '/F', '/T', '/PID', str(pid)],
                                capture_output=True, timeout=5,
                                creationflags=0x08000000
                            )
                        except Exception:
                            pass
            except Exception:
                pass

            # Kill GoogleChromePortable.exe launcher
            try:
                subprocess.run(
                    ['taskkill', '/F', '/IM', 'GoogleChromePortable.exe'],
                    capture_output=True, timeout=5,
                    creationflags=0x08000000
                )
            except Exception:
                pass

            print("[GUI] Chrome kill done.")

        # === BUOC 2: Cleanup nhanh (server state, CMD windows) ===
        try:
            from server.app import chrome_pool, server_settings, settings_lock
            if chrome_pool:
                # Chi set state, KHONG goi close_all (Chrome da bi kill roi)
                for w in chrome_pool.workers:
                    w.ready = False
                    w.busy = False
                    w.session = None
            with settings_lock:
                server_settings['started'] = False
        except Exception:
            pass

        self._close_tool_cmd_windows()
        self._kill_tool_python_processes()

        print("[GUI] Cleanup done. Exiting...")

        # === BUOC 3: THOAT NGAY ===
        try:
            self.destroy()
        except Exception:
            pass
        os._exit(0)

    # ============================================================
    # Setup Page
    # ============================================================
    def _build_setup_page(self):
        self.setup_frame = tk.Frame(self, bg=BG)
        self.setup_frame.pack(fill='both', expand=True)

        # Title
        tk.Label(self.setup_frame, text="Chrome Server", font=("Segoe UI", 20, "bold"),
                 bg=BG, fg=BLUE).pack(pady=(15, 3))
        tk.Label(self.setup_frame, text="Cai dat va khoi dong server", font=("Segoe UI", 10),
                 bg=BG, fg=FG2).pack(pady=(0, 15))

        # Settings card
        card = tk.Frame(self.setup_frame, bg=BG2, highlightbackground=BORDER, highlightthickness=1)
        card.pack(padx=80, fill='x')

        # ============ CHE DO IPv6 ============
        row_mode = tk.Frame(card, bg=BG2)
        row_mode.pack(fill='x', padx=20, pady=(20, 5))

        tk.Label(row_mode, text="Che do IPv6", font=("Segoe UI", 13, "bold"),
                 bg=BG2, fg=FG).pack(side='left')

        # v1.0.607: Unified IPv6 mode selector
        self.ipv6_mode_var = tk.StringVar(value="pool")
        mode_options = ["Tat", "IPv6 Pool", "IPv6 Thu cong", "Webshare", "ProxyXoay"]
        self.ipv6_mode_combo = ttk.Combobox(row_mode, textvariable=self.ipv6_mode_var,
                                             values=mode_options, state='readonly', width=14)
        self.ipv6_mode_combo.set("IPv6 Pool")
        self.ipv6_mode_combo.pack(side='right')
        self.ipv6_mode_combo.bind("<<ComboboxSelected>>", self._on_ipv6_mode_changed)

        # Mode description
        self.mode_desc = tk.Label(card, text="", font=("Segoe UI", 9), bg=BG2, fg=FG2)
        self.mode_desc.pack(padx=20, anchor='w', pady=(0, 5))

        # --- IPv6 Pool frame (hien khi chon "IPv6 Pool") ---
        self.pool_frame = tk.Frame(card, bg=BG2)

        pool_row = tk.Frame(self.pool_frame, bg=BG2)
        pool_row.pack(fill='x', pady=(0, 2))
        tk.Label(pool_row, text="Pool API URL:", font=("Segoe UI", 10, "bold"),
                 bg=BG2, fg=FG).pack(side='left')
        self.pool_api_var = tk.StringVar(value="")
        pool_entry = tk.Entry(pool_row, textvariable=self.pool_api_var, width=35,
                              font=("Consolas", 10), bg='#0f172a', fg=FG,
                              insertbackground=FG, relief='solid', bd=1)
        pool_entry.pack(side='left', padx=(8, 0), fill='x', expand=True)

        pool_hint = tk.Label(self.pool_frame, text="VD: http://192.168.88.1:8765",
                             font=("Segoe UI", 8), bg=BG2, fg=FG2)
        pool_hint.pack(anchor='w', pady=(0, 2))

        # Pool test button
        pool_btn_row = tk.Frame(self.pool_frame, bg=BG2)
        pool_btn_row.pack(fill='x', pady=(2, 5))
        self.pool_test_btn = tk.Button(pool_btn_row, text="TEST KET NOI",
                                        font=("Segoe UI", 9, "bold"),
                                        bg=ORANGE, fg='#0f172a', relief='flat', cursor='hand2',
                                        command=self._test_pool)
        self.pool_test_btn.pack(side='left')
        self.pool_status_label = tk.Label(pool_btn_row, text="", font=("Segoe UI", 9),
                                           bg=BG2, fg=FG2)
        self.pool_status_label.pack(side='left', padx=10)

        # --- IPv6 Thu cong frame (hien khi chon "IPv6 Thu cong") ---
        self.manual_ipv6_frame = tk.Frame(card, bg=BG2)

        tk.Label(self.manual_ipv6_frame, text="IPv6 (moi dong 1 IP):",
                 font=("Segoe UI", 9), bg=BG2, fg=FG2).pack(anchor='w', pady=(0, 2))

        self.ipv6_text = tk.Text(self.manual_ipv6_frame, height=3, width=50,
                                  font=("Consolas", 9), bg='#0f172a', fg=FG,
                                  insertbackground=FG, relief='solid', bd=1,
                                  highlightbackground=BORDER)
        self.ipv6_text.pack(fill='x')

        ipv6_btn_frame = tk.Frame(self.manual_ipv6_frame, bg=BG2)
        ipv6_btn_frame.pack(fill='x', pady=(4, 5))

        self.ipv6_add_btn = tk.Button(ipv6_btn_frame, text="THEM VAO MAY",
                                       font=("Segoe UI", 9, "bold"),
                                       bg=BLUE, fg='#0f172a', relief='flat', cursor='hand2',
                                       command=self._add_ipv6_to_machine)
        self.ipv6_add_btn.pack(side='left', padx=(0, 8))

        self.ipv6_test_btn = tk.Button(ipv6_btn_frame, text="TEST IPv6",
                                        font=("Segoe UI", 9, "bold"),
                                        bg=ORANGE, fg='#0f172a', relief='flat', cursor='hand2',
                                        command=self._test_ipv6)
        self.ipv6_test_btn.pack(side='left')

        self.ipv6_status_label = tk.Label(ipv6_btn_frame, text="", font=("Segoe UI", 9),
                                           bg=BG2, fg=FG2)
        self.ipv6_status_label.pack(side='left', padx=10)

        # --- Webshare frame (hien khi chon "Webshare") ---
        self.ws_frame = tk.Frame(card, bg=BG2)

        ws_row1 = tk.Frame(self.ws_frame, bg=BG2)
        ws_row1.pack(fill='x', pady=2)
        tk.Label(ws_row1, text="Username:", font=("Segoe UI", 9), bg=BG2, fg=FG2, width=10, anchor='w').pack(side='left')
        self.ws_username_var = tk.StringVar(value="")
        tk.Entry(ws_row1, textvariable=self.ws_username_var, font=("Consolas", 9),
                 bg='#0f172a', fg=FG, insertbackground=FG, relief='solid', bd=1).pack(side='left', fill='x', expand=True)

        ws_row2 = tk.Frame(self.ws_frame, bg=BG2)
        ws_row2.pack(fill='x', pady=2)
        tk.Label(ws_row2, text="Password:", font=("Segoe UI", 9), bg=BG2, fg=FG2, width=10, anchor='w').pack(side='left')
        self.ws_password_var = tk.StringVar(value="")
        tk.Entry(ws_row2, textvariable=self.ws_password_var, font=("Consolas", 9),
                 bg='#0f172a', fg=FG, insertbackground=FG, relief='solid', bd=1).pack(side='left', fill='x', expand=True)

        ws_row3 = tk.Frame(self.ws_frame, bg=BG2)
        ws_row3.pack(fill='x', pady=2)
        tk.Label(ws_row3, text="Machine ID:", font=("Segoe UI", 9), bg=BG2, fg=FG2, width=10, anchor='w').pack(side='left')
        self.ws_machine_var = tk.StringVar(value="1")
        tk.Entry(ws_row3, textvariable=self.ws_machine_var, font=("Consolas", 9),
                 bg='#0f172a', fg=FG, insertbackground=FG, relief='solid', bd=1, width=5).pack(side='left')

        self.ws_test_btn = tk.Button(ws_row3, text="TEST", font=("Segoe UI", 9, "bold"),
                                      bg=ORANGE, fg='#0f172a', relief='flat', cursor='hand2',
                                      command=self._test_webshare)
        self.ws_test_btn.pack(side='right')

        # --- ProxyXoay frame (hien khi chon "ProxyXoay") ---
        self.px_frame = tk.Frame(card, bg=BG2)

        tk.Label(self.px_frame, text="API Keys (moi dong 1 key, moi Chrome can 1 key rieng):",
                 font=("Segoe UI", 9), bg=BG2, fg=FG2).pack(anchor='w', pady=(0, 2))

        self.px_keys_text = tk.Text(self.px_frame, height=3, width=50,
                                     font=("Consolas", 9), bg='#0f172a', fg=FG,
                                     insertbackground=FG, relief='solid', bd=1,
                                     highlightbackground=BORDER)
        self.px_keys_text.pack(fill='x')

        px_opt_row = tk.Frame(self.px_frame, bg=BG2)
        px_opt_row.pack(fill='x', pady=(4, 2))
        tk.Label(px_opt_row, text="Proxy type:", font=("Segoe UI", 9),
                 bg=BG2, fg=FG2).pack(side='left')
        self.px_type_var = tk.StringVar(value="socks5")
        ttk.Combobox(px_opt_row, textvariable=self.px_type_var,
                      values=["socks5", "http"], state='readonly', width=8).pack(side='left', padx=(4, 0))

        self.px_key_count_label = tk.Label(px_opt_row, text="", font=("Segoe UI", 9),
                                            bg=BG2, fg=FG2)
        self.px_key_count_label.pack(side='right')

        px_btn_row = tk.Frame(self.px_frame, bg=BG2)
        px_btn_row.pack(fill='x', pady=(2, 5))
        self.px_test_btn = tk.Button(px_btn_row, text="TEST",
                                      font=("Segoe UI", 9, "bold"),
                                      bg=ORANGE, fg='#0f172a', relief='flat', cursor='hand2',
                                      command=self._test_proxyxoay)
        self.px_test_btn.pack(side='left')
        self.px_status_label = tk.Label(px_btn_row, text="", font=("Segoe UI", 9),
                                         bg=BG2, fg=FG2)
        self.px_status_label.pack(side='left', padx=10)

        # Backward compat vars
        self.ipv6_var = tk.BooleanVar(value=True)
        self.proxy_type_var = tk.StringVar(value="none")

        # Show correct sub-frame
        self._on_ipv6_mode_changed()

        # Separator
        tk.Frame(card, bg=BORDER, height=1).pack(fill='x', padx=20, pady=8)

        # Chrome count
        row2 = tk.Frame(card, bg=BG2)
        row2.pack(fill='x', padx=20, pady=(0, 10))

        tk.Label(row2, text="So luong Chrome", font=("Segoe UI", 13, "bold"),
                 bg=BG2, fg=FG).pack(side='left')

        self.chrome_var = tk.StringVar(value="0")
        chrome_options = ["Tat ca", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
        self.chrome_combo = ttk.Combobox(row2, textvariable=self.chrome_var,
                                         values=chrome_options, state='readonly', width=10)
        self.chrome_combo.set("Tat ca")
        self.chrome_combo.pack(side='right')

        tk.Label(card, text="Chon so Chrome workers chay song song.",
                 font=("Segoe UI", 9), bg=BG2, fg=FG2).pack(padx=20, anchor='w', pady=(0, 10))

        # Separator
        tk.Frame(card, bg=BORDER, height=1).pack(fill='x', padx=20, pady=5)

        # Accounts input
        tk.Label(card, text="Tai khoan Google", font=("Segoe UI", 13, "bold"),
                 bg=BG2, fg=FG).pack(padx=20, anchor='w', pady=(10, 0))
        tk.Label(card, text="Moi dong 1 tai khoan: email|password|2fa_secret",
                 font=("Segoe UI", 9), bg=BG2, fg=FG2).pack(padx=20, anchor='w', pady=(2, 4))

        acc_text_frame = tk.Frame(card, bg=BG2)
        acc_text_frame.pack(fill='x', padx=20, pady=(0, 20))

        self.accounts_text = tk.Text(acc_text_frame, height=3, width=50,
                                      font=("Consolas", 9), bg='#0f172a', fg=FG,
                                      insertbackground=FG, relief='solid', bd=1,
                                      highlightbackground=BORDER)
        self.accounts_text.pack(fill='x')

        # Buttons row: UPDATE + START
        btn_row = tk.Frame(self.setup_frame, bg=BG)
        btn_row.pack(fill='x', padx=80, pady=15)

        # UPDATE button
        self.update_btn = tk.Button(btn_row, text="UPDATE",
                                     font=("Segoe UI", 12, "bold"),
                                     bg='#0984e3', fg='white', activebackground='#0766b2',
                                     relief='flat', cursor='hand2', width=10,
                                     command=self._run_update)
        self.update_btn.pack(side='left', padx=(0, 10), ipady=8)

        # START button
        self.start_btn = tk.Button(btn_row, text="START SERVER",
                                   font=("Segoe UI", 16, "bold"),
                                   bg=GREEN, fg='#0f172a', activebackground='#16a34a',
                                   relief='flat', cursor='hand2', height=2,
                                   command=self._on_start)
        self.start_btn.pack(side='left', fill='x', expand=True)

        # Version
        version = "?"
        try:
            vf = TOOL_DIR / "VERSION.txt"
            if vf.exists():
                version = vf.read_text(encoding='utf-8').split('\n')[0].strip()
        except:
            pass
        tk.Label(self.setup_frame, text=f"v{version}", font=("Segoe UI", 9),
                 bg=BG, fg=FG2).pack(side='bottom', pady=10)

    def _on_ipv6_mode_changed(self, event=None):
        """v1.0.607: Show/hide sub-frames based on IPv6 mode selection."""
        mode = self.ipv6_mode_combo.get() if hasattr(self, 'ipv6_mode_combo') else "Tat"

        # Hide all sub-frames
        self.pool_frame.pack_forget()
        self.manual_ipv6_frame.pack_forget()
        self.ws_frame.pack_forget()
        self.px_frame.pack_forget()

        # Update backward compat vars
        if mode == "Tat":
            self.ipv6_var.set(False)
            self.proxy_type_var.set("none")
            self.mode_desc.config(text="Khong dung IPv6. Chrome dung IPv4 chung (de bi 403).")
        elif mode == "IPv6 Pool":
            self.ipv6_var.set(True)
            self.proxy_type_var.set("ipv6")
            self.mode_desc.config(text="Lay IPv6 tu MikroTik Pool API. Tu dong rotate khi 403.")
            self.pool_frame.pack(fill='x', padx=20, pady=(0, 5))
        elif mode == "IPv6 Thu cong":
            self.ipv6_var.set(True)
            self.proxy_type_var.set("ipv6")
            self.mode_desc.config(text="Nhap IPv6 thu cong. Moi Chrome 1 IPv6 rieng.")
            self.manual_ipv6_frame.pack(fill='x', padx=20, pady=(0, 5))
        elif mode == "Webshare":
            self.ipv6_var.set(False)
            self.proxy_type_var.set("webshare")
            self.mode_desc.config(text="Dung Webshare.io Rotating Residential Proxy.")
            self.ws_frame.pack(fill='x', padx=20, pady=(0, 5))
        elif mode == "ProxyXoay":
            self.ipv6_var.set(False)
            self.proxy_type_var.set("proxyxoay")
            self.mode_desc.config(text="Dung proxyxoay.shop (SOCKS5/HTTP). Moi Chrome can 1 key rieng.")
            self.px_frame.pack(fill='x', padx=20, pady=(0, 5))
            self._update_px_key_count()

    def _test_pool(self):
        """Test ket noi IPv6 Pool API."""
        url = self.pool_api_var.get().strip()
        if not url:
            self.pool_status_label.config(text="Chua nhap URL!", fg=RED)
            return

        self.pool_test_btn.config(state='disabled', text="DANG TEST...")
        self.pool_status_label.config(text="", fg=FG2)

        def _do_test():
            try:
                import urllib.request
                test_url = url.rstrip('/') + '/api/status'
                req = urllib.request.Request(test_url, method='GET')
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())
                    total = data.get('total', 0)
                    available = data.get('available', 0)
                    msg = f"OK! Pool: {available}/{total} IP san sang"
                    self.after(0, lambda: self.pool_status_label.config(text=msg, fg=GREEN))
            except Exception as e:
                self.after(0, lambda: self.pool_status_label.config(text=f"Loi: {e}", fg=RED))
            finally:
                self.after(0, lambda: self.pool_test_btn.config(state='normal', text="TEST KET NOI"))

        threading.Thread(target=_do_test, daemon=True).start()

    def _load_settings(self):
        """Load settings tu file JSON."""
        try:
            if self._settings_file.exists():
                data = json.loads(self._settings_file.read_text(encoding='utf-8'))
                # v1.0.607: Unified IPv6 mode
                if 'ipv6_mode' in data:
                    self.ipv6_mode_combo.set(data['ipv6_mode'])
                elif data.get('pool_api_url'):
                    # Migrate: co pool URL = dang dung pool
                    self.ipv6_mode_combo.set("IPv6 Pool")
                elif data.get('proxy_type') == 'webshare':
                    self.ipv6_mode_combo.set("Webshare")
                elif data.get('use_ipv6') and data.get('ipv6_list'):
                    self.ipv6_mode_combo.set("IPv6 Thu cong")
                elif not data.get('use_ipv6', True):
                    self.ipv6_mode_combo.set("Tat")
                # Pool API URL
                if data.get('pool_api_url'):
                    self.pool_api_var.set(data['pool_api_url'])
                # IPv6 list
                if data.get('ipv6_list'):
                    self.ipv6_text.delete("1.0", "end")
                    self.ipv6_text.insert("1.0", "\n".join(data['ipv6_list']))
                # Chrome count
                if 'chrome_count' in data:
                    val = data['chrome_count']
                    self.chrome_combo.set("Tat ca" if val == 0 else str(val))
                # Accounts
                if data.get('accounts_raw'):
                    self.accounts_text.delete("1.0", "end")
                    self.accounts_text.insert("1.0", "\n".join(data['accounts_raw']))
                # Webshare settings
                if data.get('ws_username'):
                    self.ws_username_var.set(data['ws_username'])
                if data.get('ws_password'):
                    self.ws_password_var.set(data['ws_password'])
                if data.get('ws_machine_id'):
                    self.ws_machine_var.set(str(data['ws_machine_id']))
                # ProxyXoay settings
                if data.get('px_keys'):
                    self.px_keys_text.delete("1.0", "end")
                    self.px_keys_text.insert("1.0", "\n".join(data['px_keys']))
                if data.get('px_type'):
                    self.px_type_var.set(data['px_type'])
                # Migrate: proxy_type=proxyxoay nhung chua co ipv6_mode
                if not data.get('ipv6_mode') and data.get('proxy_type') == 'proxyxoay':
                    self.ipv6_mode_combo.set("ProxyXoay")
                # Apply mode UI
                self._on_ipv6_mode_changed()
        except Exception:
            pass

    def _save_settings(self):
        """Luu settings ra file JSON."""
        try:
            self._settings_file.parent.mkdir(parents=True, exist_ok=True)
            data = {
                # v1.0.607: Unified mode
                'ipv6_mode': self.ipv6_mode_combo.get(),
                'use_ipv6': self.ipv6_var.get(),
                'ipv6_list': self._get_ipv6_list(),
                'chrome_count': self._get_chrome_count(),
                'accounts_raw': self._get_accounts_raw(),
                'proxy_type': self.proxy_type_var.get(),
                'ws_username': self.ws_username_var.get().strip(),
                'ws_password': self.ws_password_var.get().strip(),
                'ws_machine_id': int(self.ws_machine_var.get().strip() or '1'),
                'pool_api_url': self.pool_api_var.get().strip(),
                'px_keys': self._get_px_keys(),
                'px_type': self.px_type_var.get(),
            }
            self._settings_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
        except Exception:
            pass

    def _test_webshare(self):
        """v1.0.545: Test ket noi Webshare proxy."""
        import threading
        def _do_test():
            self.ws_test_btn.config(state='disabled', text="Testing...")
            try:
                from modules.proxy_providers.webshare_provider import WebshareProvider
                provider = WebshareProvider(
                    config={'webshare': {
                        'rotating_host': 'p.webshare.io',
                        'rotating_port': 80,
                        'rotating_username': self.ws_username_var.get().strip(),
                        'rotating_password': self.ws_password_var.get().strip(),
                        'machine_id': int(self.ws_machine_var.get().strip() or '1'),
                    }},
                    log_func=lambda msg, lvl="INFO": print(msg),
                )
                ok = provider.test_connectivity()
                if ok:
                    self.ws_test_btn.config(text="OK!", bg=GREEN)
                else:
                    self.ws_test_btn.config(text="FAIL!", bg=RED)
            except Exception as e:
                self.ws_test_btn.config(text=f"ERR", bg=RED)
                print(f"Webshare test error: {e}")
            finally:
                self.ws_test_btn.config(state='normal')
                self.root.after(3000, lambda: self.ws_test_btn.config(text="TEST", bg=ORANGE))
        threading.Thread(target=_do_test, daemon=True).start()

    def _get_px_keys(self):
        """Lay danh sach ProxyXoay API keys tu text box."""
        try:
            text = self.px_keys_text.get("1.0", "end").strip()
            return [line.strip() for line in text.split('\n') if line.strip()]
        except Exception:
            return []

    def _update_px_key_count(self):
        """Cap nhat so key hien thi."""
        try:
            keys = self._get_px_keys()
            chrome_count = self._get_chrome_count()
            count_text = f"{len(keys)} key"
            if chrome_count > 0 and len(keys) < chrome_count:
                count_text += f" (CAN {chrome_count} cho {chrome_count} Chrome!)"
                self.px_key_count_label.config(text=count_text, fg=RED)
            else:
                self.px_key_count_label.config(text=count_text, fg=GREEN)
        except Exception:
            pass

    def _test_proxyxoay(self):
        """Test ket noi ProxyXoay."""
        keys = self._get_px_keys()
        if not keys:
            self.px_status_label.config(text="Chua nhap API key!", fg=RED)
            return

        self.px_test_btn.config(state='disabled', text="Testing...")
        self.px_status_label.config(text="", fg=FG2)

        def _do_test():
            try:
                from modules.proxy_providers.proxyxoay_provider import ProxyXoayProvider
                provider = ProxyXoayProvider(
                    config={'proxyxoay': {
                        'api_keys': [keys[0]],
                        'proxy_type': self.px_type_var.get(),
                    }},
                    log_func=lambda msg, lvl="INFO": print(msg),
                )
                if provider.setup(worker_id=0):
                    ok = provider.test_connectivity()
                    if ok:
                        ip = provider.get_current_ip()
                        self.after(0, lambda: self.px_status_label.config(
                            text=f"OK! IP: {ip}", fg=GREEN))
                    else:
                        self.after(0, lambda: self.px_status_label.config(
                            text="Proxy loi ket noi!", fg=RED))
                    provider.stop()
                else:
                    self.after(0, lambda: self.px_status_label.config(
                        text="Setup fail (key sai hoac cooldown)", fg=RED))
            except Exception as e:
                self.after(0, lambda: self.px_status_label.config(
                    text=f"Loi: {e}", fg=RED))
            finally:
                self.after(0, lambda: self.px_test_btn.config(state='normal', text="TEST"))

        threading.Thread(target=_do_test, daemon=True).start()

    def _get_proxy_config(self) -> dict:
        """v1.0.545: Lay proxy provider config tu GUI."""
        ptype = self.proxy_type_var.get()
        config = {
            'proxy_provider': {
                'type': ptype,
                'webshare': {
                    'rotating_host': 'p.webshare.io',
                    'rotating_port': 80,
                    'rotating_username': self.ws_username_var.get().strip(),
                    'rotating_password': self.ws_password_var.get().strip(),
                    'machine_id': int(self.ws_machine_var.get().strip() or '1'),
                },
                'proxyxoay': {
                    'api_keys': self._get_px_keys(),
                    'proxy_type': self.px_type_var.get(),
                },
            }
        }
        return config

    def _get_ipv6_list(self):
        """Lay danh sach IPv6 tu text box."""
        text = self.ipv6_text.get("1.0", "end").strip()
        return [
            line.strip() for line in text.split('\n')
            if line.strip() and ':' in line.strip()
        ]

    def _get_accounts_raw(self):
        """Lay danh sach dong tai khoan (chua parse)."""
        text = self.accounts_text.get("1.0", "end").strip()
        return [
            line.strip() for line in text.split('\n')
            if line.strip() and '|' in line.strip()
        ]

    def _get_accounts_parsed(self):
        """Parse tai khoan tu text box thanh list dict."""
        accounts = []
        for line in self._get_accounts_raw():
            parts = line.split('|')
            if len(parts) >= 2:
                acc = {
                    'id': parts[0].strip(),
                    'password': parts[1].strip(),
                    'totp_secret': parts[2].strip() if len(parts) >= 3 else '',
                }
                accounts.append(acc)
        return accounts

    def _add_ipv6_to_machine(self):
        """Them IPv6 vao network interface bang netsh."""
        ipv6_list = self._get_ipv6_list()
        if not ipv6_list:
            self.ipv6_status_label.config(text="Chua nhap IPv6!", fg=RED)
            return

        self.ipv6_add_btn.config(state='disabled', text="DANG THEM...")
        self.ipv6_status_label.config(text="", fg=FG2)
        threading.Thread(target=self._do_add_ipv6, args=(ipv6_list,), daemon=True).start()

    def _do_add_ipv6(self, ipv6_list):
        """Thread: chay netsh de add IPv6."""
        import subprocess
        added = 0
        skipped = 0
        failed = 0

        # Tim network interface name
        iface = self._detect_interface()

        for ip in ipv6_list:
            try:
                result = subprocess.run(
                    ['netsh', 'interface', 'ipv6', 'add', 'address', iface, ip],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    added += 1
                elif 'already' in result.stderr.lower() or 'da ton tai' in result.stderr.lower() \
                        or result.returncode == 1:
                    # Co the da ton tai
                    skipped += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

        msg = f"Them: {added}  Co san: {skipped}  Loi: {failed}"
        color = GREEN if failed == 0 else YELLOW
        self.after(0, lambda: self.ipv6_status_label.config(text=msg, fg=color))
        self.after(0, lambda: self.ipv6_add_btn.config(state='normal', text="THEM VAO MAY"))

    def _detect_interface(self):
        """Tim ten network interface chinh (Ethernet/Wi-Fi)."""
        import subprocess
        try:
            result = subprocess.run(
                ['netsh', 'interface', 'ipv6', 'show', 'address'],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.split('\n'):
                line = line.strip()
                if line.startswith('Interface') and ':' in line:
                    name = line.split(':', 1)[1].strip()
                    if name.lower() not in ('loopback pseudo-interface 1',):
                        return name
        except Exception:
            pass
        return "Ethernet"

    def _test_ipv6(self):
        """Test IPv6 - kiem tra IPv6 nao da duoc assign va hoat dong."""
        ipv6_list = self._get_ipv6_list()
        if not ipv6_list:
            self.ipv6_status_label.config(text="Chua nhap IPv6!", fg=RED)
            return

        self.ipv6_test_btn.config(state='disabled', text="DANG TEST...")
        self.ipv6_status_label.config(text="", fg=FG2)
        threading.Thread(target=self._do_test_ipv6, args=(ipv6_list,), daemon=True).start()

    def _do_test_ipv6(self, ipv6_list):
        """Thread: kiem tra IPv6."""
        import subprocess
        import socket

        # 1. Lay danh sach IPv6 hien co tren may
        assigned = set()
        try:
            result = subprocess.run(
                ['netsh', 'interface', 'ipv6', 'show', 'address'],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.split('\n'):
                line = line.strip()
                if '2001:' in line or '::' in line:
                    parts = line.split()
                    for p in parts:
                        if ':' in p and not p.endswith('%'):
                            # Loai bo %interface suffix
                            clean = p.split('%')[0]
                            if clean.startswith('2001:') or clean.startswith('fe80:'):
                                assigned.add(clean.lower())
        except Exception:
            pass

        ok_count = 0
        fail_count = 0
        results = []

        for ip in ipv6_list:
            ip_lower = ip.lower()
            if ip_lower in assigned:
                # Da assign → test bind
                try:
                    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                    sock.bind((ip, 0))
                    sock.close()
                    results.append(f"  OK  {ip}")
                    ok_count += 1
                except Exception:
                    results.append(f"  BIND FAIL  {ip}")
                    fail_count += 1
            else:
                results.append(f"  CHUA ADD  {ip}")
                fail_count += 1

        # Hien ket qua trong popup
        msg = f"OK: {ok_count}  Loi: {fail_count} / {len(ipv6_list)}"
        color = GREEN if fail_count == 0 else (YELLOW if ok_count > 0 else RED)
        self.after(0, lambda: self.ipv6_status_label.config(text=msg, fg=color))
        self.after(0, lambda: self.ipv6_test_btn.config(state='normal', text="TEST IPv6"))

        # Show detail popup
        detail = f"IPv6 Test Results ({ok_count}/{len(ipv6_list)} OK)\n\n" + "\n".join(results)
        self.after(0, lambda: self._show_ipv6_result(detail))

    def _show_ipv6_result(self, detail):
        """Hien popup ket qua test IPv6."""
        popup = tk.Toplevel(self)
        popup.title("IPv6 Test Results")
        popup.geometry("500x400")
        popup.configure(bg=BG)
        popup.transient(self)

        text = tk.Text(popup, bg=BG, fg=FG, font=("Consolas", 10),
                       wrap='none', bd=0, padx=10, pady=10)
        text.pack(fill='both', expand=True)

        for line in detail.split('\n'):
            if 'OK' in line and 'FAIL' not in line and 'CHUA' not in line:
                text.insert('end', line + '\n', 'ok')
            elif 'FAIL' in line or 'CHUA' in line:
                text.insert('end', line + '\n', 'fail')
            else:
                text.insert('end', line + '\n')

        text.tag_config('ok', foreground=GREEN)
        text.tag_config('fail', foreground=RED)
        text.config(state='disabled')

        tk.Button(popup, text="DONG", font=("Segoe UI", 11, "bold"),
                  bg=BLUE, fg='#0f172a', relief='flat',
                  command=popup.destroy).pack(pady=10)

    def _get_chrome_count(self):
        val = self.chrome_combo.get()
        if val == "Tat ca":
            return 0
        try:
            return int(val)
        except:
            return 0

    # ============================================================
    # Update
    # ============================================================
    def _run_update(self):
        """Cap nhat code tu GitHub - giong vm_manager_gui.py."""
        import subprocess
        import urllib.request
        import zipfile
        import shutil

        GITHUB_ZIP_URL = "https://github.com/nguyenvantuong161978-dotcom/ve3-tool-simple/archive/refs/heads/main.zip"
        GITHUB_GIT_URL = "https://github.com/nguyenvantuong161978-dotcom/ve3-tool-simple.git"

        def do_update():
            self.update_btn.config(state="disabled", text="DANG CAP NHAT...", bg='#666')

            try:
                # Kiem tra git co san khong
                git_available = False
                try:
                    result = subprocess.run(
                        ["git", "--version"],
                        capture_output=True, text=True, timeout=10
                    )
                    git_available = (result.returncode == 0)
                except:
                    git_available = False

                if git_available:
                    # === DUNG GIT ===
                    # Kiem tra remote origin
                    result = subprocess.run(
                        ["git", "remote", "get-url", "origin"],
                        cwd=str(TOOL_DIR), capture_output=True, text=True, timeout=10
                    )
                    if result.returncode != 0:
                        subprocess.run(
                            ["git", "remote", "add", "origin", GITHUB_GIT_URL],
                            cwd=str(TOOL_DIR), capture_output=True, timeout=10
                        )
                    elif GITHUB_GIT_URL not in result.stdout.strip():
                        subprocess.run(
                            ["git", "remote", "set-url", "origin", GITHUB_GIT_URL],
                            cwd=str(TOOL_DIR), capture_output=True, timeout=10
                        )

                    # Fetch va reset
                    for cmd in [
                        ["git", "fetch", "origin", "main"],
                        ["git", "checkout", "main"],
                        ["git", "reset", "--hard", "origin/main"]
                    ]:
                        subprocess.run(cmd, cwd=str(TOOL_DIR), capture_output=True, text=True, timeout=120)

                else:
                    # === KHONG CO GIT - TAI ZIP ===
                    import ssl
                    ssl_context = ssl.create_default_context()
                    ssl_context.check_hostname = False
                    ssl_context.verify_mode = ssl.CERT_NONE

                    zip_path = TOOL_DIR / "update_temp.zip"
                    extract_dir = TOOL_DIR / "update_temp"

                    cache_buster = f"?t={int(time.time())}"
                    with urllib.request.urlopen(GITHUB_ZIP_URL + cache_buster, context=ssl_context) as response:
                        with open(str(zip_path), 'wb') as out_file:
                            out_file.write(response.read())

                    with zipfile.ZipFile(str(zip_path), 'r') as zip_ref:
                        zip_ref.extractall(str(extract_dir))

                    extracted_folder = extract_dir / "ve3-tool-simple-main"

                    # Copy files
                    files_to_update = [
                        "vm_manager.py", "vm_manager_gui.py", "run_excel_api.py",
                        "run_worker.py", "START.py", "START.bat", "START_SERVER.bat",
                        "requirements.txt", "_run_chrome1.py", "_run_chrome2.py",
                        "google_login.py", "VERSION.txt",
                    ]
                    for f in files_to_update:
                        src = extracted_folder / f
                        dst = TOOL_DIR / f
                        if src.exists():
                            shutil.copy2(str(src), str(dst))

                    # Copy modules/
                    src_modules = extracted_folder / "modules"
                    dst_modules = TOOL_DIR / "modules"
                    if src_modules.exists():
                        for py_file in src_modules.glob("*.py"):
                            shutil.copy2(str(py_file), str(dst_modules / py_file.name))
                        for sub_dir in src_modules.iterdir():
                            if sub_dir.is_dir():
                                dst_sub = dst_modules / sub_dir.name
                                if dst_sub.exists():
                                    shutil.rmtree(str(dst_sub))
                                shutil.copytree(str(sub_dir), str(dst_sub))

                    # Copy server/
                    src_server = extracted_folder / "server"
                    dst_server = TOOL_DIR / "server"
                    if src_server.exists():
                        dst_server.mkdir(exist_ok=True)
                        for py_file in src_server.glob("*.py"):
                            shutil.copy2(str(py_file), str(dst_server / py_file.name))

                    # v1.0.593: Copy control/ (master_control.py)
                    src_ctrl = extracted_folder / "control"
                    dst_ctrl = TOOL_DIR / "control"
                    if src_ctrl.exists():
                        dst_ctrl.mkdir(exist_ok=True)
                        for py_file in src_ctrl.glob("*.py"):
                            shutil.copy2(str(py_file), str(dst_ctrl / py_file.name))

                    # Copy ipv6/ (IPv6 Dynamic Pool)
                    src_ipv6 = extracted_folder / "ipv6"
                    dst_ipv6 = TOOL_DIR / "ipv6"
                    if src_ipv6.exists():
                        dst_ipv6.mkdir(exist_ok=True)
                        for py_file in src_ipv6.glob("*.py"):
                            shutil.copy2(str(py_file), str(dst_ipv6 / py_file.name))

                    # Cleanup
                    if zip_path.exists():
                        zip_path.unlink()
                    if extract_dir.exists():
                        shutil.rmtree(str(extract_dir))

                # Restart
                self.update_btn.config(text="XONG - KHOI DONG LAI...", bg='#00ff88')
                self.after(1000, lambda: os.execv(sys.executable, [sys.executable] + sys.argv))

            except Exception as e:
                self.update_btn.config(text="LOI", bg=RED)
                print(f"Update error: {e}")
                from tkinter import messagebox
                messagebox.showerror("Loi cap nhat", f"Loi: {e}\n\nThu tai thu cong:\n{GITHUB_ZIP_URL}")
            finally:
                self.after(3000, lambda: self.update_btn.config(state="normal", text="UPDATE", bg='#0984e3'))

        threading.Thread(target=do_update, daemon=True).start()

    # ============================================================
    # Start Server
    # ============================================================
    def _on_start(self):
        if self._server_started:
            return

        # v1.0.672: Validate ProxyXoay keys vs Chrome count
        if self.proxy_type_var.get() == "proxyxoay":
            keys = self._get_px_keys()
            chrome_count = self._get_chrome_count()
            if not keys:
                from tkinter import messagebox
                messagebox.showerror("ProxyXoay", "Chua nhap API key!\nMoi Chrome can 1 key rieng.")
                return
            if chrome_count > 0 and len(keys) < chrome_count:
                from tkinter import messagebox
                messagebox.showerror("ProxyXoay",
                    f"Thieu key! Co {len(keys)} key nhung can {chrome_count} cho {chrome_count} Chrome.\n"
                    f"Moi Chrome can 1 key rieng de co IP khac nhau.")
                return

        self._server_started = True
        self.start_btn.config(text="DANG KHOI DONG...", bg='#475569', state='disabled')
        self._save_settings()

        use_ipv6 = self.ipv6_var.get()
        chrome_count = self._get_chrome_count()

        # Thu thap IPv6 bo sung tu text box
        extra_ipv6 = self._get_ipv6_list()

        # Parse accounts tu text box
        gui_accounts = self._get_accounts_parsed()

        # Switch to monitor page
        self.after(500, lambda: self._switch_to_monitor(use_ipv6, chrome_count, extra_ipv6, gui_accounts))

    def _switch_to_monitor(self, use_ipv6, chrome_count, extra_ipv6=None, gui_accounts=None):
        self.setup_frame.destroy()
        self._build_monitor_page()

        # Start server in background
        threading.Thread(
            target=self._start_server,
            args=(use_ipv6, chrome_count, extra_ipv6 or [], gui_accounts or []),
            daemon=True,
        ).start()

    def _start_server(self, use_ipv6, chrome_count, extra_ipv6=None, gui_accounts=None):
        """Start Flask + Chrome workers in background."""
        self._add_log("Khoi dong server...", "INFO")

        # Import and configure app
        from server.app import (
            app, server_settings, settings_lock, _do_start_workers,
            server_log, cleanup_old_tasks, server_logs, log_lock
        )

        # Apply settings
        with settings_lock:
            server_settings['use_ipv6'] = use_ipv6
            server_settings['chrome_count'] = chrome_count
            server_settings['extra_ipv6'] = extra_ipv6 or []
            server_settings['gui_accounts'] = gui_accounts or []
            server_settings['mode'] = 'gop'
            server_settings['started'] = True
            # v1.0.545: Proxy Provider config
            server_settings['proxy_config'] = self._get_proxy_config()
            # v1.0.561: Pool API URL
            server_settings['pool_api_url'] = self.pool_api_var.get().strip()

        # Redirect server_log to our GUI
        self._server_logs_ref = server_logs
        self._server_log_lock = log_lock

        self._add_log(f"IPv6: {'BAT' if use_ipv6 else 'TAT'}", "INFO")
        self._add_log(f"Chrome: {chrome_count or 'TAT CA'}", "INFO")

        # Start cleanup
        threading.Thread(target=cleanup_old_tasks, daemon=True).start()

        # Start Chrome workers
        threading.Thread(target=_do_start_workers, daemon=True).start()
        self._add_log("Chrome workers dang setup...", "INFO")

        # Start Flask (blocking)
        self._add_log("Flask server: http://0.0.0.0:5000/", "OK")
        try:
            app.run(host='0.0.0.0', port=5000, debug=False, threaded=True, use_reloader=False)
        except Exception as e:
            self._add_log(f"Flask error: {e}", "ERROR")

    # ============================================================
    # Monitor Page
    # ============================================================
    def _build_monitor_page(self):
        self.monitor_frame = tk.Frame(self, bg=BG)
        self.monitor_frame.pack(fill='both', expand=True)

        # Header
        header = tk.Frame(self.monitor_frame, bg=BG2)
        header.pack(fill='x')

        tk.Label(header, text="Chrome Server", font=("Segoe UI", 14, "bold"),
                 bg=BG2, fg=BLUE).pack(side='left', padx=16, pady=10)

        self.header_status = tk.Label(header, text="Starting...", font=("Segoe UI", 10),
                                      bg=BG2, fg=YELLOW)
        self.header_status.pack(side='right', padx=16)

        # Stats row
        stats_frame = tk.Frame(self.monitor_frame, bg=BG)
        stats_frame.pack(fill='x', padx=12, pady=(12, 0))

        self.stat_labels = {}
        stat_defs = [
            ("workers", "Workers Ready", BLUE),
            ("queue", "In Queue", ORANGE),
            ("completed", "Completed", GREEN),
            ("failed", "Failed", RED),
        ]

        for key, label, color in stat_defs:
            box = tk.Frame(stats_frame, bg=BG2, highlightbackground=BORDER, highlightthickness=1)
            box.pack(side='left', fill='x', expand=True, padx=4)
            num = tk.Label(box, text="0", font=("Segoe UI", 22, "bold"), bg=BG2, fg=color)
            num.pack(pady=(8, 0))
            tk.Label(box, text=label.upper(), font=("Segoe UI", 8), bg=BG2, fg=FG2).pack(pady=(0, 8))
            self.stat_labels[key] = num

        # Workers list
        workers_frame = tk.LabelFrame(self.monitor_frame, text=" Chrome Workers ",
                                       font=("Segoe UI", 10, "bold"),
                                       bg=BG2, fg=FG2, bd=1, relief='solid')
        workers_frame.pack(fill='x', padx=12, pady=(12, 0))

        self.workers_container = tk.Frame(workers_frame, bg=BG2)
        self.workers_container.pack(fill='x', padx=8, pady=8)

        self.worker_labels = {}

        # Log area
        log_frame = tk.LabelFrame(self.monitor_frame, text=" Server Logs ",
                                   font=("Segoe UI", 10, "bold"),
                                   bg=BG2, fg=FG2, bd=1, relief='solid')
        log_frame.pack(fill='both', expand=True, padx=12, pady=12)

        self.log_text = tk.Text(log_frame, bg=BG, fg=FG2, font=("Consolas", 9),
                                wrap='none', state='disabled', bd=0,
                                selectbackground='#334155')
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.config(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.log_text.pack(fill='both', expand=True, padx=4, pady=4)

        # Color tags
        self.log_text.tag_config('INFO', foreground=FG2)
        self.log_text.tag_config('OK', foreground=GREEN)
        self.log_text.tag_config('WARN', foreground=YELLOW)
        self.log_text.tag_config('ERROR', foreground=RED)

        # Start update loop
        self._last_log_count = 0
        self._update_monitor()

    def _add_log(self, msg, level="INFO"):
        ts = time.strftime("%H:%M:%S")
        self._logs.append({"time": ts, "level": level, "msg": msg})

    def _update_monitor(self):
        """Update monitor every 2s."""
        try:
            self._update_stats()
            self._update_workers_display()
            self._update_logs()
        except Exception:
            pass

        self.after(2000, self._update_monitor)

    def _update_stats(self):
        try:
            from server.app import chrome_pool, task_queue, queue_lock, stats, external_workers, external_workers_lock
            ready = 0
            total = 0
            if chrome_pool:
                ready = chrome_pool.total_ready()
                total = len(chrome_pool.workers)

            # Include external workers
            with external_workers_lock:
                for wid, ew in external_workers.items():
                    total += 1
                    if ew["status"] in ("ready", "busy"):
                        ready += 1

            self.stat_labels['workers'].config(text=f"{ready}/{total}")
            if ready > 0:
                self.header_status.config(text=f"{ready} workers ready", fg=GREEN)
            elif total > 0:
                self.header_status.config(text="Setting up...", fg=YELLOW)

            with queue_lock:
                self.stat_labels['queue'].config(text=str(len(task_queue)))

            self.stat_labels['completed'].config(text=str(stats['total_completed']))
            self.stat_labels['failed'].config(text=str(stats['total_failed']))
        except:
            pass

    def _update_workers_display(self):
        try:
            from server.app import chrome_pool, external_workers, external_workers_lock

            # Internal workers (from chrome_pool)
            if chrome_pool:
                workers = chrome_pool.workers
                for w in workers:
                    wid = f"w{w.index}"
                    if wid not in self.worker_labels:
                        frame = tk.Frame(self.workers_container, bg=BG, highlightbackground=BORDER,
                                         highlightthickness=1, padx=8, pady=4)
                        frame.grid(sticky='nsew', padx=4, pady=4)

                        name_lbl = tk.Label(frame, text=f"Chrome-{w.index}", font=("Segoe UI", 10, "bold"),
                                            bg=BG, fg=FG)
                        name_lbl.pack(anchor='w')

                        status_lbl = tk.Label(frame, text="SETUP", font=("Segoe UI", 9),
                                              bg=BG, fg=YELLOW)
                        status_lbl.pack(anchor='w')

                        info_lbl = tk.Label(frame, text="", font=("Segoe UI", 8),
                                            bg=BG, fg=FG2)
                        info_lbl.pack(anchor='w')

                        self.worker_labels[wid] = {
                            'frame': frame,
                            'status': status_lbl,
                            'info': info_lbl,
                        }

                    labels = self.worker_labels[wid]
                    if w.busy:
                        labels['status'].config(text="BUSY", fg=ORANGE)
                        labels['frame'].config(highlightbackground=ORANGE)
                    elif w.ready:
                        labels['status'].config(text="READY", fg=GREEN)
                        labels['frame'].config(highlightbackground=GREEN)
                    else:
                        labels['status'].config(text="SETUP", fg=YELLOW)
                        labels['frame'].config(highlightbackground=YELLOW)

                    acc = w.account['id'].split('@')[0] if w.account else ""
                    labels['info'].config(text=f"{acc}\nDone:{w.total_completed} Fail:{w.total_failed}")

            # External workers (separate processes)
            with external_workers_lock:
                for ewid, ew in external_workers.items():
                    wid = f"ext{ew['index']}"
                    if wid not in self.worker_labels:
                        frame = tk.Frame(self.workers_container, bg=BG, highlightbackground=BORDER,
                                         highlightthickness=1, padx=8, pady=4)
                        frame.grid(sticky='nsew', padx=4, pady=4)

                        name_lbl = tk.Label(frame, text=f"Ext-{ew['index']}", font=("Segoe UI", 10, "bold"),
                                            bg=BG, fg=FG)
                        name_lbl.pack(anchor='w')

                        status_lbl = tk.Label(frame, text="EXT", font=("Segoe UI", 9),
                                              bg=BG, fg=YELLOW)
                        status_lbl.pack(anchor='w')

                        info_lbl = tk.Label(frame, text="", font=("Segoe UI", 8),
                                            bg=BG, fg=FG2)
                        info_lbl.pack(anchor='w')

                        self.worker_labels[wid] = {
                            'frame': frame,
                            'status': status_lbl,
                            'info': info_lbl,
                        }

                    labels = self.worker_labels[wid]
                    status = ew.get("status", "starting")
                    if status == "busy":
                        labels['status'].config(text="BUSY", fg=ORANGE)
                        labels['frame'].config(highlightbackground=ORANGE)
                    elif status == "ready":
                        labels['status'].config(text="READY", fg=GREEN)
                        labels['frame'].config(highlightbackground=GREEN)
                    elif status == "failed":
                        labels['status'].config(text="FAIL", fg=RED)
                        labels['frame'].config(highlightbackground=RED)
                    else:
                        labels['status'].config(text="SETUP", fg=YELLOW)
                        labels['frame'].config(highlightbackground=YELLOW)

                    acc = str(ew.get('account', '')).split('@')[0] if ew.get('account') else ""
                    labels['info'].config(text=f"{acc}\nDone:{ew.get('total_completed',0)} Fail:{ew.get('total_failed',0)}")

            # Reflow cards: 5 cot -> 10 workers se thanh 2 dong.
            cols = 5
            try:
                cols = max(1, int(os.getenv("GUI_WORKER_COLUMNS", "5")))
            except Exception:
                cols = 5

            # Sap xep on dinh: internal w0..wn truoc, external sau.
            def _sort_key(k: str):
                if k.startswith('w'):
                    try:
                        return (0, int(k[1:]))
                    except Exception:
                        return (0, 10_000)
                if k.startswith('ext'):
                    try:
                        return (1, int(k[3:]))
                    except Exception:
                        return (1, 10_000)
                return (2, 10_000)

            keys = sorted(self.worker_labels.keys(), key=_sort_key)
            for idx, wid in enumerate(keys):
                row = idx // cols
                col = idx % cols
                self.worker_labels[wid]['frame'].grid_configure(row=row, column=col)
                self.workers_container.grid_columnconfigure(col, weight=1)

        except:
            pass

    def _update_logs(self):
        """Pull logs from server_logs."""
        try:
            from server.app import server_logs, log_lock
            with log_lock:
                new_logs = list(server_logs[self._last_log_count:])
                self._last_log_count = len(server_logs)

            if new_logs:
                self.log_text.config(state='normal')
                for log in new_logs:
                    level = log.get('level', 'INFO')
                    line = f"[{log['time']}] {log['msg']}\n"
                    self.log_text.insert('end', line, level)
                self.log_text.see('end')
                self.log_text.config(state='disabled')
        except:
            pass


def main():
    gui = ServerGUI()
    gui.mainloop()


if __name__ == '__main__':
    main()
