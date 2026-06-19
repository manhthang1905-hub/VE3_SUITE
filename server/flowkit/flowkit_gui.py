"""
FlowKit Server GUI — Setup, monitor, and manage FlowKit instances.

Run: python flowkit_gui.py
  or: START_FLOWKIT_GUI.bat
"""
import sys
import os
import json
import shutil
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE_DIR = Path(__file__).parent
TOOL_DIR = BASE_DIR.parent  # server/ (or Documents/ on standalone VM)
SUITE_ROOT = TOOL_DIR.parent  # VE3_SUITE root (or user home on standalone VM)
_IS_STANDALONE = not (TOOL_DIR / "google_login.py").exists() and (BASE_DIR / "google_login.py").exists()
LOG_DIR = BASE_DIR / "logs"
sys.path.insert(0, str(BASE_DIR))
if not _IS_STANDALONE:
    sys.path.insert(0, str(TOOL_DIR))

GITHUB_REPO = "manhthang1905-hub/VE3_SUITE"
GITHUB_ZIP_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"
GITHUB_GIT_URL = f"https://github.com/{GITHUB_REPO}.git"

# Colors (dark theme — matches server_gui.py)
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

SETTINGS_FILE = BASE_DIR / "config" / "flowkit_gui.json"

PROTECTED_PATHS = {
    "chrome_profiles",
    "config/settings.yaml",
    "config/flowkit_gui.json",
    "config/flow_accounts.yaml",
    ".claude",
}


def _get_auto_version() -> str:
    # 1. VERSION file at repo root (always correct after git checkout)
    for vf in (SUITE_ROOT / "VERSION", BASE_DIR / "VERSION.txt", BASE_DIR / "VERSION"):
        try:
            if vf.exists():
                txt = vf.read_text(encoding="utf-8-sig").split("\n")[0].strip()
                if txt and len(txt) > 3:
                    return txt
        except Exception:
            pass
    # 2. Fallback: git rev-list (dev machine only, not shallow clone)
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(SUITE_ROOT), capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            count = int(result.stdout.strip())
            if count > 10:
                return f"1.0.{count}"
    except Exception:
        pass
    return "?"


class FlowKitGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self._version = _get_auto_version()
        self._remote_version = ""
        self.title(f"FlowKit Server v{self._version}")
        gui_w = 700
        try:
            gui_h = self.winfo_screenheight() - 40
        except Exception:
            gui_h = 1040
        self.geometry(f"{gui_w}x{gui_h}+0+0")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(600, 500)

        self._started = False
        self._processes = []
        self._log_handles = []
        self._proc_lock = threading.Lock()
        self._logs = []
        self._workers = []
        self._stats = {}
        self._poll_thread = None
        self._account_stats = []
        self._settings = self._load_gui_settings()

        # Process supervision — track by role for auto-restart
        self._gateway_proc = None
        self._gateway_port_val = 5100
        self._gateway_log_fh = None
        self._gateway_tail_gen = 0
        self._agent_procs = {}    # name → Popen
        self._agent_log_fhs = {}  # name → file handle
        self._inst_configs = {}   # name → inst config dict
        self._restart_times = {}  # "gateway"/name → list of timestamps
        self._pending_restart_id = None

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._notebook = ttk.Notebook(self)
        self._notebook.pack(fill='both', expand=True)

        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', background=BG2, foreground=FG,
                        padding=[10, 4], font=('Segoe UI', 9))
        style.map('TNotebook.Tab',
                  background=[('selected', BLUE)],
                  foreground=[('selected', '#000')])

        self._setup_frame = tk.Frame(self._notebook, bg=BG)
        self._monitor_frame = tk.Frame(self._notebook, bg=BG)
        self._notebook.add(self._setup_frame, text="  Setup  ")
        self._notebook.add(self._monitor_frame, text="  Monitor  ")

        self._build_setup_page()
        self._build_monitor_page()
        self._load_settings()
        self._detect_chromes()

        # Kill leftover Chrome/agents from previous run (crash or restart)
        self._kill_all_chrome()
        self._kill_flowkit_python()
        # Delete stale IPv6 override + health files (prevent dead IP on startup)
        for pattern in (".ipv6_override_*", ".proxy_health_*"):
            for f in BASE_DIR.glob(pattern):
                try:
                    f.unlink()
                except Exception:
                    pass

        self._autostart_remaining = 15
        self._autostart_id = None
        if self._autostart_var.get():
            self._autostart_id = self.after(1000, self._autostart_tick)

    # ============================================================
    # Setup Page
    # ============================================================
    def _build_setup_page(self):
        p = self._setup_frame

        # Header row: title + version + update button
        hdr = tk.Frame(p, bg=BG)
        hdr.pack(fill='x', padx=10, pady=(8, 4))
        tk.Label(hdr, text="FlowKit Server", font=('Segoe UI', 14, 'bold'),
                 fg=BLUE, bg=BG).pack(side='left')
        self._version_label = tk.Label(hdr, text=f"v{self._version}", font=('Consolas', 9),
                 fg=FG2, bg=BG)
        self._version_label.pack(side='left', padx=(6, 0))

        self._update_btn = tk.Button(
            hdr, text="Update", command=self._on_check_update,
            bg='#0984e3', fg='#fff', font=('Segoe UI', 9, 'bold'),
            relief='flat', cursor='hand2', padx=10)
        self._update_btn.pack(side='right')

        # ── Gateway + 403 Rotation (same row) ──
        row_top = tk.Frame(p, bg=BG)
        row_top.pack(fill='x', padx=10, pady=2)

        gw = tk.LabelFrame(row_top, text=" Gateway ", bg=BG2, fg=FG2,
                           font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        gw.pack(side='left', fill='x', expand=True, padx=(0, 4))
        gw_row = tk.Frame(gw, bg=BG2)
        gw_row.pack(fill='x', padx=6, pady=4)
        tk.Label(gw_row, text="Port:", bg=BG2, fg=FG, font=('Segoe UI', 9)).pack(side='left')
        self._gateway_port = tk.Entry(gw_row, width=7, bg=BG, fg=FG, insertbackground=FG,
                                      font=('Consolas', 10), bd=1, relief='solid')
        self._gateway_port.pack(side='left', padx=4)
        self._gateway_port.insert(0, "5100")

        rot = tk.LabelFrame(row_top, text=" 403 Rotation ", bg=BG2, fg=FG2,
                            font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        rot.pack(side='left', fill='x', expand=True, padx=(4, 0))
        rot_row = tk.Frame(rot, bg=BG2)
        rot_row.pack(fill='x', padx=6, pady=4)
        tk.Label(rot_row, text="Max:", bg=BG2, fg=FG, font=('Segoe UI', 9)).pack(side='left')
        self._max_403 = tk.Entry(rot_row, width=4, bg=BG, fg=FG, insertbackground=FG,
                                 font=('Consolas', 10), bd=1, relief='solid')
        self._max_403.pack(side='left', padx=4)
        self._max_403.insert(0, "3")
        tk.Label(rot_row, text="Cool:", bg=BG2, fg=FG, font=('Segoe UI', 9)).pack(side='left', padx=(8, 0))
        self._cooldown = tk.Entry(rot_row, width=5, bg=BG, fg=FG, insertbackground=FG,
                                  font=('Consolas', 10), bd=1, relief='solid')
        self._cooldown.pack(side='left', padx=4)
        self._cooldown.insert(0, "300")

        # ── IPv6 Pool ──
        f = tk.LabelFrame(p, text=" IPv6 Pool ", bg=BG2, fg=FG2,
                          font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        f.pack(fill='x', padx=10, pady=2)
        ipv6_row = tk.Frame(f, bg=BG2)
        ipv6_row.pack(fill='x', padx=6, pady=4)
        self._ipv6_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(ipv6_row, text="Enable", variable=self._ipv6_enabled,
                       bg=BG2, fg=FG, selectcolor=BG, activebackground=BG2,
                       font=('Segoe UI', 9)).pack(side='left')
        tk.Label(ipv6_row, text="URL:", bg=BG2, fg=FG, font=('Segoe UI', 9)).pack(side='left', padx=(8, 0))
        self._pool_url = tk.Entry(ipv6_row, width=30, bg=BG, fg=FG, insertbackground=FG,
                                  font=('Consolas', 9), bd=1, relief='solid')
        self._pool_url.pack(side='left', padx=4, fill='x', expand=True)
        self._pool_url.insert(0, "http://192.168.88.146:8765")
        btn = tk.Button(ipv6_row, text="TEST", command=self._test_pool, bg='#334155',
                        fg=FG, font=('Segoe UI', 8, 'bold'), relief='flat', cursor='hand2')
        btn.pack(side='left', padx=4)
        self._pool_status = tk.Label(f, text="", bg=BG2, fg=FG2, font=('Segoe UI', 8))
        self._pool_status.pack(padx=6, pady=(0, 2), anchor='w')

        # ── Chrome Instances ──
        f = tk.LabelFrame(p, text=" Chrome ", bg=BG2, fg=FG2,
                          font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        f.pack(fill='x', padx=10, pady=2)
        self._chrome_info = tk.Label(f, text="Dang quet...", bg=BG2, fg=FG,
                                     font=('Segoe UI', 9))
        self._chrome_info.pack(padx=6, pady=3, anchor='w')

        chrome_row = tk.Frame(f, bg=BG2)
        chrome_row.pack(fill='x', padx=6, pady=(0, 4))
        tk.Label(chrome_row, text="So luong Chrome:", bg=BG2, fg=FG,
                 font=('Segoe UI', 9)).pack(side='left')
        self._chrome_count_var = tk.StringVar(value="Tat ca")
        max_chromes = max(1, len(getattr(self, '_chrome_dirs', [])) or 6)
        chrome_options = ["Tat ca"] + [str(i) for i in range(1, max_chromes + 1)]
        self._chrome_count_combo = ttk.Combobox(
            chrome_row, textvariable=self._chrome_count_var,
            values=chrome_options, state='readonly', width=8)
        self._chrome_count_combo.pack(side='left', padx=6)
        # Load saved value
        try:
            saved = self._settings.get('chrome_count', 0)
            if saved and saved > 0:
                self._chrome_count_combo.set(str(saved))
        except Exception:
            pass

        # ── Fixed Account Mode ──
        fa_frame = tk.LabelFrame(p, text=" Fixed Account ", bg=BG2, fg=FG2,
                                  font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        fa_frame.pack(fill='x', padx=10, pady=2)
        fa_row = tk.Frame(fa_frame, bg=BG2)
        fa_row.pack(fill='x', padx=6, pady=4)
        self._fixed_account_var = tk.BooleanVar(value=False)
        self._fa_checkbox = tk.Checkbutton(
            fa_row, text="Co dinh tai khoan", variable=self._fixed_account_var,
            bg=BG2, fg=FG, selectcolor=BG, activebackground=BG2,
            font=('Segoe UI', 9), command=self._on_fa_toggle)
        self._fa_checkbox.pack(side='left')
        tk.Label(fa_row, text="Dong thoi:", bg=BG2, fg=FG,
                 font=('Segoe UI', 9)).pack(side='left', padx=(16, 0))
        self._fa_concurrent = tk.Spinbox(
            fa_row, from_=1, to=20, width=4, bg=BG, fg=FG,
            insertbackground=FG, font=('Consolas', 10), bd=1, relief='solid')
        self._fa_concurrent.pack(side='left', padx=4)
        self._fa_concurrent.delete(0, 'end')
        self._fa_concurrent.insert(0, "2")
        self._fa_info = tk.Label(fa_frame, text="", bg=BG2, fg=FG2,
                                  font=('Segoe UI', 8))
        self._fa_info.pack(padx=6, pady=(0, 2), anchor='w')
        # Load saved values
        try:
            self._fixed_account_var.set(self._settings.get('fixed_account_enabled', False))
            saved_concurrent = self._settings.get('fixed_account_concurrent', 2)
            self._fa_concurrent.delete(0, 'end')
            self._fa_concurrent.insert(0, str(saved_concurrent))
        except Exception:
            pass
        self._on_fa_toggle()

        # ── Tu dong ──
        auto_frame = tk.LabelFrame(p, text=" Tu dong ", bg=BG2, fg=FG2,
                                    font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        auto_frame.pack(fill='x', padx=10, pady=2)
        auto_row1 = tk.Frame(auto_frame, bg=BG2)
        auto_row1.pack(fill='x', padx=6, pady=(4, 0))
        self._autostart_var = tk.BooleanVar(value=True)
        tk.Checkbutton(auto_row1, text="Tu dong chay khi mo GUI", variable=self._autostart_var,
                       bg=BG2, fg=FG, selectcolor=BG, activebackground=BG2,
                       font=('Segoe UI', 9)).pack(side='left')
        auto_row2 = tk.Frame(auto_frame, bg=BG2)
        auto_row2.pack(fill='x', padx=6, pady=(2, 4))
        self._auto_restart_var = tk.BooleanVar(value=True)
        tk.Checkbutton(auto_row2, text="Tu dong restart sau", variable=self._auto_restart_var,
                       bg=BG2, fg=FG, selectcolor=BG, activebackground=BG2,
                       font=('Segoe UI', 9)).pack(side='left')
        self._restart_hours = tk.Spinbox(
            auto_row2, from_=1, to=24, width=4, bg=BG, fg=FG,
            insertbackground=FG, font=('Consolas', 10), bd=1, relief='solid')
        self._restart_hours.pack(side='left', padx=4)
        self._restart_hours.delete(0, 'end')
        self._restart_hours.insert(0, "3")
        tk.Label(auto_row2, text="gio", bg=BG2, fg=FG,
                 font=('Segoe UI', 9)).pack(side='left')
        self._restart_countdown_label = tk.Label(
            auto_frame, text="", bg=BG2, fg=FG2, font=('Segoe UI', 8))
        self._restart_countdown_label.pack(padx=6, pady=(0, 2), anchor='w')
        try:
            self._autostart_var.set(self._settings.get('autostart_enabled', True))
            self._auto_restart_var.set(self._settings.get('auto_restart_enabled', True))
            self._restart_hours.delete(0, 'end')
            self._restart_hours.insert(0, str(self._settings.get('auto_restart_hours', 3)))
        except Exception:
            pass

        # ── Google Accounts ──
        f = tk.LabelFrame(p, text=" Tai khoan Google (email|password|2fa_secret) ",
                          bg=BG2, fg=FG2, font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        f.pack(fill='x', padx=10, pady=2)
        self._accounts_text = scrolledtext.ScrolledText(
            f, width=60, height=4, bg=BG, fg=FG, insertbackground=FG,
            font=('Consolas', 9), bd=1, relief='solid', wrap='none')
        self._accounts_text.pack(padx=6, pady=4, fill='x')

        # ── Buttons ──
        btn_frame = tk.Frame(p, bg=BG)
        btn_frame.pack(fill='x', padx=10, pady=(6, 8))

        self._start_btn = tk.Button(
            btn_frame, text="START FLOWKIT", command=self._on_start,
            bg=GREEN, fg='#000', font=('Segoe UI', 12, 'bold'),
            relief='flat', cursor='hand2', height=1)
        self._start_btn.pack(fill='x', pady=2)

        self._stop_btn = tk.Button(
            btn_frame, text="STOP", command=self._on_stop,
            bg=RED, fg='#fff', font=('Segoe UI', 10, 'bold'),
            relief='flat', cursor='hand2', state='disabled')
        self._stop_btn.pack(fill='x', pady=2)

    # ============================================================
    # Monitor Page
    # ============================================================
    def _build_monitor_page(self):
        p = self._monitor_frame

        # Stats row
        stats_f = tk.Frame(p, bg=BG)
        stats_f.pack(fill='x', padx=8, pady=4)

        self._stat_labels = {}
        for col, (key, label, color) in enumerate([
            ('available', 'Available', GREEN),
            ('completed', 'Done', BLUE),
            ('failed', 'Failed', RED),
            ('cooling', 'Cooling', ORANGE),
        ]):
            box = tk.Frame(stats_f, bg=BG2, bd=1, relief='solid')
            box.pack(side='left', fill='x', expand=True, padx=2)
            val = tk.Label(box, text="0", font=('Segoe UI', 18, 'bold'),
                           fg=color, bg=BG2)
            val.pack(pady=(4, 0))
            tk.Label(box, text=label, font=('Segoe UI', 8), fg=FG2, bg=BG2).pack(pady=(0, 4))
            self._stat_labels[key] = val

        # Refresh button
        rf = tk.Frame(p, bg=BG)
        rf.pack(fill='x', padx=8, pady=(4, 0))
        tk.Button(rf, text="⟳ Refresh", command=lambda: threading.Thread(target=self._poll_once, daemon=True).start(),
                  bg=BG2, fg=FG, font=('Segoe UI', 8), relief='flat', cursor='hand2',
                  bd=1, width=10).pack(side='right')

        # Workers grid
        wf = tk.LabelFrame(p, text=" Workers ", bg=BG2, fg=FG2,
                           font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        wf.pack(fill='x', padx=8, pady=2)
        self._workers_frame = tk.Frame(wf, bg=BG2)
        self._workers_frame.pack(fill='x', padx=4, pady=4)
        self._worker_cards = {}

        # Accounts overview
        af = tk.LabelFrame(p, text=" Accounts ", bg=BG2, fg=FG2,
                           font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        af.pack(fill='x', padx=8, pady=2)
        self._accounts_frame = tk.Frame(af, bg=BG2)
        self._accounts_frame.pack(fill='x', padx=4, pady=4)

        # Logs — hidden by default, toggle with button
        log_header = tk.Frame(p, bg=BG)
        log_header.pack(fill='x', padx=8, pady=(2, 0))
        self._log_visible = False
        self._log_toggle_btn = tk.Button(
            log_header, text="▶ Show Logs", command=self._toggle_logs,
            bg=BG2, fg=FG2, font=('Segoe UI', 8), relief='flat', cursor='hand2', bd=0)
        self._log_toggle_btn.pack(side='left')

        self._log_frame = tk.LabelFrame(p, text=" Logs ", bg=BG2, fg=FG2,
                           font=('Segoe UI', 8, 'bold'), bd=1, relief='groove')
        # NOT packed — hidden by default
        self._log_text = scrolledtext.ScrolledText(
            self._log_frame, bg=BG, fg=FG2, font=('Consolas', 8),
            bd=0, wrap='word', state='disabled')
        self._log_text.pack(fill='both', expand=True, padx=2, pady=2)
        self._log_text.tag_config('OK', foreground=GREEN)
        self._log_text.tag_config('WARN', foreground=YELLOW)
        self._log_text.tag_config('ERROR', foreground=RED)
        self._log_text.tag_config('INFO', foreground=FG2)

    def _toggle_logs(self):
        if self._log_visible:
            self._log_frame.pack_forget()
            self._log_toggle_btn.config(text="▶ Show Logs")
            self._log_visible = False
        else:
            self._log_frame.pack(fill='both', expand=True, padx=8, pady=2)
            self._log_toggle_btn.config(text="▼ Hide Logs")
            self._log_visible = True

    # ============================================================
    # Chrome Detection
    # ============================================================
    def _detect_chromes(self):
        """Find Chrome Portable copies in flowkit directory."""
        self._chrome_dirs = []
        for d in sorted(BASE_DIR.iterdir()):
            if d.is_dir() and 'GoogleChromePortable' in d.name:
                portable = d / 'GoogleChromePortable.exe'
                if portable.exists():
                    self._chrome_dirs.append(d)
        count = len(self._chrome_dirs)
        names = ', '.join(d.name for d in self._chrome_dirs[:5])
        if count > 5:
            names += f'... (+{count - 5})'
        self._chrome_info.config(text=f"Tim thay {count} Chrome: {names}" if count else "Khong tim thay Chrome Portable!")

    # ============================================================
    def _on_fa_toggle(self):
        if self._fixed_account_var.get():
            total = len(getattr(self, '_chrome_dirs', [])) or 6
            try:
                concurrent = int(self._fa_concurrent.get() or 2)
            except Exception:
                concurrent = 2
            self._fa_info.config(
                text=f"Login {total} Chrome, chay {concurrent} dong thoi. 403 → swap Chrome.",
                fg=BLUE)
        else:
            self._fa_info.config(text="", fg=FG2)

    # Settings
    # ============================================================
    def _load_settings(self):
        if not SETTINGS_FILE.exists():
            return
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
            self._gateway_port.delete(0, 'end')
            self._gateway_port.insert(0, str(data.get('gateway_port', 5100)))
            self._ipv6_enabled.set(data.get('ipv6_enabled', False))
            self._pool_url.delete(0, 'end')
            self._pool_url.insert(0, data.get('pool_url', 'http://192.168.88.146:8765'))
            self._max_403.delete(0, 'end')
            self._max_403.insert(0, str(data.get('max_403', 3)))
            self._cooldown.delete(0, 'end')
            self._cooldown.insert(0, str(data.get('cooldown', 300)))
            accounts = data.get('accounts', '')
            if accounts:
                self._accounts_text.delete('1.0', 'end')
                self._accounts_text.insert('1.0', accounts)
            self._fixed_account_var.set(data.get('fixed_account_enabled', False))
            self._fa_concurrent.delete(0, 'end')
            self._fa_concurrent.insert(0, str(data.get('fixed_account_concurrent', 2)))
            self._on_fa_toggle()
            self._auto_restart_var.set(data.get('auto_restart_enabled', True))
            self._restart_hours.delete(0, 'end')
            self._restart_hours.insert(0, str(data.get('auto_restart_hours', 3)))
        except Exception:
            pass

    def _load_gui_settings(self) -> dict:
        try:
            if SETTINGS_FILE.exists():
                return json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
        return {}

    def _get_chrome_count(self) -> int:
        try:
            combo = getattr(self, '_chrome_count_combo', None)
            if not combo:
                return self._settings.get('chrome_count', 0) if hasattr(self, '_settings') else 0
            val = combo.get()
            if val == "Tat ca":
                return 0
            return int(val)
        except Exception:
            return 0

    def _save_settings(self):
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'gateway_port': int(self._gateway_port.get() or 5100),
            'ipv6_enabled': self._ipv6_enabled.get(),
            'pool_url': self._pool_url.get().strip(),
            'max_403': int(self._max_403.get() or 3),
            'cooldown': int(self._cooldown.get() or 300),
            'accounts': self._accounts_text.get('1.0', 'end').strip(),
            'chrome_count': self._get_chrome_count(),
            'fixed_account_enabled': self._fixed_account_var.get(),
            'fixed_account_concurrent': int(self._fa_concurrent.get() or 2),
            'autostart_enabled': self._autostart_var.get(),
            'auto_restart_enabled': self._auto_restart_var.get(),
            'auto_restart_hours': int(self._restart_hours.get() or 3),
        }
        self._settings = data
        SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')

    # ============================================================
    # IPv6 Pool Test
    # ============================================================
    def _test_pool(self):
        url = self._pool_url.get().strip()
        self._pool_status.config(text="Dang ket noi...", fg=YELLOW)
        self.update()

        def _do():
            try:
                from ipv6_pool_client import IPv6PoolClient
                client = IPv6PoolClient(url, timeout=5)
                status = client.get_status()
                if status:
                    avail = status.get('available', '?')
                    total = status.get('total', '?')
                    self._pool_status.config(text=f"OK - Available: {avail}/{total}", fg=GREEN)
                else:
                    self._pool_status.config(text="Khong ket noi duoc!", fg=RED)
            except Exception as e:
                self._pool_status.config(text=f"Loi: {e}", fg=RED)

        threading.Thread(target=_do, daemon=True).start()

    # ============================================================
    # Start / Stop
    # ============================================================
    def _cancel_autostart(self):
        if self._autostart_id:
            self.after_cancel(self._autostart_id)
            self._autostart_id = None
        self._start_btn.config(text="START FLOWKIT")

    def _autostart_tick(self):
        if self._started:
            return
        self._autostart_remaining -= 1
        if self._autostart_remaining <= 0:
            self._autostart_id = None
            self._on_start()
            return
        self._start_btn.config(text=f"START FLOWKIT ({self._autostart_remaining}s)")
        self._autostart_id = self.after(1000, self._autostart_tick)

    def _on_start(self):
        self._cancel_autostart()
        if self._started:
            return
        self._save_settings()
        self._started = True
        self._start_btn.config(state='disabled', bg='#475569')
        self._stop_btn.config(state='normal')
        self._notebook.select(self._monitor_frame)

        # Schedule auto-restart
        self._restart_timer_id = None
        self._restart_at = 0
        if self._auto_restart_var.get():
            hours = int(self._restart_hours.get() or 3)
            self._restart_at = time.time() + hours * 3600
            self._restart_timer_id = self.after(hours * 3600 * 1000, self._scheduled_restart)
            self._log(f"Starting FlowKit Server... (auto-restart sau {hours}h)", "INFO")
        else:
            self._log("Starting FlowKit Server...", "INFO")

        # Update config.yaml with GUI settings
        self._update_config()

        # Parse accounts
        accounts = self._parse_accounts()

        threading.Thread(target=self._start_all, args=(accounts,), daemon=True).start()

    @staticmethod
    def _default_config() -> dict:
        """Baseline config used when config.yaml is missing/corrupt (self-heal)."""
        return {
            'gateway_host': '0.0.0.0',
            'gateway_port': 5100,
            'chrome_layout': {'cols': 2, 'gui_width': 700, 'rows': 0, 'zoom': 50},
            'ipv6': {'enabled': False, 'pool_url': '', 'prefix_length': 56, 'socks_port': 1080},
            'quota': {'cooldown_seconds': 120, 'retry_count': 3, 'retry_delay': 15},
            'rate_limit': {'cooldown_per_instance': 5, 'max_concurrent_per_instance': 1},
            'recovery': {'chrome_restart_delay': 5, 'extension_reconnect_timeout': 30,
                         'level1_max_attempts': 2, 'level2_max_attempts': 2,
                         'level3_max_attempts': 3, 'min_recovery_interval': 30},
            'rotation': {'cooldown_seconds': 300, 'max_consecutive_403': 3,
                         'max_retries_per_request': 3},
            'timeouts': {'health_check': 5, 'image_generation': 120, 'video_poll': 420,
                         'video_poll_interval': 10, 'video_submit': 60},
            'instances': [],
        }

    def _update_config(self):
        """Update config.yaml with current GUI settings (UTF-8, self-heals if corrupt)."""
        import yaml
        config_path = BASE_DIR / "config.yaml"
        config = None
        try:
            with open(config_path, encoding="utf-8-sig") as f:
                config = yaml.safe_load(f)
        except Exception as e:
            self._log(f"config.yaml unreadable ({e}) -> regenerating from defaults", "WARN")
        if not isinstance(config, dict):
            config = self._default_config()
        # Ensure required sections exist (in case of partial/old config)
        for k, v in self._default_config().items():
            if k != 'instances':
                config.setdefault(k, v)

        config['gateway_port'] = int(self._gateway_port.get() or 5100)
        config['rotation']['max_consecutive_403'] = int(self._max_403.get() or 3)
        config['rotation']['cooldown_seconds'] = int(self._cooldown.get() or 300)

        ipv6_cfg = config.get('ipv6', {})
        ipv6_cfg['enabled'] = self._ipv6_enabled.get()
        ipv6_cfg['pool_url'] = self._pool_url.get().strip()
        config['ipv6'] = ipv6_cfg

        # Fixed Account config
        fa_enabled = self._fixed_account_var.get()
        fa_concurrent = int(self._fa_concurrent.get() or 2)
        config['fixed_account'] = {
            'enabled': fa_enabled,
            'concurrent': fa_concurrent,
            'cooldown_seconds': int(self._cooldown.get() or 300),
        }

        # Update instance count based on detected Chromes + chrome_count limit
        chrome_count = 0
        try:
            val = self._chrome_count_combo.get()
            if val != "Tat ca":
                chrome_count = int(val)
        except Exception:
            pass

        instances = []
        for i, chrome_dir in enumerate(self._chrome_dirs):
            enabled = True
            if fa_enabled:
                enabled = True
            elif chrome_count > 0 and i >= chrome_count:
                enabled = False
            instances.append({
                'name': f'flowkit-{i + 1}',
                'chrome_path': f'{chrome_dir.name}/App/Chrome-bin/chrome.exe',
                'profile_dir': f'{chrome_dir.name}/Data/profile',
                'extension_dir': f'flowkit_extensions/ext_{8100 + i}',
                'api_port': 8100 + i,
                'ws_port': 9222 + i,
                'enabled': enabled,
            })
        config['instances'] = instances

        # Atomic write in UTF-8 (avoid cp1252 + half-written/corrupt files)
        tmp_path = config_path.with_suffix(".yaml.tmp")
        with open(tmp_path, 'w', encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        os.replace(str(tmp_path), str(config_path))

    def _parse_accounts(self) -> list:
        """Parse accounts from text field. Format: email|password|2fa_secret"""
        raw = self._accounts_text.get('1.0', 'end').strip()
        accounts = []
        for line in raw.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('|')
            if len(parts) >= 2:
                acc = {
                    'email': parts[0].strip(),
                    'password': parts[1].strip(),
                    'totp_secret': parts[2].strip() if len(parts) > 2 else '',
                }
                accounts.append(acc)
        return accounts

    def _start_all(self, accounts: list):
        """Start Chrome instances + agents + gateway in background."""
        import yaml
        config_path = BASE_DIR / "config.yaml"
        with open(config_path) as f:
            config = yaml.safe_load(f)

        instances = config.get('instances', [])
        gateway_port = config.get('gateway_port', 5100)
        use_ipv6 = config.get('ipv6', {}).get('enabled', False)
        pool_url = config.get('ipv6', {}).get('pool_url', '')

        LOG_DIR.mkdir(exist_ok=True)

        # IPv6 Pool Client
        pool_client = None
        if use_ipv6 and pool_url:
            try:
                from ipv6_pool_client import IPv6PoolClient
                pool_client = IPv6PoolClient(pool_url, log_func=lambda m: self._log(m, "INFO"))
                if pool_client.ping():
                    self._log(f"IPv6 Pool connected: {pool_url}", "OK")
                else:
                    self._log(f"IPv6 Pool unreachable: {pool_url}", "WARN")
                    pool_client = None
            except Exception as e:
                self._log(f"IPv6 Pool error: {e}", "ERROR")

        # Fixed Account mode
        fa_cfg = config.get('fixed_account', {})
        fa_enabled = fa_cfg.get('enabled', False)
        fa_concurrent = fa_cfg.get('concurrent', 2)

        # Chrome count limiting — BEFORE IPv6 to only request IPs we need
        chrome_count = 0
        try:
            val = self._chrome_count_combo.get()
            if val != "Tat ca":
                chrome_count = int(val)
        except Exception:
            pass
        if fa_enabled:
            for inst in instances:
                inst['enabled'] = True
            self._log(f"Fixed Account mode: ALL {len(instances)} instances enabled, {fa_concurrent} concurrent", "INFO")
        elif chrome_count > 0:
            for i, inst in enumerate(instances):
                inst['enabled'] = (i < chrome_count)
            self._log(f"Chrome count limited to {chrome_count}/{len(instances)}", "INFO")
        self._settings['chrome_count'] = chrome_count
        self._save_settings()

        # Get IPv6 addresses and start per-instance SOCKS5 proxies
        ipv6_map = {}
        proxy_port_map = {}
        PROXY_BASE_PORT = 1081
        if pool_client:
            enabled_names = [inst['name'] for inst in instances if inst.get('enabled', True)]
            self._log(f"Getting IPv6 for {len(enabled_names)} instances: {', '.join(enabled_names)}", "INFO")
            for i, inst in enumerate(instances):
                if not inst.get('enabled', True) or i >= len(self._chrome_dirs):
                    continue
                try:
                    result = pool_client.get_ip(worker=f"flowkit_{inst['name']}")
                except Exception as e:
                    self._log(f"[{inst['name']}] IPv6 pool error: {e}", "ERROR")
                    result = None
                if result:
                    ipv6_map[i] = {
                        'ip': result['ip'],
                        'gateway': result.get('gateway', ''),
                    }
                    self._log(f"[{inst['name']}] Got IPv6: {result['ip']}", "OK")
                else:
                    # Fallback: try existing IPv6 override file
                    proxy_port = PROXY_BASE_PORT + (inst['api_port'] - 8100)
                    override_file = BASE_DIR / f".ipv6_override_{proxy_port}"
                    if override_file.exists():
                        try:
                            old_ip = override_file.read_text().strip()
                        except Exception:
                            old_ip = ""
                        if old_ip:
                            ipv6_map[i] = {'ip': old_ip, 'gateway': ''}
                            self._log(f"[{inst['name']}] Pool no IP → fallback: {old_ip}", "WARN")
                    if i not in ipv6_map:
                        self._log(f"[{inst['name']}] No IPv6 available — will use direct connection", "WARN")

            if ipv6_map:
                self._log(f"Setting up SOCKS5 proxies ({len(ipv6_map)} IPs)...", "INFO")
                self._setup_ipv6_proxies(ipv6_map, instances, PROXY_BASE_PORT, proxy_port_map,
                                        pool_url=pool_url, pool_client=pool_client)
            else:
                self._log("WARNING: No IPv6 for any instance — using direct connection", "WARN")

        # Build account map — account 1 = Chrome 1, account 2 = Chrome 2, fixed
        enabled = [(i, inst) for i, inst in enumerate(instances)
                   if inst.get('enabled', True) and i < len(self._chrome_dirs)]
        # Sync the LOGIN window layout to the running-Chrome (Flow) layout so both have
        # the same position + full height. Same slot count (real # of Chromes, not the
        # 6 enabled in config), same columns, same left reserve as launcher chrome_layout.
        try:
            from launcher import CONFIG as _lcfg
            _cl = _lcfg.get("chrome_layout", {})
            os.environ["CHROME_LAYOUT_SLOTS"] = str(max(1, len(enabled)))
            os.environ["CHROME_LAYOUT_COLS"] = str(_cl.get("cols", 2))
            os.environ["CHROME_LAYOUT_LEFT_RESERVED"] = str(_cl.get("gui_width", 700))
        except Exception:
            os.environ["CHROME_LAYOUT_SLOTS"] = str(max(1, len(enabled)))
        account_map = {}
        if accounts:
            for i, inst in enumerate(instances):
                if not inst.get('enabled', True):
                    continue
                acc = accounts[i % len(accounts)]
                account_map[inst["name"]] = {
                    "id": acc["email"], "password": acc["password"],
                    "totp_secret": acc.get("totp_secret", ""),
                }
        try:
            accounts_file = BASE_DIR / "config" / ".flow_accounts.json"
            accounts_file.parent.mkdir(exist_ok=True)
            accounts_file.write_text(json.dumps(account_map), encoding="utf-8")
        except Exception:
            pass

        # Per-instance pipeline
        setup_concurrency = max(1, int(os.getenv("CHROME_SETUP_CONCURRENCY", "6")))
        setup_stagger = max(0.0, float(os.getenv("CHROME_SETUP_STAGGER_SEC", "3.0")))

        setup_sem = threading.Semaphore(setup_concurrency)
        ready_count = [0]
        ready_lock = threading.Lock()
        ready_event = threading.Event()

        self._log(f"Starting {len(enabled)} instance pipelines (concurrency={setup_concurrency})...", "INFO")

        def _instance_pipeline(idx, inst_cfg):
            name = inst_cfg['name']
            chrome_dir = self._chrome_dirs[idx]
            ext_dir = BASE_DIR / inst_cfg['extension_dir']
            debug_port = 19200 + (inst_cfg['api_port'] - 8100)
            proxy_arg = f"socks5://127.0.0.1:{proxy_port_map[idx]}" if idx in proxy_port_map else ""

            account_info = None
            if accounts:
                acc = accounts[idx % len(accounts)]
                account_info = {
                    'id': acc['email'],
                    'password': acc['password'],
                    'totp_secret': acc.get('totp_secret', ''),
                }

            win_args = []
            try:
                from launcher import _calc_chrome_layout, _resolve_chrome_slot, CONFIG as _lcfg
                instances_cfg = [ii for ii in _lcfg.get("instances", []) if ii.get("enabled", True)]
                slot = _resolve_chrome_slot(name)
                x, y, w, h = _calc_chrome_layout(slot, max(1, len(enabled)))
                win_args = [f"--window-position={x},{y}", f"--window-size={w},{h}"]
            except Exception:
                pass

            # ── Step 1: DrissionPage setup (login + navigate + project) ──
            # Retry with different accounts if login fails (like old server)
            setup_ok = False
            tried_accounts = set()
            attempt = 0
            with setup_sem:
                while not setup_ok:
                    if not self._started:
                        return
                    attempt += 1
                    if fa_enabled and attempt > 5:
                        self._log(f"[{name}] Setup FAILED after 5 attempts — skipped", "ERROR")
                        break
                    if attempt > 1:
                        from chrome_setup import _kill_chrome_for_dir
                        _kill_chrome_for_dir(chrome_dir)
                        # FA mode: NEVER rotate accounts — each Chrome = fixed account
                        if not fa_enabled and account_info and accounts and len(accounts) > 1:
                            old_email = account_info['id']
                            tried_accounts.add(old_email)
                            new_acc = None
                            for a in accounts:
                                if a['email'] not in tried_accounts:
                                    new_acc = a
                                    break
                            if new_acc:
                                account_info = {
                                    'id': new_acc['email'],
                                    'password': new_acc['password'],
                                    'totp_secret': new_acc.get('totp_secret', ''),
                                }
                                self._log(f"[{name}] Login fail → doi account: {old_email} → {new_acc['email']}", "WARN")
                            elif len(tried_accounts) >= len(accounts):
                                tried_accounts.clear()
                                self._log(f"[{name}] Het account, cho 60s roi thu lai tu dau...", "WARN")
                                time.sleep(60)
                                continue
                        delay = min(10 * attempt, 60)
                        self._log(f"[{name}] Retry setup (lan {attempt}), cho {delay}s...", "WARN")
                        time.sleep(delay)
                    try:
                        from chrome_setup import setup_chrome
                        ok = setup_chrome(
                            chrome_dir=chrome_dir,
                            ext_dir=ext_dir,
                            port=debug_port,
                            account=account_info,
                            proxy_arg=proxy_arg,
                            window_args=win_args,
                            log_func=lambda msg, n=name: self._log(f"[{n}] {msg}", "INFO"),
                            instance_name=name,
                        )
                        if ok:
                            self._log(f"[{name}] Chrome setup OK (account: {account_info['id'] if account_info else '?'})", "OK")
                            setup_ok = True
                    except Exception as e:
                        self._log(f"[{name}] Setup error: {e}", "ERROR")

            # ── Step 2: Start agent (port 9222+i now free) ──
            if not self._started:
                return
            self._start_agent(inst_cfg)
            if self._wait_agent_ready(inst_cfg['api_port'], timeout=20):
                self._log(f"[{name}] Agent ready", "OK")
            else:
                self._log(f"[{name}] Agent not ready after 20s", "WARN")

            # ── Step 3: Start Chrome subprocess + apply CDP ──
            # Fixed Account: only start Chrome for first M instances (standby = no Chrome)
            is_active = not fa_enabled or idx < fa_concurrent
            if is_active and not setup_ok:
                self._log(f"[{name}] Setup FAILED — skip Chrome start, agent only", "ERROR")
                is_active = False
            if is_active:
                self._start_chrome(chrome_dir, inst_cfg, proxy_arg)
                time.sleep(5)
                try:
                    from chrome_setup import apply_chrome_cdp
                    apply_chrome_cdp(
                        debug_port=debug_port,
                        ext_dir=ext_dir,
                        instance_name=name,
                        window_args=win_args,
                        log_func=lambda msg, n=name: self._log(f"[{n}] {msg}", "INFO"),
                    )
                except Exception as e:
                    self._log(f"[{name}] CDP apply error: {e}", "WARN")

                # Minimize Chrome to save GPU/RAM (extension works when minimized)
                try:
                    from DrissionPage import ChromiumPage, ChromiumOptions
                    _co = ChromiumOptions()
                    _co.set_address(f"127.0.0.1:{debug_port}")
                    _p = ChromiumPage(_co)
                    _p.run_cdp('Browser.getWindowForTarget')
                    info = _p.run_cdp('Browser.getWindowForTarget')
                    wid = info.get('windowId')
                    if wid:
                        _p.run_cdp('Browser.setWindowBounds', windowId=wid,
                                   bounds={'windowState': 'minimized'})
                    try:
                        _p.disconnect()
                    except Exception:
                        pass
                except Exception:
                    pass
                self._log(f"[{name}] Instance READY (active)", "OK")
            else:
                self._log(f"[{name}] Instance STANDBY (Chrome not started, agent ready)", "OK")
            if setup_ok:
                with ready_lock:
                    ready_count[0] += 1
            ready_event.set()

        # Launch all pipelines with stagger
        pipeline_threads = []
        for i, inst in enabled:
            t = threading.Thread(target=_instance_pipeline, args=(i, inst), daemon=True)
            t.start()
            pipeline_threads.append(t)
            if setup_stagger > 0:
                time.sleep(setup_stagger)

        if fa_enabled:
            for t in pipeline_threads:
                t.join(timeout=300)
            if not self._started:
                self._log("Startup aborted (stopped during setup)", "WARN")
                return
            with ready_lock:
                total_ready = ready_count[0]
            self._log(f"All {total_ready} instances setup done. Starting Gateway...", "OK")
            self._start_gateway(gateway_port)
            self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._poll_thread.start()
        else:
            if ready_event.wait(timeout=120):
                self._log("Starting Gateway (instance ready)...", "OK")
            else:
                self._log("Starting Gateway (timeout — no instances ready yet)...", "WARN")
            if not self._started:
                self._log("Startup aborted (stopped during setup)", "WARN")
                return
            self._start_gateway(gateway_port)
            self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._poll_thread.start()
            for t in pipeline_threads:
                t.join(timeout=180)
            with ready_lock:
                total_ready = ready_count[0]

        self._log(f"FlowKit Server READY — {total_ready}/{len(enabled)} instances, gateway on port {gateway_port}", "OK")

    def _clear_chrome_profile(self, chrome_dir: Path, inst_name: str):
        """Xoa toan bo du lieu Chrome profile de login lai tu dau.
        Extension khong bi anh huong vi load tu thu muc ngoai (--load-extension)."""
        profile_default = chrome_dir / "Data" / "profile" / "Default"
        if profile_default.exists():
            shutil.rmtree(str(profile_default), ignore_errors=True)
        self._log(f"[{inst_name}] Cleared Chrome profile data", "INFO")

    def _do_chrome_login(self, chrome_dir: Path, account: dict, inst: dict, proxy_arg: str = ""):
        """Login Google account into Chrome profile using google_login.py."""
        try:
            login_module = BASE_DIR / "google_login.py"
            if not login_module.exists():
                login_module = TOOL_DIR / "google_login.py"
            if not login_module.exists():
                self._log(f"[{inst['name']}] google_login.py not found, skip login", "WARN")
                return

            sys.path.insert(0, str(login_module.parent))
            from google_login import login_google_chrome

            portable = str(chrome_dir / "GoogleChromePortable.exe")
            worker_id = inst['api_port'] - 8100

            account_info = {
                'id': account['email'],
                'password': account['password'],
                'totp_secret': account.get('totp_secret', ''),
            }

            success = login_google_chrome(
                account_info=account_info,
                chrome_portable=portable,
                worker_id=worker_id,
                proxy_arg=proxy_arg,
            )

            if success:
                self._log(f"[{inst['name']}] Login OK: {account['email']}", "OK")
            else:
                self._log(f"[{inst['name']}] Login FAILED, clearing profile and retrying...", "WARN")
                self._clear_chrome_profile(chrome_dir, inst['name'])
                success = login_google_chrome(
                    account_info=account_info,
                    chrome_portable=portable,
                    worker_id=worker_id,
                    proxy_arg=proxy_arg,
                )
                if success:
                    self._log(f"[{inst['name']}] Retry Login OK: {account['email']}", "OK")
                else:
                    self._log(f"[{inst['name']}] Retry Login FAILED: {account['email']}", "ERROR")

        except Exception as e:
            self._log(f"[{inst['name']}] Login error: {e}", "ERROR")

    def _start_chrome(self, chrome_dir: Path, inst: dict, proxy_arg: str = ""):
        """Start Chrome with extension, navigate to Flow page to activate extension."""
        portable = chrome_dir / "GoogleChromePortable.exe"
        ext_dir = BASE_DIR / inst['extension_dir']

        # Clear stale service worker cache to ensure fresh extension loading
        profile_dir = chrome_dir / "Data" / "profile"
        sw_cache = profile_dir / "Default" / "Service Worker"
        if sw_cache.exists():
            try:
                shutil.rmtree(sw_cache)
            except Exception:
                pass

        # Write fresh Preferences (zoom 50%, session restore prevention)
        try:
            from launcher import _write_chrome_prefs
            _write_chrome_prefs(chrome_dir)
        except Exception:
            pass

        # Regenerate fingerprint (includes zoom CSS) before launch
        try:
            from launcher import generate_fingerprint
            generate_fingerprint(ext_dir, inst["name"])
        except Exception:
            pass

        debug_port = 19200 + (inst["api_port"] - 8100)
        args = [
            str(portable),
            f"--load-extension={ext_dir}",
            f"--remote-debugging-port={debug_port}",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "--no-first-run",
            "--no-default-browser-check",
        ]

        # Window layout — same config as launcher
        try:
            from launcher import _calc_chrome_layout, _resolve_chrome_slot, CONFIG as _lcfg
            instances_cfg = [i for i in _lcfg.get("instances", []) if i.get("enabled", True)]
            slot = _resolve_chrome_slot(inst["name"])
            x, y, w, h = _calc_chrome_layout(slot, max(1, len(self._chrome_dirs)))
            args.append(f"--window-position={x},{y}")
            args.append(f"--window-size={w},{h}")
        except Exception:
            pass

        if proxy_arg:
            args.append(f"--proxy-server={proxy_arg}")
        # Open directly to Flow page so extension can capture flow key
        args.append("https://labs.google/fx/tools/flow")

        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        with self._proc_lock:
            self._processes.append(proc)
        log_extra = f" proxy={proxy_arg}" if proxy_arg else ""
        self._log(f"[{inst['name']}] Chrome started (PID {proc.pid}){log_extra}", "INFO")

    def _setup_ipv6_proxies(self, ipv6_map: dict, instances: list, base_port: int, proxy_port_map: dict,
                            pool_url: str = "", pool_client=None):
        """Add IPv6 addresses to interface and start a SOCKS5 proxy per instance.

        ipv6_map: {index: {'ip': '2001:...', 'gateway': '2001:...:1'}}
        """
        try:
            from modules.ipv6_proxy import IPv6SocksProxy
        except ImportError:
            try:
                from ipv6_proxy import IPv6SocksProxy
            except ImportError:
                self._log("ipv6_proxy module not found, cannot start SOCKS5 proxies", "ERROR")
                return

        iface = "Ethernet"
        self._ipv6_proxies = []
        first_gateway = None

        # Step 1: Add addresses + per-subnet routes
        for i, info in ipv6_map.items():
            ipv6 = info['ip']
            gateway = info.get('gateway', '') or self._compute_ipv6_gateway(ipv6)
            name = instances[i]['name'] if i < len(instances) else f"flowkit-{i}"
            if not first_gateway and gateway:
                first_gateway = gateway

            # Add IPv6 address
            try:
                subprocess.run(f'netsh interface ipv6 add address "{iface}" {ipv6}',
                               shell=True, capture_output=True, timeout=10)
                self._log(f"[{name}] Added {ipv6} to {iface}", "INFO")
            except Exception as e:
                self._log(f"[{name}] netsh add address failed: {e}", "WARN")

            # Add on-link route for THIS subnet (each instance needs its own)
            onlink_prefix = ':'.join(gateway.split(':')[:4]) + '::/64'
            try:
                subprocess.run(f'netsh interface ipv6 add route {onlink_prefix} "{iface}" {gateway}',
                               shell=True, capture_output=True, timeout=10)
                self._log(f"[{name}] Route {onlink_prefix} via {gateway}", "OK")
            except Exception:
                pass

        # Default route (once)
        if first_gateway:
            try:
                subprocess.run(f'netsh interface ipv6 add route ::/0 "{iface}" {first_gateway}',
                               shell=True, capture_output=True, timeout=10)
                self._log(f"Default route ::/0 via {first_gateway}", "OK")
            except Exception:
                pass

        # Firewall: allow ICMPv6 NDP
        try:
            subprocess.run('netsh advfirewall firewall add rule name="ICMPv6-NDP-In" dir=in action=allow protocol=icmpv6',
                           shell=True, capture_output=True, timeout=5)
            subprocess.run('netsh advfirewall firewall add rule name="ICMPv6-NDP-Out" dir=out action=allow protocol=icmpv6',
                           shell=True, capture_output=True, timeout=5)
        except Exception:
            pass

        # Step 2: Wait for NDP — per-IP readiness check
        import socket
        self._ndp_threads = getattr(self, '_ndp_threads', [])
        ready_ips = {}

        for i, info in ipv6_map.items():
            ipv6 = info['ip']
            gateway = info.get('gateway', '') or self._compute_ipv6_gateway(ipv6)
            name = instances[i]['name'] if i < len(instances) else f"flowkit-{i}"

            # Ping gateway to force NDP resolve
            if gateway:
                try:
                    subprocess.run(f'ping -6 -n 2 -w 3000 -S {ipv6} {gateway}',
                                   shell=True, capture_output=True, timeout=10)
                except Exception:
                    pass

            # Wait for address to become "Preferred" (DAD complete)
            ip_ready = False
            for attempt in range(15):
                time.sleep(1)
                try:
                    result = subprocess.run(
                        f'netsh interface ipv6 show address "{iface}"',
                        shell=True, capture_output=True, text=True, timeout=5)
                    if ipv6 in result.stdout and "Preferred" in result.stdout:
                        # Test actual connectivity
                        try:
                            s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                            s.settimeout(3)
                            s.bind((ipv6, 0))
                            s.connect(("2001:4860:4860::8888", 53))
                            s.close()
                            ip_ready = True
                            self._log(f"[{name}] IPv6 ready ({attempt+1}s)", "OK")
                            break
                        except OSError:
                            try: s.close()
                            except: pass
                except Exception:
                    pass

            if not ip_ready:
                self._log(f"[{name}] IPv6 {ipv6} not ready after 15s — rotate", "WARN")
                # Auto-rotate: ask pool for new IP
                if pool_client:
                    try:
                        new_result = pool_client.rotate_ip(ipv6, reason="ndp_fail", worker=name)
                        if new_result and new_result.get('ip') != ipv6:
                            new_ip = new_result['ip']
                            new_gw = new_result.get('gateway', '') or self._compute_ipv6_gateway(new_ip)
                            subprocess.run(f'netsh interface ipv6 delete address "{iface}" {ipv6}',
                                           shell=True, capture_output=True, timeout=10)
                            subprocess.run(f'netsh interface ipv6 add address "{iface}" {new_ip}',
                                           shell=True, capture_output=True, timeout=10)
                            new_prefix = ':'.join(new_gw.split(':')[:4]) + '::/64'
                            subprocess.run(f'netsh interface ipv6 add route {new_prefix} "{iface}" {new_gw}',
                                           shell=True, capture_output=True, timeout=10)
                            subprocess.run(f'ping -6 -n 2 -w 3000 -S {new_ip} {new_gw}',
                                           shell=True, capture_output=True, timeout=10)
                            time.sleep(2)
                            ipv6_map[i] = {'ip': new_ip, 'gateway': new_gw}
                            self._log(f"[{name}] Rotated: {ipv6} → {new_ip}", "OK")
                            ip_ready = True
                    except Exception as e:
                        self._log(f"[{name}] Rotate failed: {e}", "WARN")

            ready_ips[i] = ip_ready

            # Start NDP keepalive (shared module — restartable on rotate)
            cur_ip = ipv6_map[i]['ip']
            cur_gw = ipv6_map[i].get('gateway', '') or self._compute_ipv6_gateway(cur_ip)
            port = base_port + i
            if cur_gw:
                try:
                    from ipv6_proxy import start_ndp_keepalive
                    start_ndp_keepalive(cur_ip, cur_gw, port, lambda m: self._log(m, "INFO"))
                except ImportError:
                    # Fallback: inline NDP keepalive if ipv6_proxy is old version
                    def _ndp_loop(src=cur_ip, gw=cur_gw):
                        while self._started:
                            try:
                                subprocess.run(f'ping -6 -n 1 -w 3000 -S {src} {gw}',
                                               shell=True, capture_output=True, timeout=10)
                            except Exception:
                                pass
                            time.sleep(20)
                    threading.Thread(target=_ndp_loop, daemon=True).start()

        # Step 3: Start proxy for ready IPs
        for i, info in ipv6_map.items():
            if not ready_ips.get(i):
                name = instances[i]['name'] if i < len(instances) else f"flowkit-{i}"
                self._log(f"[{name}] Skip proxy — IPv6 not ready", "WARN")
                continue

            ipv6 = info['ip']
            name = instances[i]['name'] if i < len(instances) else f"flowkit-{i}"
            port = base_port + i

            proxy = IPv6SocksProxy(
                listen_port=port,
                ipv6_address=ipv6,
                log_func=lambda m, _n=name: self._log(f"[{_n}] {m}", "INFO")
            )
            proxy.pool_url = pool_url
            proxy.worker_name = name
            if proxy.start():
                proxy_port_map[i] = port
                self._ipv6_proxies.append(proxy)
                self._log(f"[{name}] SOCKS5 proxy on 127.0.0.1:{port} → {ipv6}", "OK")
            else:
                self._log(f"[{name}] Failed to start proxy on port {port}", "ERROR")

    def _compute_ipv6_gateway(self, ipv6: str) -> str:
        """Compute gateway address from IPv6 (::1 of the /64 prefix)."""
        try:
            import ipaddress
            addr = ipaddress.IPv6Address(ipv6)
            network = ipaddress.IPv6Network(f"{addr}/64", strict=False)
            return str(network.network_address + 1)
        except Exception:
            return ""

    def _rotate_log_file(self, log_file: Path):
        """Rename old log to .bak before creating new one. Keeps 1 backup."""
        if log_file.exists():
            bak = log_file.with_suffix('.log.bak')
            try:
                if bak.exists():
                    bak.unlink()
                log_file.rename(bak)
            except Exception:
                pass

    def _start_agent(self, inst: dict):
        """Start FlowKit agent for an instance."""
        env = os.environ.copy()
        env['API_PORT'] = str(inst['api_port'])
        env['WS_PORT'] = str(inst['ws_port'])
        env['INSTANCE_NAME'] = inst['name']

        # Close old log handle if restarting (prevent handle leak)
        old_fh = self._agent_log_fhs.pop(inst['name'], None)
        if old_fh:
            try:
                old_fh.close()
            except Exception:
                pass

        log_file = LOG_DIR / f"{inst['name']}.log"
        self._rotate_log_file(log_file)
        fh = open(log_file, 'w', encoding='utf-8')
        self._agent_log_fhs[inst['name']] = fh
        with self._proc_lock:
            self._log_handles.append(fh)

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "agent.main:app",
             "--host", "127.0.0.1", "--port", str(inst['api_port']),
             "--log-level", "info"],
            env=env, cwd=str(BASE_DIR),
            stdout=fh, stderr=fh,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        with self._proc_lock:
            self._processes.append(proc)
        self._agent_procs[inst['name']] = proc
        self._inst_configs[inst['name']] = inst
        self._log(f"[{inst['name']}] Agent started: port {inst['api_port']} (PID {proc.pid})", "INFO")

    def _wait_agent_ready(self, api_port: int, timeout: float = 20.0) -> bool:
        """Poll agent /health endpoint until it responds."""
        import urllib.request
        url = f"http://127.0.0.1:{api_port}/health"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        return True
            except Exception:
                pass
            time.sleep(1.0)
        return False

    def _start_gateway(self, port: int):
        """Start the gateway."""
        # Close old log handle if restarting
        if self._gateway_log_fh:
            try:
                self._gateway_log_fh.close()
            except Exception:
                pass
        # Stop old tail thread via generation counter
        self._gateway_tail_gen += 1

        log_file = LOG_DIR / "gateway.log"
        self._rotate_log_file(log_file)
        fh = open(log_file, 'w', encoding='utf-8')
        self._gateway_log_fh = fh
        with self._proc_lock:
            self._log_handles.append(fh)

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "gateway:app",
             "--host", "0.0.0.0", "--port", str(port),
             "--log-level", "info"],
            cwd=str(BASE_DIR),
            stdout=fh, stderr=fh,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        with self._proc_lock:
            self._processes.append(proc)
        self._gateway_proc = proc
        self._gateway_port_val = port
        self._log(f"Gateway started: port {port} (PID {proc.pid})", "INFO")

        # Tail gateway.log to GUI — generation counter prevents duplicate threads
        my_gen = self._gateway_tail_gen
        def _tail_gateway_log():
            try:
                with open(log_file, 'r', encoding='utf-8', errors='replace') as rf:
                    rf.seek(0, 2)
                    while self._started and self._gateway_tail_gen == my_gen:
                        lines = rf.readlines()
                        for line in lines[-10:]:
                            line = line.strip()
                            if line and any(k in line.upper() for k in [
                                'RECOVERY', 'SELFHEAL', 'COOLING', 'ROTATE',
                                'ACCOUNT', '403', '429', 'QUOTA', 'FAILED', 'ERROR'
                            ]):
                                self._log(f"[GW] {line}", "WARN" if 'ERROR' in line.upper() or 'FAIL' in line.upper() else "INFO")
                        time.sleep(5)
            except Exception:
                pass
        threading.Thread(target=_tail_gateway_log, daemon=True).start()

    def _scheduled_restart(self):
        """Auto-restart: kill everything → restart GUI process (clean memory)."""
        self._restart_timer_id = None
        self._restart_at = 0
        hours = int(self._restart_hours.get() or 3)
        self._log(f"=== AUTO-RESTART ({hours}h) — kill all + restart GUI ===", "WARN")
        self._on_stop()
        self._start_btn.config(state='disabled', bg='#475569', text="Restarting...")
        self._stop_btn.config(state='disabled')
        self._restart_countdown_label.config(text="Cho 15s...", fg=YELLOW)
        self._pending_restart_id = self.after(15000, self._exec_restart)

    def _exec_restart(self):
        """Start new GUI process then exit. Safe on Windows (no process chaining)."""
        self._pending_restart_id = None
        try:
            subprocess.Popen([sys.executable] + sys.argv,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        except Exception as e:
            self._log(f"[RESTART] failed: {e}", "ERROR")
        os._exit(0)

    def _on_stop(self):
        """Stop all processes."""
        # Cancel all pending restart timers
        for attr in ('_restart_timer_id', '_pending_restart_id'):
            tid = getattr(self, attr, None)
            if tid:
                self.after_cancel(tid)
                setattr(self, attr, None)
        self._restart_at = 0

        self._log("Stopping FlowKit Server...", "WARN")
        self._started = False

        # Kill managed subprocesses (agents, gateway)
        for proc in self._processes:
            try:
                proc.kill()
            except Exception:
                pass
        self._processes.clear()

        # Close log file handles
        for fh in self._log_handles:
            try:
                fh.close()
            except Exception:
                pass
        self._log_handles.clear()
        self._gateway_log_fh = None
        self._gateway_proc = None
        self._gateway_tail_gen += 1
        self._agent_log_fhs.clear()
        self._agent_procs.clear()
        self._restart_times.clear()

        # Stop IPv6 proxies (release ports for restart)
        for proxy in getattr(self, '_ipv6_proxies', []):
            try:
                proxy.stop()
            except Exception:
                pass
        self._ipv6_proxies = []
        # Delete override files (prevent stale IP on next start)
        for f in BASE_DIR.glob(".ipv6_override_*"):
            try:
                f.unlink()
            except Exception:
                pass

        # Stop NDP keepalive threads
        try:
            from ipv6_proxy import stop_ndp_keepalive
            for port in range(1081, 1111):
                stop_ndp_keepalive(port)
        except Exception:
            pass

        self._kill_all_chrome()
        self._kill_flowkit_python()

        self._start_btn.config(state='normal', bg=GREEN)
        self._stop_btn.config(state='disabled')
        self._log("FlowKit Server stopped.", "WARN")

    def _kill_all_chrome(self):
        """Kill all GoogleChromePortable chrome.exe — dual strategy like old server."""
        # Step 1: WMIC terminate
        try:
            subprocess.run(
                ['wmic', 'process', 'where',
                 "name='chrome.exe' and CommandLine like '%GoogleChromePortable%'",
                 'call', 'terminate'],
                capture_output=True, text=True, timeout=10,
                creationflags=0x08000000,
            )
        except Exception:
            pass

        # Step 2: taskkill /F /T tree kill (catches child processes WMIC missed)
        try:
            proc = subprocess.run(
                ['wmic', 'process', 'where',
                 "name='chrome.exe' and CommandLine like '%GoogleChromePortable%'",
                 'get', 'ProcessId', '/FORMAT:CSV'],
                capture_output=True, text=True, timeout=8,
                creationflags=0x08000000,
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
                            creationflags=0x08000000,
                        )
                    except Exception:
                        pass
        except Exception:
            pass

        # Step 3: Kill GoogleChromePortable.exe launcher itself
        try:
            subprocess.run(
                ['taskkill', '/F', '/IM', 'GoogleChromePortable.exe'],
                capture_output=True, timeout=5,
                creationflags=0x08000000,
            )
        except Exception:
            pass

    def _kill_flowkit_python(self):
        """Kill FlowKit agents + gateway by port (precise, won't kill other tools)."""
        CF = 0x08000000
        gw_port = getattr(self, '_gateway_port_val', 5100)
        try:
            result = subprocess.run(
                'netstat -ano -p tcp',
                shell=True, capture_output=True, text=True, timeout=10,
                creationflags=CF)
            my_pid = str(os.getpid())
            pids = set()
            for line in result.stdout.splitlines():
                if 'LISTENING' not in line:
                    continue
                parts = line.split()
                if len(parts) < 5:
                    continue
                try:
                    port = int(parts[1].rsplit(':', 1)[1])
                except Exception:
                    continue
                if port == gw_port or 8100 <= port <= 8129:
                    pid = parts[-1]
                    if pid != my_pid and pid != '0':
                        pids.add(pid)
            for pid in pids:
                try:
                    subprocess.run(['taskkill', '/F', '/T', '/PID', pid],
                                   capture_output=True, timeout=5, creationflags=CF)
                except Exception:
                    pass
        except Exception:
            pass

    # ============================================================
    # Monitoring
    # ============================================================
    def _poll_once(self):
        """Single poll of gateway APIs."""
        import requests as req
        port = int(self._gateway_port.get() or 5100)
        try:
            r = req.get(f"http://127.0.0.1:{port}/api/instances", timeout=5)
            self._workers = r.json().get('instances', [])
            r2 = req.get(f"http://127.0.0.1:{port}/api/status", timeout=5)
            self._stats = r2.json()
            try:
                r3 = req.get(f"http://127.0.0.1:{port}/api/accounts", timeout=5)
                self._account_stats = r3.json().get('accounts', [])
            except Exception:
                pass
            self.after(0, self._update_monitor)
        except Exception:
            pass

    def _poll_loop(self):
        """Poll gateway + supervise processes + rotate logs. Every 60s."""
        cycle = 0
        while self._started:
            try:
                self._poll_once()
                self._supervise_processes()
                with self._proc_lock:
                    self._processes = [p for p in self._processes if p.poll() is None]
                cycle += 1
                if cycle % 10 == 0:
                    self._rotate_logs()
                # Update restart countdown
                if self._restart_at:
                    remaining = self._restart_at - time.time()
                    if remaining > 0:
                        h = int(remaining // 3600)
                        m = int((remaining % 3600) // 60)
                        self.after(0, lambda h=h, m=m: self._restart_countdown_label.config(
                            text=f"Restart sau {h}h{m:02d}m", fg=FG2))
            except Exception as e:
                self._log(f"[Supervisor] poll error: {e}", "ERROR")
            time.sleep(60)

    def _can_restart(self, key: str) -> bool:
        """Backoff: max 3 restarts per 5min per process."""
        now = time.time()
        times = self._restart_times.get(key, [])
        times = [t for t in times if now - t < 300]
        self._restart_times[key] = times
        if len(times) >= 3:
            return False
        times.append(now)
        return True

    def _supervise_processes(self):
        """Auto-restart crashed gateway/agents. Core of 24/7 reliability."""
        # Gateway
        if self._gateway_proc and self._gateway_proc.poll() is not None:
            if self._can_restart("gateway"):
                self._log("Gateway CRASHED — auto-restarting...", "ERROR")
                time.sleep(5)
                self._start_gateway(self._gateway_port_val)
            else:
                self._log("Gateway keeps crashing — pausing restarts for 5min", "ERROR")

        # Agents
        for name, proc in list(self._agent_procs.items()):
            if proc.poll() is not None:
                cfg = self._inst_configs.get(name)
                if cfg and self._can_restart(name):
                    self._log(f"[{name}] Agent CRASHED — auto-restarting...", "ERROR")
                    self._start_agent(cfg)

    def _rotate_logs(self):
        """Delete .bak files older than 24h to prevent disk accumulation."""
        try:
            cutoff = time.time() - 86400
            for f in LOG_DIR.iterdir():
                if f.suffix == '.bak' and f.stat().st_mtime < cutoff:
                    f.unlink()
        except Exception:
            pass

    def _update_monitor(self):
        """Update monitor UI from polled data."""
        # Stats
        self._stat_labels['available'].config(text=str(self._stats.get('instances_available', 0)))
        self._stat_labels['completed'].config(text=str(self._stats.get('total_completed', 0)))
        self._stat_labels['failed'].config(text=str(self._stats.get('total_failed', 0)))
        self._stat_labels['cooling'].config(text=str(self._stats.get('instances_cooling', 0)))

        # Workers — dedupe by name, always clear + rebuild
        for widget in self._workers_frame.winfo_children():
            widget.destroy()

        account_map = {}
        try:
            accounts_file = BASE_DIR / "config" / ".flow_accounts.json"
            if accounts_file.exists():
                account_map = json.loads(accounts_file.read_text(encoding="utf-8"))
        except Exception:
            pass

        # Dedupe workers by name (prevent duplicates)
        seen = set()
        workers = []
        for w in self._workers:
            name = w.get('name', '')
            if name and name not in seen:
                seen.add(name)
                workers.append(w)

        for i, w in enumerate(workers):
            name = w.get('name', '?')
            ext_connected = w.get('extension_connected', False)
            is_healthy = w.get('healthy', False)

            # Determine status
            if w.get('cooling'):
                border_color = ORANGE
                status_text = f"COOL {w.get('cooling_remaining', 0)}s"
                status_fg = ORANGE
            elif w.get('quota_exhausted'):
                border_color = ORANGE
                status_text = f"429 {w.get('quota_remaining', 0)}s"
                status_fg = ORANGE
            elif ext_connected and w.get('available'):
                border_color = GREEN
                status_text = "READY" if w.get('processing', 0) == 0 else "BUSY"
                status_fg = GREEN
            elif ext_connected and is_healthy:
                border_color = YELLOW
                status_text = "BUSY" if w.get('processing', 0) > 0 else "WAIT"
                status_fg = YELLOW
            elif is_healthy and not ext_connected:
                border_color = '#555'
                status_text = "STANDBY"
                status_fg = '#888'
            else:
                border_color = RED
                status_text = "DOWN"
                status_fg = RED

            card = tk.Frame(self._workers_frame, bg=BG, bd=1, relief='solid',
                            highlightbackground=border_color)
            card.pack(side='left', fill='both', padx=2, pady=2, expand=True)

            tk.Label(card, text=name, font=('Segoe UI', 9, 'bold'),
                     fg=FG if ext_connected else '#666', bg=BG).pack(padx=6, pady=(4, 1))
            tk.Label(card, text=status_text, font=('Segoe UI', 8, 'bold'),
                     fg=status_fg, bg=BG).pack()

            acc = account_map.get(name, {})
            email = acc.get('id', '')
            if email:
                short = email.split('@')[0][:12]
                tk.Label(card, text=short, font=('Consolas', 7),
                         fg=BLUE if ext_connected else '#555', bg=BG).pack()

            ext = "Ext: OK" if ext_connected else "Ext: --"
            ext_fg = GREEN if ext_connected else '#555'
            tk.Label(card, text=ext, font=('Consolas', 8), fg=ext_fg, bg=BG).pack()

            c403 = w.get('consecutive_403', 0)
            c403_fg = RED if c403 > 0 else FG2
            tk.Label(card, text=f"403: {c403}",
                     font=('Consolas', 8), fg=c403_fg, bg=BG).pack()
            tk.Label(card, text=f"OK:{w.get('total_completed', 0)} F:{w.get('total_failed', 0)}",
                     font=('Consolas', 8), fg=FG2, bg=BG).pack(pady=(0, 4))

        # Accounts overview
        for widget in self._accounts_frame.winfo_children():
            widget.destroy()

        if self._account_stats:
            # Use grid for aligned columns
            grid = tk.Frame(self._accounts_frame, bg=BG2)
            grid.pack(fill='x')
            cols = [("Account", 20, 'w'), ("Status", 8, 'center'), ("Worker", 8, 'center'),
                    ("OK", 6, 'center'), ("403", 6, 'center'), ("Fail", 6, 'center')]
            for c, (text, width, anchor) in enumerate(cols):
                tk.Label(grid, text=text, font=('Segoe UI', 8, 'bold'), fg=FG2, bg=BG2,
                         width=width, anchor=anchor).grid(row=0, column=c, padx=2, pady=(2, 0))
            grid.grid_columnconfigure(0, weight=1)

            for r, acc in enumerate(self._account_stats):
                email = acc.get('email', '?')
                short_email = email.split('@')[0]
                status = acc.get('status', 'pool')
                assigned = acc.get('assigned_to', '')
                ok_count = acc.get('ok', 0)
                fail_count = acc.get('fail', 0)
                err_403 = acc.get('errors_403', 0)

                row_bg = BG if r % 2 == 0 else BG2
                stat_text = "ACTIVE" if status == 'active' else "pool"
                stat_fg = GREEN if status == 'active' else FG2

                tk.Label(grid, text=short_email, font=('Consolas', 9), fg=FG, bg=row_bg,
                         anchor='w').grid(row=r+1, column=0, padx=2, sticky='ew')
                tk.Label(grid, text=stat_text, font=('Consolas', 9, 'bold'), fg=stat_fg, bg=row_bg,
                         width=8).grid(row=r+1, column=1, padx=2)
                tk.Label(grid, text=assigned.replace('flowkit-', 'fk-') if assigned else "-",
                         font=('Consolas', 9), fg=BLUE if assigned else FG2, bg=row_bg,
                         width=8).grid(row=r+1, column=2, padx=2)
                tk.Label(grid, text=str(ok_count), font=('Consolas', 9, 'bold'),
                         fg=GREEN if ok_count > 0 else FG2, bg=row_bg,
                         width=6).grid(row=r+1, column=3, padx=2)
                tk.Label(grid, text=str(err_403), font=('Consolas', 9, 'bold'),
                         fg=RED if err_403 > 0 else FG2, bg=row_bg,
                         width=6).grid(row=r+1, column=4, padx=2)
                tk.Label(grid, text=str(fail_count), font=('Consolas', 9, 'bold'),
                         fg=ORANGE if fail_count > 0 else FG2, bg=row_bg,
                         width=6).grid(row=r+1, column=5, padx=2)

    # ============================================================
    # Logging
    # ============================================================
    def _log(self, msg: str, level: str = "INFO"):
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] [{level}] {msg}"
        print(line)
        self._logs.append(line)
        if len(self._logs) > 500:
            self._logs = self._logs[-300:]

        # Only update widget when logs are visible (save CPU/RAM)
        if not getattr(self, '_log_visible', False):
            return

        def _append():
            try:
                self._log_text.config(state='normal')
                self._log_text.insert('end', line + '\n', level)
                line_count = int(self._log_text.index('end-1c').split('.')[0])
                if line_count > 500:
                    self._log_text.delete('1.0', f'{line_count - 300}.0')
                self._log_text.see('end')
                self._log_text.config(state='disabled')
            except Exception:
                pass

        try:
            self.after(0, _append)
        except Exception:
            pass

    # ============================================================
    # Update
    # ============================================================

    def _get_remote_version(self) -> str:
        """Get remote version from GitHub VERSION file (no API, no rate limit)."""
        import ssl
        from urllib.request import urlopen, Request
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/VERSION"
            req = Request(url, headers={"User-Agent": "FlowKit-Updater"})
            with urlopen(req, timeout=15, context=ctx) as resp:
                return resp.read().decode("utf-8").strip()
        except Exception as e:
            print(f"[UPDATE] error: {e}")
            return ""

    def _on_check_update(self):
        self._cancel_autostart()
        self._update_btn.config(text="Checking...", state="disabled", fg='#FFA500')
        threading.Thread(target=self._check_and_update_thread, daemon=True).start()

    def _check_and_update_thread(self):
        """Check + update in one click. No second click needed."""
        try:
            local = self._version
            remote = self._get_remote_version()
            self._remote_version = remote
            if not remote:
                self.after(0, lambda: self._update_btn.config(
                    text="Loi ket noi", state="normal", bg=RED, fg='#fff'))
                self.after(5000, lambda: self._update_btn.config(
                    text="Update", bg='#0984e3', fg='#fff'))
                return

            has_update = False
            try:
                local_n = int(local.rsplit(".", 1)[-1])
                remote_n = int(remote.rsplit(".", 1)[-1])
                has_update = remote_n > local_n
            except (ValueError, IndexError):
                has_update = remote != local

            if not has_update:
                self.after(0, lambda: self._update_btn.config(
                    text="Da moi nhat", state="normal", bg=GREEN, fg='#fff'))
                self.after(5000, lambda: self._update_btn.config(
                    text="Update", bg='#0984e3', fg='#fff'))
                return

            # Has update → run immediately (no second click)
            self.after(0, lambda: self._version_label.config(
                text=f"v{local}  >>  v{remote}", fg='#FFA500'))
            self._do_update()

        except Exception as e:
            self.after(0, lambda: self._update_btn.config(
                text="Loi", state="normal", bg=RED, fg='#fff'))
            print(f"Update error: {e}")
            self.after(5000, lambda: self._update_btn.config(
                text="Update", bg='#0984e3', fg='#fff'))

    def _do_update(self):
        """Download + apply update. Called from background thread."""
        import urllib.request
        import zipfile

        self._update_btn.config(state="disabled", text="Dang cap nhat...", bg='#666', fg='#fff')

        try:
            git_available = False
            try:
                result = subprocess.run(
                    ["git", "--version"],
                    capture_output=True, text=True, timeout=10,
                )
                git_available = (result.returncode == 0)
            except Exception:
                pass

            git_root = SUITE_ROOT if not _IS_STANDALONE else BASE_DIR
            git_ok = False
            # Standalone (VM): git checkout would populate <root>/server/flowkit/* and
            # leave the running FLAT files untouched -> use the ZIP flat-copy path instead.
            if git_available and not _IS_STANDALONE:
                git_dir = Path(git_root) / ".git"
                if not git_dir.exists():
                    self._log("Git init (lan dau)...", "INFO")
                    subprocess.run(["git", "init"], cwd=str(git_root),
                                   capture_output=True, timeout=30)
                # Ensure remote
                result = subprocess.run(
                    ["git", "remote", "get-url", "origin"],
                    cwd=str(git_root), capture_output=True, text=True, timeout=10,
                )
                if result.returncode != 0:
                    subprocess.run(
                        ["git", "remote", "add", "origin", GITHUB_GIT_URL],
                        cwd=str(git_root), capture_output=True, timeout=10,
                    )
                elif GITHUB_GIT_URL not in result.stdout.strip():
                    subprocess.run(
                        ["git", "remote", "set-url", "origin", GITHUB_GIT_URL],
                        cwd=str(git_root), capture_output=True, timeout=10,
                    )
                self._log("Git fetch...", "INFO")
                r = subprocess.run(["git", "fetch", "--depth=1", "origin", "main"],
                                   cwd=str(git_root), capture_output=True, text=True, timeout=60)
                if r.returncode == 0:
                    r2 = subprocess.run(["git", "checkout", "-f", "-B", "main", "origin/main"],
                                        cwd=str(git_root), capture_output=True, text=True, timeout=60)
                    if r2.returncode == 0:
                        git_ok = True
                        self._log("Git update OK", "OK")
                    else:
                        self._log(f"Git checkout error: {r2.stderr[:100]}", "WARN")
                else:
                    self._log(f"Git fetch error: {r.stderr[:100]}", "WARN")

            if not git_ok:
                import ssl
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

                zip_path = BASE_DIR / "update_temp.zip"
                extract_dir = BASE_DIR / "update_temp"

                cache_buster = f"?nocache={int(time.time())}"
                self._log(f"Downloading: {GITHUB_ZIP_URL}", "INFO")
                with urllib.request.urlopen(GITHUB_ZIP_URL + cache_buster, context=ssl_context) as response:
                    with open(str(zip_path), 'wb') as out_file:
                        out_file.write(response.read())

                with zipfile.ZipFile(str(zip_path), 'r') as zip_ref:
                    zip_ref.extractall(str(extract_dir))

                src_flowkit = extract_dir / "VE3_SUITE-main" / "server" / "flowkit"

                if src_flowkit.exists():
                    copied_files = []
                    for py_file in src_flowkit.glob("*.py"):
                        shutil.copy2(str(py_file), str(BASE_DIR / py_file.name))
                        copied_files.append(py_file.name)
                    self._log(f"Copied {len(copied_files)} .py files: {', '.join(sorted(copied_files)[:10])}...", "INFO")
                    for bat_file in src_flowkit.glob("*.bat"):
                        shutil.copy2(str(bat_file), str(BASE_DIR / bat_file.name))
                    for txt_file in ("VERSION.txt",):
                        src_txt = src_flowkit / txt_file
                        if src_txt.exists():
                            shutil.copy2(str(src_txt), str(BASE_DIR / txt_file))

                    src_agent = src_flowkit / "agent"
                    dst_agent = BASE_DIR / "agent"
                    if src_agent.exists():
                        dst_agent.mkdir(exist_ok=True)
                        for py_file in src_agent.rglob("*.py"):
                            rel = py_file.relative_to(src_agent)
                            dst = dst_agent / rel
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(str(py_file), str(dst))

                    src_ext = src_flowkit / "flowkit_extensions"
                    dst_ext = BASE_DIR / "flowkit_extensions"
                    if src_ext.exists():
                        for ext_dir in src_ext.iterdir():
                            if ext_dir.is_dir():
                                dst_sub = dst_ext / ext_dir.name
                                if dst_sub.exists():
                                    shutil.rmtree(str(dst_sub))
                                shutil.copytree(str(ext_dir), str(dst_sub))

                src_server_root = extract_dir / "VE3_SUITE-main" / "server"
                # NOTE: google_login.py is already copied flat from server/flowkit by the
                # glob above. Do NOT overwrite it from server/ root — that's the LEGACY
                # copy and is stale. Only pull requirements.txt as a shared fallback.
                for shared_file in ("requirements.txt",):
                    for src in (src_flowkit / shared_file, src_server_root / shared_file):
                        if src.exists():
                            shutil.copy2(str(src), str(BASE_DIR / shared_file))
                            break
                for ipv6_path in [src_flowkit / "ipv6_proxy.py",
                                  src_server_root / "modules" / "ipv6_proxy.py"]:
                    if ipv6_path.exists():
                        shutil.copy2(str(ipv6_path), str(BASE_DIR / "ipv6_proxy.py"))
                        break

                if zip_path.exists():
                    zip_path.unlink()
                if extract_dir.exists():
                    shutil.rmtree(str(extract_dir))

            remote_ver = getattr(self, '_remote_version', '') or self._get_remote_version()
            if remote_ver:
                try:
                    (BASE_DIR / "VERSION.txt").write_text(remote_ver + "\n", encoding="utf-8")
                except Exception:
                    pass
                if not _IS_STANDALONE:
                    try:
                        (SUITE_ROOT / "VERSION").write_text(remote_ver + "\n", encoding="utf-8")
                    except Exception:
                        pass

            new_ver = _get_auto_version()
            self._version = new_ver
            self.after(0, lambda: self._version_label.config(
                text=f"v{new_ver}", fg=GREEN))
            self.after(0, lambda: self._update_btn.config(
                text=f"v{new_ver} OK! Restart...", bg='#00ff88', fg='#000'))
            self.after(0, lambda: self.title(f"FlowKit Server v{new_ver}"))

            def _restart_after_update():
                if self._started:
                    self._on_stop()
                time.sleep(3)
                os.execv(sys.executable, [sys.executable] + sys.argv)

            self.after(2000, _restart_after_update)

        except Exception as e:
            self._update_btn.config(text="LOI", bg=RED, fg='#fff')
            print(f"Update error: {e}")
            from tkinter import messagebox
            messagebox.showerror("Loi cap nhat", f"Loi: {e}\n\nThu tai thu cong:\n{GITHUB_ZIP_URL}")
        finally:
            self.after(5000, lambda: self._update_btn.config(
                state="normal", text="Update", bg='#0984e3', fg='#fff',
                command=self._on_check_update))

    # ============================================================
    # Cleanup
    # ============================================================
    def _on_close(self):
        if self._started:
            self._on_stop()
        try:
            self.destroy()
        except Exception:
            pass
        os._exit(0)


def main():
    app = FlowKitGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
